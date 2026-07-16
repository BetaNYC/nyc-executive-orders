"""Deterministic, non-destructive text cleaner for the OCR'd EO corpus.

A **rule-based only** cleaning stage. It slots into the parse pipeline AFTER
``extract``/``ocr`` produce a body and BEFORE ``build_corpus`` emits the record:

    textlayer -> extract | ocr -> [clean] -> enrich -> emit

It never rewrites order text (no LLM, no paraphrase) — an LLM hallucinating a
clause into a mayoral order is the exact failure this project exists to fight,
and it would break reproducibility (engineering-standards §7). Every transform
here is a pure function of the input string plus a handful of tunable constants,
so the same input always yields the same output, and re-running on already-clean
text is a no-op (idempotent).

Governing principles (see the sample-cleaner task brief):

* **Non-destructive.** The verbatim OCR body is preserved in ``full_text_raw``;
  the cleaned body is ``full_text``. Whatever is trimmed off the top is kept in
  ``dropped_header``; isolated file-marks removed mid-body are kept in
  ``dropped_marks``. Nothing is deleted — only relocated. The source PDF (git-LFS)
  remains ground truth regardless.
* **Conservative.** When unsure whether something is noise or content, KEEP it and
  flag ``text_quality: needs-review`` rather than stripping. No confident anchor
  ==> no trim. A suspiciously large trim ==> no trim, flag instead.

Five passes (all in :func:`clean_record`):

1. **Anchor detection** — fuzzy, OCR-tolerant, case-insensitive. Find the earliest
   line matching one of the header anchors ("OFFICE OF THE MAYOR" /
   "THE CITY OF NEW YORK" / "EXECUTIVE ORDER [NO.]"). Everything before it becomes
   ``dropped_header``. No anchor found ==> keep everything, flag needs-review.
2. **File-mark stripping** — remove isolated short non-prose marks on their own
   line (e.g. ``nl-8``, ``37-12``, ``ps 7-&``); each removal is logged in
   ``dropped_marks``. Legal list/section markers (``2.``, ``(a)``, ``§4-a``) are
   left intact.
3. **Title + date extraction** — pull the ALL-CAPS subject line into ``title`` and
   the signing date into ``date_signed`` (ISO 8601), but ONLY when the frontmatter
   field is empty/null, and only when found confidently (year cross-checked against
   the record's known ``year``; the day is never fabricated). Otherwise leave the
   field as-is and flag.
4. **Quality tiering** — stamp ``text_quality`` ∈ {clean, minor-noise,
   needs-review} from measurable signals.
5. **Report** — :func:`clean_record` returns a :class:`CleanResult` carrying every
   field, metric, and flag so a diff/report emitter (or a test) can inspect the
   full before/after without re-deriving anything.

No network, no external binary, no LLM. Pure local string work.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from . import lexicon

logger = logging.getLogger("nyc_executive_orders.clean")

# --------------------------------------------------------------------------- #
# Tunable constants (every threshold is here, named, and documented).          #
# --------------------------------------------------------------------------- #

# Fuzzy-match ratio (difflib SequenceMatcher, 0..1) for a multi-word anchor
# phrase window. OCR mangles a few chars per phrase, so this sits below 1 but
# high enough that unrelated noise lines do not match. Tuned on the worst-noise
# sample (e.g. "EXEcurryE ORDER NO", "operce oF THE MAYOR").
ANCHOR_PHRASE_RATIO = 0.72

# The single distinctive token "EXECUTIVE" is a strong header signal on its own;
# require a high ratio so body words never trip it. Catches "RXECUTIVE" (the real
# header of 1976-EO-064, mangled at char 0) at ratio ~0.89.
ANCHOR_TOKEN_RATIO = 0.80

# Safety cap: if anchoring would trim MORE than this many chars, we assume the
# fuzzy match landed on a later (body) anchor rather than the real header — do NOT
# trim, flag needs-review instead. The worst real leading noise measured on the
# corpus is a few hundred chars; a >900-char "header" is almost certainly real
# body text after a header whose anchor OCR destroyed.
MAX_HEADER_TRIM_CHARS = 900

# A file-mark line, after stripping, is at most this long.
FILE_MARK_MAX_LEN = 8

# Quality-tier thresholds, computed on the CLEANED body.
CLEAN_MAX_LEADING_NOISE = 20      # <= this many trimmed chars still counts "clean"
CLEAN_MIN_WORD_RATIO = 0.90       # english-likeness floor for "clean"
CLEAN_MAX_JUNK_RATIO = 0.05       # junk-char ceiling for "clean"
REVIEW_MIN_WORD_RATIO = 0.70      # below this => needs-review
REVIEW_MAX_JUNK_RATIO = 0.15      # above this => needs-review

# Title gate: a candidate title is auto-accepted ONLY if it has at least this many
# meaningful (>=2-char alpha) tokens AND EVERY one of them is a recognized word
# (see :mod:`lexicon`). One unrecognized token (e.g. the OCR mangle "Cray" for
# "City") holds the whole title as `title-uncertain` for human review — the safe
# direction, since a false hold just routes a good title to a person.
TITLE_MIN_MEANINGFUL_TOKENS = 2
# A title line/furniture line has at most this many tokens; longer caps lines that
# happen to contain an anchor phrase (e.g. "...OF THE MAYOR'S MIDTOWN...") are real
# titles, not letterhead, and must NOT be skipped during title extraction.
FURNITURE_MAX_TOKENS = 6

# How many post-anchor lines to scan for the date (keeps us in the header region,
# away from body-referenced dates like "...Order No. 98, dated March 12, 2020").
DATE_SCAN_LINES = 12

TEXT_QUALITY_CLEAN = "clean"
TEXT_QUALITY_MINOR = "minor-noise"
TEXT_QUALITY_REVIEW = "needs-review"

# --------------------------------------------------------------------------- #
# Anchors                                                                       #
# --------------------------------------------------------------------------- #

# Header anchors as normalized token tuples. Order is priority for labelling only;
# detection always takes the EARLIEST matching line regardless of which anchor.
_ANCHORS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("executive-order", ("EXECUTIVE", "ORDER")),
    ("office-of-the-mayor", ("OFFICE", "OF", "THE", "MAYOR")),
    ("city-of-new-york", ("THE", "CITY", "OF", "NEW", "YORK")),
)

# Body-starter phrases: once one of these appears, the header/title region is over.
# Matched as a normalized-prefix test on a line, so title extraction never swallows
# the opening clause of the order body.
_BODY_STARTERS: tuple[tuple[str, ...], ...] = (
    ("WHEREAS",),
    ("BY", "VIRTUE"),
    ("BY", "THE", "POWER"),
    ("PURSUANT",),
    ("NOW", "THEREFORE"),
    ("SECTION",),
    ("IT", "IS", "HEREBY"),
    ("IN", "THE", "EXERCISE"),
    ("BY", "THE", "AUTHORITY"),
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
# Full month names only (for the "is this line really a date line?" title guard —
# "may" as a common word would over-trigger, so abbreviations are excluded here).
_MONTH_WORDS = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
})
# Tokens that make a caps line an EO-number fragment (e.g. a one-word-per-line
# "ORDER / No. 10" layout), to skip during title extraction rather than collect.
_EO_NUMBER_TOKENS = frozenset({"EXECUTIVE", "EMERGENCY", "ORDER", "ORDERS", "NO", "NOS"})
# Trailing function words that signal a TRUNCATED or sentence-like caps line, not a
# real subject title — a title ending in one of these is held for review.
_TITLE_TRAILING_STOP = frozenset({
    "which", "with", "and", "the", "to", "of", "for", "in", "on", "by", "as",
    "that", "from", "a", "an", "or", "at", "into", "under", "upon",
})

# Month-name date: <Month> <day> [,;] <4-digit-year>. Separators tolerate OCR
# commas/semicolons/spaces. The day is a REAL 1-2 digit number or it does not
# match (we never fabricate a day from mangled OCR like "a5").
# Separators between the month, day, and year tolerate the stray commas, periods,
# semicolons, and OCR quote glyphs (' " “ ” ’) that scanning sprinkles in — e.g.
# `’ AUGUST 24, “1977`. They never relax the numeric shape: a real 1-2 digit day
# and 4-digit year are still required, so nothing is fabricated.
_DATE_SEP = r"[ .,;'\"“”‘’]+"
_MONTH_DATE_RE = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b"
    + _DATE_SEP + r"(\d{1,2})" + _DATE_SEP + r"(\d{4})\b",
    re.IGNORECASE,
)
# Numeric date: M/D/YY or M/D/YYYY (also tolerant of '-' separators).
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})\b")

# Isolated file-mark shapes (whole line, after stripping surrounding brackets):
#   digit-run hyphen digit-run           -> "37-12"
#   1-3 letters, sep, digits, opt tail    -> "nl-8", "ps 7-&"
_FILE_MARK_RES = (
    re.compile(r"^\d{1,3}-\d{1,3}$"),
    re.compile(r"^[a-z]{1,3}[ \-]\d{1,3}[ \-]?[&\w]{0,2}$", re.IGNORECASE),
)
# Legal list/section markers we must NEVER treat as file-marks.
_LIST_MARKER_RES = (
    re.compile(r"^\(?[a-z0-9]{1,3}\)?[.)]?$", re.IGNORECASE),  # "2.", "(a)", "10)"
    re.compile(r"^[§].*"),                                # "§4-a", "§2."
)

_ALPHA_RE = re.compile(r"[^A-Za-z ]+")
_VOWELS = set("aeiou")

# US ZIP or "N. Y." address furniture (the letterhead address line under the city
# name), which must be skipped — never mistaken for a caps subject line.
_ADDRESS_RE = re.compile(r"\b\d{5}\b|N\.?\s*Y\.?", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Result type                                                                   #
# --------------------------------------------------------------------------- #

@dataclass
class CleanResult:
    """Full before/after of cleaning one order. Nothing is discarded."""

    full_text: str                    # cleaned body
    full_text_raw: str                # verbatim input body
    dropped_header: str               # trimmed leading noise ('' if none)
    dropped_marks: list[str] = field(default_factory=list)
    title: str | None = None          # resolved title (pre-existing or extracted)
    date_signed: str | None = None    # resolved ISO date (pre-existing or extracted)
    text_quality: str = TEXT_QUALITY_REVIEW
    anchor_found: bool = False
    anchor_label: str | None = None
    title_extracted: bool = False     # True only if THIS pass filled it
    date_extracted: bool = False      # True only if THIS pass filled it
    metrics: dict = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Normalization helpers                                                          #
# --------------------------------------------------------------------------- #

def _norm_tokens(line: str) -> list[str]:
    """Uppercase alpha-only tokens of a line (digits/punct become separators)."""
    return _ALPHA_RE.sub(" ", line).upper().split()


def _phrase_ratio(line_tokens: list[str], anchor: tuple[str, ...]) -> float:
    """Best sliding-window fuzzy ratio of ``anchor`` within ``line_tokens``."""
    k = len(anchor)
    if len(line_tokens) < k:
        if not line_tokens:
            return 0.0
        # Whole short line vs the anchor (handles OCR merging the phrase).
        return SequenceMatcher(None, "".join(line_tokens), "".join(anchor)).ratio()
    target = "".join(anchor)
    best = 0.0
    for i in range(len(line_tokens) - k + 1):
        window = "".join(line_tokens[i:i + k])
        best = max(best, SequenceMatcher(None, window, target).ratio())
    return best


def _line_anchor_label(line: str) -> str | None:
    """Return the anchor label this line matches, or None. Fuzzy + OCR-tolerant."""
    tokens = _norm_tokens(line)
    if not tokens:
        return None
    # Strong single-token signal: a token ~ "EXECUTIVE".
    for tok in tokens:
        if len(tok) >= 7 and SequenceMatcher(None, tok, "EXECUTIVE").ratio() >= ANCHOR_TOKEN_RATIO:
            return "executive-order"
    # Phrase-window signals.
    for label, anchor in _ANCHORS:
        if _phrase_ratio(tokens, anchor) >= ANCHOR_PHRASE_RATIO:
            return label
    return None


def _is_body_starter(line: str) -> bool:
    tokens = _norm_tokens(line)
    for starter in _BODY_STARTERS:
        if tuple(tokens[:len(starter)]) == starter:
            return True
    return False


def _is_header_furniture(line: str) -> bool:
    """True if a line is header furniture to skip during title extraction.

    Furniture = a SHORT line dominated by a header anchor (the EO-number line, or
    the "OFFICE OF THE MAYOR" / "THE CITY OF NEW YORK" letterhead), or the address/
    ZIP line under the city name. The length bound is what distinguishes a real
    caps title that merely *contains* an anchor word (e.g. "ESTABLISHMENT OF THE
    MAYOR'S MIDTOWN ACTION OFFICE", 8 tokens) from actual letterhead ("OFFICE OF
    THE MAYOR", 4 tokens) — the former must be kept as a title, not skipped.
    """
    if _ADDRESS_RE.search(line):
        return True
    tokens = _norm_tokens(line)
    if not tokens:
        return False
    # EO-number fragment: every alpha token is EO-number furniture ("ORDER", "No",
    # "EXECUTIVE"...). Catches one-word-per-line OCR layouts that split the header.
    if all(t in _EO_NUMBER_TOKENS for t in tokens):
        return True
    return len(tokens) <= FURNITURE_MAX_TOKENS and _line_anchor_label(line) is not None


def _line_is_dateish(line: str) -> bool:
    """True if a line carries a full month name (a date line, not a title line).

    Broader than :data:`_MONTH_DATE_RE`, which needs a numeric day — this also
    catches ``DATED JANUARY I, 1974`` (Roman-numeral day) and a bare ``APRIL`` line
    from a split header, so neither is mistaken for a subject title.
    """
    return any(tok.lower() in _MONTH_WORDS for tok in _norm_tokens(line))


def _title_is_recognized(title: str) -> bool:
    """True if the title is trustworthy: enough meaningful tokens, all recognized.

    Meaningful token = a >=2-char alpha token (single letters, e.g. the ``S`` from
    ``MAYOR'S``, are ignored). A title with fewer than
    :data:`TITLE_MIN_MEANINGFUL_TOKENS` such tokens is not trusted (rejects
    single-word OCR noise like ``MARY``). Every meaningful token must clear
    :func:`lexicon.recognize`.
    """
    toks = [w for w in re.findall(r"[A-Za-z]+", title) if len(w) >= 2]
    if len(toks) < TITLE_MIN_MEANINGFUL_TOKENS:
        return False
    # A caps line ending in a dangling function word ("...CONTRACT WITH",
    # "...PRINCIPLES WHICH") is a truncated line or a sentence, not a subject title.
    if toks[-1].lower() in _TITLE_TRAILING_STOP:
        return False
    return all(lexicon.recognize(w) for w in toks)


def _english_like(token: str) -> bool:
    """Cheap, dict-free english-likeness test for one lowercased alpha token."""
    t = token.lower()
    if not t.isalpha():
        return False
    if len(t) == 1:
        return t in ("a", "i")
    if len(t) > 20:
        return False
    if not any(c in _VOWELS for c in t):
        return False
    # No run of 5+ consonants (real English words basically never have this).
    run = 0
    for c in t:
        if c in _VOWELS:
            run = 0
        else:
            run += 1
            if run >= 5:
                return False
    return True


def _word_ratio(text: str) -> float:
    """Fraction of >=2-char alpha tokens that look English. 1.0 if none present."""
    toks = [w for w in re.findall(r"[A-Za-z]+", text) if len(w) >= 2]
    if not toks:
        return 1.0
    return sum(_english_like(w) for w in toks) / len(toks)


def _junk_ratio(text: str) -> float:
    """Fraction of chars that are neither alnum, space, nor common punctuation."""
    if not text:
        return 0.0
    allowed = set(" \t\n.,;:'\"()-&/$%§#’“”")
    junk = sum(1 for c in text if not (c.isalnum() or c in allowed))
    return junk / len(text)


# --------------------------------------------------------------------------- #
# Pass 2 — file-mark stripping                                                  #
# --------------------------------------------------------------------------- #

def _is_file_mark(line: str) -> bool:
    """True if a whole line is an isolated non-prose file-mark (not a list marker)."""
    s = line.strip().strip("[]()").strip()
    if not s or len(s) > FILE_MARK_MAX_LEN:
        return False
    if not any(c.isdigit() for c in s):
        return False
    if any(rx.match(s) for rx in _LIST_MARKER_RES):
        return False
    return any(rx.match(s) for rx in _FILE_MARK_RES)


def _strip_file_marks(body: str) -> tuple[str, list[str]]:
    kept: list[str] = []
    dropped: list[str] = []
    for line in body.split("\n"):
        if _is_file_mark(line):
            dropped.append(line.strip())
            logger.info("clean: dropped file-mark %r", line.strip())
        else:
            kept.append(line)
    return "\n".join(kept), dropped


# --------------------------------------------------------------------------- #
# Pass 3 — title + date                                                         #
# --------------------------------------------------------------------------- #

def _extract_title(lines: list[str]) -> tuple[str | None, str | None]:
    """Find the ALL-CAPS subject line(s) after the header.

    Returns ``(accepted_title, raw_candidate)``:

    * ``accepted_title`` — the title IF it clears the strict confidence gate
      (:data:`TITLE_MIN_STRONG_RATIO`); else ``None``.
    * ``raw_candidate`` — the caps text that was found regardless of confidence, so
      the caller can surface a mangled-but-present title as ``title-uncertain`` for
      a human. ``None`` if no caps subject line was found at all.

    Header furniture (EO-number line, letterhead, address/ZIP) and the date line are
    skipped; a body-starter ends the search.
    """
    collected: list[str] = []
    for line in lines:
        s = line.strip()
        if not s:
            if collected:      # blank line ends a started title block
                break
            continue
        if _is_body_starter(s):
            break
        if (_is_header_furniture(s) or _line_is_dateish(s)
                or _NUMERIC_DATE_RE.search(s)):
            if collected:
                break
            continue
        alpha = [c for c in s if c.isalpha()]
        if len(alpha) < 4:
            if collected:
                break
            continue
        upper_ratio = sum(c.isupper() for c in alpha) / len(alpha)
        if upper_ratio >= 0.80:
            collected.append(s)
        elif collected:
            break
        else:
            # A non-caps, non-furniture prose line before any title => no title.
            break
    if not collected:
        return None, None
    # Strip leading/trailing non-alphanumeric OCR crud (stray "*", "|", dashes,
    # brackets) while preserving internal punctuation.
    candidate = re.sub(r"^[^0-9A-Za-z]+|[^0-9A-Za-z]+$", "", " ".join(collected))
    if not candidate:
        return None, None
    if _title_is_recognized(candidate):
        return candidate, candidate
    return None, candidate


def _extract_date(lines: list[str], expected_year: int) -> str | None:
    """First confidently-parseable date whose year matches ``expected_year``."""
    for line in lines[:DATE_SCAN_LINES]:
        m = _MONTH_DATE_RE.search(line)
        if m:
            month = _MONTHS[m.group(1).lower()]
            day, year = int(m.group(2)), int(m.group(3))
            iso = _validate_date(year, month, day, expected_year)
            if iso:
                return iso
        m = _NUMERIC_DATE_RE.search(line)
        if m:
            month, day, yr = int(m.group(1)), int(m.group(2)), m.group(3)
            year = int(yr) if len(yr) == 4 else (expected_year // 100) * 100 + int(yr)
            iso = _validate_date(year, month, day, expected_year)
            if iso:
                return iso
    return None


def _validate_date(year: int, month: int, day: int, expected_year: int) -> str | None:
    """Return ISO 8601 date if valid AND the year matches the record; else None."""
    if year != expected_year:
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


# --------------------------------------------------------------------------- #
# Public entry                                                                  #
# --------------------------------------------------------------------------- #

def clean_record(
    body: str,
    *,
    year: int,
    existing_title: str | None = None,
    existing_date_signed: str | None = None,
    text_source: str | None = None,
) -> CleanResult:
    """Run all five passes on one order body. Pure; deterministic; idempotent.

    ``body`` is the extracted/OCR'd full text. ``year`` is the record's known
    signing year (from ``eo_id``) — used to cross-check any extracted date.
    ``existing_title`` / ``existing_date_signed`` are the current frontmatter
    values; a non-empty existing value is NEVER overwritten. ``text_source`` (e.g.
    ``born-digital`` / ``ocr``) only informs tiering.
    """
    raw = body
    flags: list[str] = []

    # --- Pass 1: anchor detection ------------------------------------------- #
    lines = raw.split("\n")
    anchor_idx: int | None = None
    anchor_label: str | None = None
    for i, line in enumerate(lines):
        label = _line_anchor_label(line)
        if label is not None:
            anchor_idx, anchor_label = i, label
            break
    # First line that opens the order body ("WHEREAS", "BY VIRTUE", "BY THE POWER",
    # "NOW THEREFORE", ...). If real body begins ABOVE the earliest anchor, that
    # anchor is a body reference (e.g. "...Administrative Code of The City of New
    # York..."), not the header — trimming to it would delete real content.
    first_body_idx: int | None = None
    for i, line in enumerate(lines):
        if _is_body_starter(line):
            first_body_idx = i
            break

    dropped_header = ""
    if anchor_idx is None:
        flags.append("no-anchor-found: header not trimmed (conservative keep)")
        working = raw
    elif first_body_idx is not None and first_body_idx < anchor_idx:
        # Mis-anchored on a body reference; the order body precedes the anchor.
        flags.append(
            "anchor-after-body-start: earliest anchor sits below the order's "
            "opening clause; no clean header anchor found; not trimmed "
            "(conservative keep)"
        )
        anchor_label = None
        anchor_idx = None
        working = raw
    else:
        header_text = "\n".join(lines[:anchor_idx])
        if len(header_text) > MAX_HEADER_TRIM_CHARS:
            # Fuzzy match likely landed on a body reference, not the header.
            flags.append(
                f"large-header-trim-skipped: {len(header_text)} chars > "
                f"{MAX_HEADER_TRIM_CHARS} cap; not trimmed (conservative keep)"
            )
            anchor_label = None
            anchor_idx = None
            working = raw
        else:
            dropped_header = header_text.strip()
            working = "\n".join(lines[anchor_idx:])

    # --- Pass 2: file-mark stripping ---------------------------------------- #
    working, dropped_marks = _strip_file_marks(working)

    # --- normalize whitespace (shared cleaning; paragraph-preserving) ------- #
    cleaned = _normalize_ws(working)

    # --- Pass 3: title + date ----------------------------------------------- #
    body_lines = cleaned.split("\n")
    title = existing_title if (existing_title or "").strip() else None
    title_extracted = False
    if title is None:
        accepted, candidate = _extract_title(body_lines)
        if accepted:
            title, title_extracted = accepted, True
        elif candidate:
            # A caps subject line was found but is too OCR-mangled to trust into
            # frontmatter — surface it for a human, do NOT auto-insert it.
            flags.append(f"title-uncertain: {candidate!r}")
        else:
            flags.append("title-not-extracted")

    date_signed = existing_date_signed if existing_date_signed else None
    date_extracted = False
    if date_signed is None:
        cand_d = _extract_date(body_lines, year)
        if cand_d:
            date_signed, date_extracted = cand_d, True
        else:
            flags.append("date-not-extracted")

    # --- Pass 4: quality tiering -------------------------------------------- #
    word_ratio = _word_ratio(cleaned)
    junk_ratio = _junk_ratio(cleaned)
    leading_noise = len(dropped_header)
    anchor_found = anchor_label is not None

    metrics = {
        "leading_noise_chars": leading_noise,
        "english_word_ratio": round(word_ratio, 4),
        "junk_char_ratio": round(junk_ratio, 4),
        "anchor_found": anchor_found,
        "title_resolved": title is not None,
        "date_resolved": date_signed is not None,
        "dropped_mark_count": len(dropped_marks),
        "raw_chars": len(raw),
        "clean_chars": len(cleaned),
        "lexicon_source": lexicon.english_lexicon_source(),
    }
    # Any of these flags means a human should look before the field is trusted.
    review_flag = any(
        f.startswith(("large-header-trim-skipped", "anchor-after-body-start",
                      "title-uncertain"))
        for f in flags
    )
    text_quality = _tier(
        text_source=text_source,
        anchor_found=anchor_found,
        leading_noise=leading_noise,
        word_ratio=word_ratio,
        junk_ratio=junk_ratio,
        review_flag=review_flag,
    )

    return CleanResult(
        full_text=cleaned,
        full_text_raw=raw,
        dropped_header=dropped_header,
        dropped_marks=dropped_marks,
        title=title,
        date_signed=date_signed,
        text_quality=text_quality,
        anchor_found=anchor_found,
        anchor_label=anchor_label,
        title_extracted=title_extracted,
        date_extracted=date_extracted,
        metrics=metrics,
        flags=flags,
    )


def _normalize_ws(text: str) -> str:
    """Collapse intra-line spaces, strip trailing spaces, cap blank runs at one."""
    text = "\n".join(re.sub(r"[ \t]+", " ", ln).rstrip() for ln in text.split("\n"))
    text = re.sub(r"\n[ \t]*\n[ \t]*(?:\n[ \t]*)+", "\n\n", text)
    return text.strip()


def _tier(
    *,
    text_source: str | None,
    anchor_found: bool,
    leading_noise: int,
    word_ratio: float,
    junk_ratio: float,
    review_flag: bool,
) -> str:
    born_digital = text_source == "born-digital"
    # needs-review: strongest signals of trouble.
    if review_flag:
        return TEXT_QUALITY_REVIEW
    if not anchor_found and not born_digital:
        return TEXT_QUALITY_REVIEW
    if word_ratio < REVIEW_MIN_WORD_RATIO or junk_ratio > REVIEW_MAX_JUNK_RATIO:
        return TEXT_QUALITY_REVIEW
    # clean: born-digital, or a well-anchored low-noise high-word-ratio doc.
    if (
        (born_digital or anchor_found)
        and leading_noise <= CLEAN_MAX_LEADING_NOISE
        and word_ratio >= CLEAN_MIN_WORD_RATIO
        and junk_ratio <= CLEAN_MAX_JUNK_RATIO
    ):
        return TEXT_QUALITY_CLEAN
    return TEXT_QUALITY_MINOR
