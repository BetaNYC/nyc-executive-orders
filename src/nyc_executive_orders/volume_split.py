"""Split a bound pre-1974 volume's page-level OCR into per-instrument documents.

Phase E, stage 2. Consumes the page records :mod:`vlm_ocr` wrote
(``json/page_XXXX.json``) and returns one :class:`SplitDocument` per executive
order / memorandum, ready for :mod:`build_pre1974` to clean, enrich and emit.

**Rule-based only, no model, no network.** The VLM's job ended at
pixels -> (text, layout category); every decision here — where one instrument
ends and the next begins, what its number is, what date it carries — is made by
deterministic code reading literal strings the scan contains. That keeps the
project's one hard rule intact: *never invent or guess at content; an empty or
flagged field is correct, a guess is a bug.*

The layout these volumes actually have (measured on the proven 126-page
Impellitteri-Sharkey run, not assumed):

* **Only rectos carry print.** Every printed page is followed by a blank verso,
  which the blank/bleed-through classifier already skips. So documents are
  separated by blank pages far more often than they are by anything subtle.
* **Letterhead opens a document.** "CITY OF NEW YORK / OFFICE OF THE MAYOR /
  NEW YORK 7, N.Y." — sometimes three elements, sometimes one element with
  embedded newlines, usually preceded by a ``Picture`` (the city seal).
* **A page-number ornament opens a continuation.** ``-2-``, ``- 4 -``, ``-5-``,
  ``II``, ``PAGE 2`` as the leading ``Page-header``. This is the signal that a
  page belongs to the instrument before it rather than starting a new one.
* **The instrument label is inconsistent within one numbering sequence.** The
  Impellitteri-Sharkey volume's 39 instruments are labelled variously
  "Memorandum No. 1", "MEMORANDUM\\nEXECUTIVE ORDER NO. 14", "EXECUTIVE
  MEMORANDUM #30" and "EXECUTIVE MEMORANDUM NO. 31" — one continuous 1..39
  sequence, three different printed names. See :func:`resolve_series`.
* **Each volume opens with its own subject index**, emitted by the model as a
  one-line HTML ``<table>`` of SUBJECT | NUMBER | DATE. It is alphabetical by
  subject, so one instrument appears under several subjects and numbers repeat;
  the distinct NUMBER set is the volume's ground truth for reconciliation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from . import clean
from .vlm_pages import (  # noqa: F401 - re-exported, see "Text rendering"
    MIN_COVERED_FRACTION,
    assemble_body,
    element_text,
    page_blocks,
    page_flags,
    page_lines,
)
from .identity import (
    SERIES_ADMINISTRATIVE_MEMORANDUM,
    SERIES_EXECUTIVE_MEMORANDUM,
    SERIES_EXECUTIVE_ORDER,
)

logger = logging.getLogger("nyc_executive_orders.volume_split")

# --------------------------------------------------------------------------- #
# Tunables (named and documented, per clean.py's house style)                   #
# --------------------------------------------------------------------------- #

# How many leading lines of a page count as its "head" for start/continuation
# detection. The letterhead, date, and instrument label all sit within the first
# few elements; scanning deeper starts matching body references to other orders.
HEAD_LINES = 14

# A letterhead line must match one of these anchor phrases at least this well.
# Reuses clean.py's OCR-tolerant fuzzy matcher at its tuned ratio.
LETTERHEAD_RATIO = clean.ANCHOR_PHRASE_RATIO

# Minimum distinct letterhead anchors that must hit before a page counts as
# opening a document on letterhead alone. Two ("CITY OF NEW YORK" plus "OFFICE
# OF THE MAYOR") keeps a body reference to "the City of New York" from starting
# a spurious document.
MIN_LETTERHEAD_ANCHORS = 2

# ...but two anchors are only meaningful if each is a LINE, not a phrase found
# somewhere inside one. clean._phrase_ratio slides a window across every token
# of the line it is given, so a 150-token body paragraph reading "...granted in
# Section 124 (a) of the New York City Charter are hereby delegated to the
# Office of the Mayor..." matches BOTH anchors on its own and the page opens a
# document in the middle of an order. Real letterhead is printed on its own
# short line; this caps how long a line may be and still count as one. Twelve
# tokens leaves room for the whole block folded into a single line ("CITY OF
# NEW YORK OFFICE OF THE MAYOR NEW YORK 7 N Y"), which the model does emit.
LETTERHEAD_MAX_LINE_TOKENS = 12

_LETTERHEAD_ANCHORS: tuple[tuple[str, ...], ...] = (
    ("CITY", "OF", "NEW", "YORK"),
    ("OFFICE", "OF", "THE", "MAYOR"),
)

# Instrument labels, MOST SPECIFIC FIRST — order is load-bearing. Page 48 of the
# Impellitteri-Sharkey volume reads "MEMORANDUM\nEXECUTIVE ORDER NO. 14"; a bare
# "MEMORANDUM" pattern tried first would label it a memorandum and lose the fact
# that the city called it an executive order.
#
# The number carries an optional letter suffix, which may be hyphenated or
# spaced on the page: the Impellitteri-Sharkey volume prints "MEMORANDUM NO. 7-A"
# and indexes it as "7A". A digits-only pattern reads that as plain "7" and
# collides with the real 7 — silently merging two distinct instruments — so the
# suffix is captured here and normalized by :func:`normalize_number`.
_NUM = r"(\d+\s*-?\s*[A-Za-z]?)"
_SEP = r"\s*(?:NO\.?|NUMBER|#)?\s*"
_INSTRUMENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (SERIES_EXECUTIVE_ORDER,
     re.compile(rf"\bEXECUTIVE\s+ORDER{_SEP}{_NUM}\b", re.IGNORECASE)),
    (SERIES_ADMINISTRATIVE_MEMORANDUM,
     re.compile(rf"\bADMINISTRATIVE\s+MEMORAND(?:UM|A){_SEP}{_NUM}\b", re.IGNORECASE)),
    (SERIES_EXECUTIVE_MEMORANDUM,
     re.compile(rf"\bEXECUTIVE\s+MEMORAND(?:UM|A){_SEP}{_NUM}\b", re.IGNORECASE)),
    (SERIES_EXECUTIVE_MEMORANDUM,
     re.compile(rf"\bMEMORAND(?:UM|A){_SEP}{_NUM}\b", re.IGNORECASE)),
)

# An unnumbered instrument still announces itself with a bare label.
_BARE_LABEL_RE = re.compile(
    r"\b(EXECUTIVE\s+ORDER|ADMINISTRATIVE\s+MEMORAND(?:UM|A)"
    r"|EXECUTIVE\s+MEMORAND(?:UM|A))\b",
    re.IGNORECASE,
)

# A bare label opens a document only when the printed line is essentially JUST
# the label — "EXECUTIVE MEMORANDA", "## EXECUTIVE ORDER". These instruments
# refer to themselves constantly in their own body ("This Executive Order shall
# take effect immediately.", "...arising under this Executive Order."), and an
# unanchored bare-label match turns every such sentence into a spurious new
# document: it is what split Lindsay's four-page Executive Order No. 84
# (pages 107-110) into a real record and a phantom one, because page 108's body
# says "...vested in the Mayor's Office for Veteran Action by this Executive
# Order." The numbered patterns above already require the match to start the
# line; this is the same rule for the fallback, plus a cap on what may trail it
# so a title line ("EXECUTIVE ORDER - LABOR RELATIONS") still counts and a
# sentence opening with the label does not.
BARE_LABEL_MAX_TRAILING_CHARS = 32

# Leading page-number ornaments that mark a CONTINUATION page. Covers the five
# shapes observed: "-2-", "- 4 -", "PAGE 2", "-page 2-", and bare roman numerals
# ("II").
_CONTINUATION_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"^-\s*\d{1,3}\s*-$"),
    re.compile(r"^\(?\s*\d{1,3}\s*\)?$"),
    re.compile(r"^PAGE\s+\d{1,3}$", re.IGNORECASE),
    re.compile(r"^-\s*PAGE\s+\d{1,3}\s*-$", re.IGNORECASE),
    re.compile(r"^-?\s*[IVXLC]{1,6}\s*-?$"),
)

# Lead-in phrases that mean a page is talking ABOUT another instrument rather
# than opening one of its own — "Re: Memorandum #2 ... (attached)", "In answer
# to your Memorandum No.20", "In response to the Mayor's Memorandum Number 49".
# A label match on one of these lines must not mint a document under that
# number; see :func:`find_instrument_label`.
_REFERENTIAL_MARKERS: tuple[str, ...] = (
    "re:", "in answer to", "in response to", "in reply to", "with reference to",
)


def _is_referential_line(line: str) -> bool:
    norm = line.strip().lower()
    return any(norm.startswith(m) or f" {m}" in norm for m in _REFERENTIAL_MARKERS)

# Library-stamp / binding furniture that is not part of any instrument. Matched
# against a normalized (uppercase, alpha-only) line.
_LIBRARY_FURNITURE = (
    "MUNICIPAL REFERENCE LIBRARY",
    "REFERENCE ONLY",
    "MUNICIPAL BUILDING",
    "NEW YORK CITY",
    "RECEIVED",
)

# A page whose head matches this is the volume's own subject index.
_INDEX_TITLE_RE = re.compile(r"\bINDEX\s+TO\s+THE\b", re.IGNORECASE)
# ...and the index title also names the series the volume numbers (see
# resolve_series): "INDEX TO THE / EXECUTIVE ORDERS OF THE MAYOR / 1950-1953".
_INDEX_SERIES_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (SERIES_EXECUTIVE_ORDER, re.compile(r"\bEXECUTIVE\s+ORDERS?\b", re.IGNORECASE)),
    (SERIES_ADMINISTRATIVE_MEMORANDUM,
     re.compile(r"\bADMINISTRATIVE\s+MEMORAND(?:UM|A)\b", re.IGNORECASE)),
    (SERIES_EXECUTIVE_MEMORANDUM,
     re.compile(r"\bEXECUTIVE\s+MEMORAND(?:UM|A)\b", re.IGNORECASE)),
)

_NUMBER_NORM_RE = re.compile(r"^(\d+)\s*-?\s*([A-Za-z]?)$")


def normalize_number(raw: str | None) -> str | None:
    """Canonicalize a printed instrument number: '7-A' / '7 a' / '007A' -> '7A'.

    One instrument is printed one way on its own face and another in the index
    ("NO. 7-A" vs "7A"). Both must reduce to the same key or reconciliation
    compares two spellings of the same number and reports a phantom gap.
    Leading zeros are dropped here; :func:`identity.mint_pre1974_id` re-pads for
    the id, so sorting still works.
    """
    if raw is None:
        return None
    m = _NUMBER_NORM_RE.match(raw.strip())
    if not m:
        return raw.strip() or None
    return f"{int(m.group(1))}{m.group(2).upper()}"


_TABLE_ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TABLE_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_NUMBER_CELL_RE = re.compile(r"^\d+\s*-?\s*[A-Za-z]?$")

# Page roles.
ROLE_BLANK = "blank"
ROLE_INDEX = "index"
ROLE_FURNITURE = "furniture"     # library stamps, binding, endpapers
ROLE_START = "start"             # opens an instrument
ROLE_CONTINUATION = "continuation"
ROLE_FAILED = "failed"           # OCR produced nothing usable (see page_flags)


# --------------------------------------------------------------------------- #
# Result types                                                                  #
# --------------------------------------------------------------------------- #

@dataclass
class IndexEntry:
    """One instrument as the volume's own subject index records it."""

    number: str
    date: str | None = None          # ISO 8601, from the index's M/D/YY cell
    subjects: list[str] = field(default_factory=list)


@dataclass
class PageInfo:
    """A page record reduced to what segmentation needs, plus its QA flags."""

    page: int
    role: str
    lines: list[str] = field(default_factory=list)
    # The page's layout elements, unflattened — what the document body is built
    # from. ``lines`` is the same content with the block boundaries gone, and is
    # what classification counts from the top of the page.
    blocks: list[str] = field(default_factory=list)
    series: str | None = None        # from the instrument label, if any
    number: str | None = None
    printed_label: str | None = None  # the label VERBATIM as printed
    flags: list[str] = field(default_factory=list)


@dataclass
class SplitDocument:
    """One instrument, assembled from one or more pages."""

    series: str
    number: str | None
    pages: list[int]
    body: str
    printed_label: str | None = None
    subject: str | None = None       # from a SUBJECT:/RE: line, if stated
    date_on_page: str | None = None
    date_source: str | None = None   # "page" | "index" | None
    number_source: str = "page"      # "page" | "index"
    signed_by: str | None = None
    flags: list[str] = field(default_factory=list)

    @property
    def page_span(self) -> tuple[int, int]:
        return (self.pages[0], self.pages[-1])


@dataclass
class VolumeSplit:
    """Everything one volume yielded."""

    documents: list[SplitDocument] = field(default_factory=list)
    index_entries: dict[str, IndexEntry] = field(default_factory=dict)
    index_pages: list[int] = field(default_factory=list)
    furniture_pages: list[int] = field(default_factory=list)
    blank_pages: list[int] = field(default_factory=list)
    failed_pages: list[int] = field(default_factory=list)
    series: str | None = None
    flags: list[str] = field(default_factory=list)

    def reconciliation(self) -> dict:
        """Found-vs-expected against the volume's own index. A report, not a gate.

        The index is alphabetical by subject and one instrument appears under
        several subjects, so the comparison is over the DISTINCT number set.
        """
        expected = set(self.index_entries)
        found = {d.number for d in self.documents if d.number}
        return {
            "expected_from_index": sorted(expected, key=_number_sort_key),
            "found": sorted(found, key=_number_sort_key),
            "missing": sorted(expected - found, key=_number_sort_key),
            "unindexed": sorted(found - expected, key=_number_sort_key),
            "n_expected": len(expected),
            "n_found": len(found),
            "n_documents": len(self.documents),
            "n_unnumbered": sum(1 for d in self.documents if not d.number),
        }


def _number_sort_key(number: str) -> tuple[int, str]:
    """Sort '7' before '7A' before '10' (numeric part first, then suffix)."""
    m = re.match(r"^(\d+)([A-Za-z]?)$", number)
    return (int(m.group(1)), m.group(2)) if m else (10**9, number)


# --------------------------------------------------------------------------- #
# Text rendering                                                                #
# --------------------------------------------------------------------------- #
# element_text / page_lines / page_flags moved to vlm_pages, which the post-1974
# scans need too and which is not about volumes. Re-exported here (not merely
# imported) because this module's public surface is what volume_split's own tests
# and callers reach for, and moving a function must not move its name.
#
# assemble_body went the same way and took this module's last two lines of text
# gluing with it: a page's blocks were joined with "\n" here and the pages with
# "\n\n" in _flush, which published every page of a document as one Markdown
# paragraph and never rejoined a word broken across a line. Both eras now share
# one implementation. What stays here is segmentation: page roles, grouping,
# numbering, subject/signer/date.


# --------------------------------------------------------------------------- #
# Page classification                                                           #
# --------------------------------------------------------------------------- #

def _norm(line: str) -> str:
    return " ".join(clean._norm_tokens(line))


def _letterhead_anchors_on_line(line: str) -> set[tuple[str, ...]]:
    """The letterhead anchors this line IS, not the ones it merely contains."""
    tokens = clean._norm_tokens(line)
    if not tokens or len(tokens) > LETTERHEAD_MAX_LINE_TOKENS:
        return set()
    return {
        anchor for anchor in _LETTERHEAD_ANCHORS
        if clean._phrase_ratio(tokens, anchor) >= LETTERHEAD_RATIO
    }


def _is_letterhead_line(line: str) -> bool:
    return bool(_letterhead_anchors_on_line(line))


def _count_letterhead_anchors(lines: list[str]) -> int:
    """Distinct letterhead anchors printed as lines of their own in the head."""
    hit: set[tuple[str, ...]] = set()
    for line in lines:
        hit |= _letterhead_anchors_on_line(line)
    return len(hit)


def _is_continuation_marker(line: str) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 12:
        return False
    return any(rx.match(stripped) for rx in _CONTINUATION_RES)


def _is_library_furniture(line: str) -> bool:
    norm = _norm(line)
    return any(phrase in norm for phrase in _LIBRARY_FURNITURE)


def _bare_label_opens_line(line: str) -> re.Match[str] | None:
    """A bare-label match that IS the line, not one buried in a sentence.

    Same position rule the numbered patterns use (past :func:`element_text`'s
    own ``#``/``##`` heading mark), plus :data:`BARE_LABEL_MAX_TRAILING_CHARS`
    on the remainder. Without the trailing cap, "Executive Order" at the head of
    a body sentence — "Executive Order No. 40 is hereby revoked and the
    following substituted..." — would still pass the position check.
    """
    stripped = re.sub(r"^#{1,6}\s*", "", line.strip())
    m = _BARE_LABEL_RE.search(stripped)
    if m is None or m.start() != 0:
        return None
    if len(stripped) - m.end() > BARE_LABEL_MAX_TRAILING_CHARS:
        return None
    return m


def find_instrument_label(
    lines: list[str],
    *,
    allow_bare_label: bool = True,
) -> tuple[str | None, str | None, str | None, int | None]:
    """``(series, number, printed_label, line_index)`` from a page head, or all-None.

    ``allow_bare_label`` gates the unnumbered fallback. It exists for the one
    volume that prints no numbers at all; in a volume whose instruments ARE
    numbered it can only misfire, so :func:`split_volume` turns it off there.

    Patterns are tried most-specific-first so "MEMORANDUM / EXECUTIVE ORDER NO.
    14" resolves as an executive order, not a memorandum.

    Every genuine opening prints its label as (essentially) the ENTIRE line —
    "MEMORANDUM NO. 10", "Memorandum No. 51" — so a match is only accepted at
    the start of a line (past :func:`element_text`'s own ``#``/``##`` heading
    mark, when the model tagged the line Title/Section-header). That excludes
    the label showing up mid-sentence in a narrative or reply — "Under date of
    January 10, 1946, the Mayor transmitted ... Memorandum #2 requesting...",
    "In answer to your Memorandum No.20 we wish to inform..." — where a page
    talks ABOUT another instrument rather than opening one of its own. ``Re:``
    lines (see :func:`_is_referential_line`) are excluded outright for the same
    reason, belt-and-suspenders. :func:`has_referential_instrument_mention`
    lets :func:`classify_page` still isolate such a page as its own record
    instead of falling through to whatever heuristic handles a page with no
    label at all.
    """
    for i, line in enumerate(lines):
        if _is_referential_line(line):
            continue
        stripped = re.sub(r"^#{1,6}\s*", "", line.strip())
        for series, pattern in _INSTRUMENT_PATTERNS:
            m = pattern.search(stripped)
            if m and m.start() == 0:
                return series, normalize_number(m.group(1)), m.group(0).strip(), i
    # No number found — fall back to a bare label (the unnumbered volume).
    if not allow_bare_label:
        return None, None, None, None
    for i, line in enumerate(lines):
        if _is_referential_line(line):
            continue
        m = _bare_label_opens_line(line)
        if m:
            label = m.group(0).strip()
            for series, pattern in _INDEX_SERIES_RES:
                if pattern.search(label):
                    return series, None, label, i
    return None, None, None, None


def has_referential_instrument_mention(lines: list[str]) -> bool:
    """True if a page ANNOUNCES itself as being about another instrument.

    This is the narrower of the two exclusions :func:`find_instrument_label`
    makes: an explicit ``Re:``/``In answer to`` lead-in, which is how a cover
    note or reply letter states its own subject. Such a page is its own piece of
    correspondence and must not merge into whatever document happens to be open
    — but it must not be minted under the number it merely cites either.

    The OTHER exclusion — a label buried mid-sentence — deliberately does NOT
    reach here. These orders amend each other constantly in ordinary body prose
    ("Section one, Section two of Executive Order No. 32 dated September 29,
    1967 is hereby amended...", "The provisions of Executive Order No. 30 ...
    are hereby suspended"), and treating every such cross-reference as a new
    record cut long orders apart mid-section. For those pages "not a label" is
    the whole answer, and they fall through to CONTINUATION as they should.
    """
    return any(
        _is_referential_line(line) and any(p.search(line) for _, p in _INSTRUMENT_PATTERNS)
        for line in lines
    )


def classify_page(
    record: dict,
    *,
    min_year: int,
    max_year: int,
    allow_bare_label: bool = True,
) -> PageInfo:
    """Assign one page its structural role.

    ``min_year``/``max_year`` bound the date probe used to tell a genuine
    document opening from an attached exhibit that merely reprints letterhead.
    ``allow_bare_label`` is forwarded to :func:`find_instrument_label`.
    """
    page = int(record.get("page", 0))
    flags = page_flags(record)

    if record.get("skipped"):
        return PageInfo(page=page, role=ROLE_BLANK, flags=flags)

    lines = page_lines(record)
    if not lines:
        # Elements empty with no skip reason: OCR produced nothing usable. Page
        # 83 of the proven run looks exactly like this (repetition loop ->
        # unparseable JSON), and it must not pass silently as an empty page.
        return PageInfo(page=page, role=ROLE_FAILED, flags=flags or ["empty-output"])

    head = lines[:HEAD_LINES]
    blocks = page_blocks(record)

    if _INDEX_TITLE_RE.search(" ".join(head)):
        return PageInfo(page=page, role=ROLE_INDEX, lines=lines, blocks=blocks, flags=flags)
    # Index continuation pages carry no title, just "PAGE 2" and another table.
    if _has_index_table(record):
        return PageInfo(page=page, role=ROLE_INDEX, lines=lines, blocks=blocks, flags=flags)

    series, number, label, label_line = find_instrument_label(
        head, allow_bare_label=allow_bare_label
    )
    letterhead = _count_letterhead_anchors(head)

    # A leading page-number ornament means this page continues the previous
    # instrument — unless it ALSO carries letterhead or a NUMBERED instrument
    # label, in which case a new document genuinely starts here.
    #
    # The ornament outranks an unnumbered label deliberately. "-2-" printed
    # alone at the top of a page is the most reliable signal these volumes
    # carry, and it is mutually exclusive with a document opening: a first page
    # is never numbered "-2-". Letting a bare label override it is what put a
    # phantom document at Lindsay pages 108-110 — the ornament said "page 2 of
    # order 84" and a body sentence saying "this Executive Order" outvoted it.
    leading_ornament = _is_continuation_marker(lines[0])
    if leading_ornament and number is None and letterhead < MIN_LETTERHEAD_ANCHORS:
        return PageInfo(page=page, role=ROLE_CONTINUATION, lines=lines, blocks=blocks,
                        flags=flags)

    # An instrument label is decisive on its own — UNLESS the very next line is
    # itself a page-number ornament ("-page 2-", "-2-"). Some instruments repeat
    # their own label as a running header on continuation pages too
    # ("MEMORANDUM NO. 10" / "-page 2-" / "8/26/46" / ...quote continues...), and
    # without this check that running header reopens the instrument as a
    # spurious duplicate instead of continuing it.
    if series is not None:
        if (label_line is not None and label_line + 1 < len(lines)
                and _is_continuation_marker(lines[label_line + 1])):
            return PageInfo(page=page, role=ROLE_CONTINUATION, lines=lines, blocks=blocks,
                            flags=flags)
        return PageInfo(page=page, role=ROLE_START, lines=lines, blocks=blocks,
                        series=series, number=number, printed_label=label,
                        flags=flags)
    if letterhead >= MIN_LETTERHEAD_ANCHORS and clean.extract_date_in_range(
        head, min_year, max_year, scan_lines=HEAD_LINES
    ):
        return PageInfo(page=page, role=ROLE_START, lines=lines, blocks=blocks,
                        series=None, number=None, printed_label=None, flags=flags)

    # A reply letter or forwarding note that names another instrument ("Re:
    # Memorandum #2 ... (attached)", "In answer to your Memorandum No.20") is
    # still the start of its own content — it must not merge into whatever
    # document happens to be open, but it also must not mint a document under
    # the number it's merely referencing.
    if has_referential_instrument_mention(head):
        return PageInfo(page=page, role=ROLE_START, lines=lines, blocks=blocks,
                        series=None, number=None, printed_label=None,
                        flags=flags + ["referential-label-ignored"])

    # Library stamps and endpapers: furniture only, no instrument content.
    if all(_is_library_furniture(ln) or not ln.strip() for ln in lines):
        return PageInfo(page=page, role=ROLE_FURNITURE, lines=lines, blocks=blocks,
                        flags=flags)

    # Anything else (an attached exhibit, a form, a report enclosure) continues
    # whatever came before it rather than starting something new.
    return PageInfo(page=page, role=ROLE_CONTINUATION, lines=lines, blocks=blocks,
                    flags=flags)


def _has_index_table(record: dict) -> bool:
    """True if a page holds a SUBJECT/NUMBER/DATE table (the volume's index)."""
    for element in record.get("elements", []):
        text = element.get("text") or ""
        if "<table" not in text.lower():
            continue
        for row in _TABLE_ROW_RE.findall(text):
            cells = [_TAG_RE.sub("", c).strip().upper() for c in _TABLE_CELL_RE.findall(row)]
            if "SUBJECT" in cells and "NUMBER" in cells:
                return True
    return False


# --------------------------------------------------------------------------- #
# Subject-index parsing                                                         #
# --------------------------------------------------------------------------- #

def parse_index(records: list[dict], min_year: int, max_year: int) -> dict[str, IndexEntry]:
    """Distinct instrument numbers (and their dates) from the volume's index.

    The index is alphabetical by SUBJECT, so one instrument appears under several
    headings and a continuation row leaves the subject cell empty. Numbers are
    therefore collected as a set, with every subject that cites them retained for
    the provenance sidecar.
    """
    entries: dict[str, IndexEntry] = {}
    for record in records:
        if not _has_index_table(record):
            continue
        for element in record.get("elements", []):
            text = element.get("text") or ""
            if "<table" not in text.lower():
                continue
            for row in _TABLE_ROW_RE.findall(text):
                cells = [_TAG_RE.sub("", c).strip() for c in _TABLE_CELL_RE.findall(row)]
                if len(cells) < 3:
                    continue
                subject, raw_number, raw_date = cells[0], cells[1], cells[2]
                if not _NUMBER_CELL_RE.match(raw_number):
                    continue  # header row, or a subject with no number of its own
                number = normalize_number(raw_number)
                entry = entries.setdefault(number, IndexEntry(number=number))
                if subject and subject not in entry.subjects:
                    entry.subjects.append(subject)
                if entry.date is None and raw_date:
                    entry.date = clean.extract_date_in_range(
                        [raw_date], min_year, max_year, scan_lines=1
                    )
    return entries


# --------------------------------------------------------------------------- #
# Series resolution                                                             #
# --------------------------------------------------------------------------- #

def series_from_description(description: str | None) -> str | None:
    """The series a volume's CATALOGUE record says it collects, or None.

    ``sources/gpp/volumes.json`` carries DORIS's own one-line statement of what
    each compilation is — "A collection of the Executive Memoranda issued by
    Mayor John V. Lindsay between January 16, 1969 and December 27, 1971". That
    is a literal string in committed source metadata, not an inference, and it
    is available for all fourteen volumes where a printed index title is not.
    """
    if not description:
        return None
    for series, pattern in _INDEX_SERIES_RES:
        if pattern.search(description):
            return series
    return None


def resolve_series(
    pages: list[PageInfo],
    index_pages: list[PageInfo],
    description_series: str | None = None,
) -> tuple[str, list[str]]:
    """The series a volume numbers its instruments in, plus any flags.

    A VOLUME-level property, not a per-page one, because the evidence says the
    printed label varies *within a single numbering sequence*: the
    Impellitteri-Sharkey volume runs one continuous 1..39 series whose members
    are printed as "Memorandum No. 1", "EXECUTIVE ORDER NO. 14" and "EXECUTIVE
    MEMORANDUM #30" interchangeably. Minting three different series from that
    would assert three independent sequences that do not exist.

    Resolution order, most authoritative first:

    1. The volume's own index title ("INDEX TO THE EXECUTIVE ORDERS OF THE
       MAYOR") — the compilers' own statement, printed in the artifact itself.
    2. ``description_series`` — the catalogue record's statement of the same
       thing (see :func:`series_from_description`). It sits above the majority
       vote because the vote is only as good as its evidence, and on the
       1969-01-16..1971-12-27 Lindsay memoranda that evidence is a SINGLE
       labelled document in 174 pages. One stray "EXECUTIVE ORDER" heading was
       enough to name the whole volume EO, against a catalogue record that says
       in so many words it collects Executive Memoranda.
    3. Otherwise the most frequent printed label across the volume's documents.
    4. Otherwise executive memorandum, the commoner instrument, with a flag.
    """
    flags: list[str] = []
    for info in index_pages:
        head = " ".join(info.lines[:6])
        if not _INDEX_TITLE_RE.search(head):
            continue
        for series, pattern in _INDEX_SERIES_RES:
            if pattern.search(head):
                return series, flags

    counts: dict[str, int] = {}
    for info in pages:
        if info.series:
            counts[info.series] = counts.get(info.series, 0) + 1

    if description_series:
        if counts and set(counts) != {description_series}:
            flags.append(
                "series-from-catalogue: the volume's catalogue description says "
                f"{description_series}; the printed labels found on its pages say "
                f"{counts}. Took the catalogue's. Each document's verbatim label "
                "is preserved in the sidecar."
            )
        return description_series, flags

    if counts:
        best = max(counts, key=lambda s: counts[s])
        if len(counts) > 1:
            flags.append(
                "mixed-instrument-labels: printed labels vary across the volume "
                f"({counts}); series taken from the majority ({best}). Each "
                "document's verbatim label is preserved in the sidecar."
            )
        return best, flags

    flags.append(
        "series-undetermined: no index title, no catalogue description and no "
        "numbered instrument label found; defaulted to executive memorandum"
    )
    return SERIES_EXECUTIVE_MEMORANDUM, flags


# --------------------------------------------------------------------------- #
# Signature                                                                     #
# --------------------------------------------------------------------------- #

# Who actually signed. Pre-1974 volumes are full of Acting and Deputy Mayors
# standing in (Impellitteri for O'Dwyer, Sharkey for Impellitteri, Theobald for
# Wagner), which is a property of the SIGNATURE, not of the administration —
# `mayor` stays the administration's mayor and this rides in the sidecar.
_SIGNOFF_ROLE_RE = re.compile(
    r"^(MAYOR|ACTING\s+MAYOR|DEPUTY\s+MAYOR|CITY\s+ADMINISTRATOR)\b", re.IGNORECASE
)
_NAME_LINE_RE = re.compile(r"^[A-Z][A-Z.\s'’-]{5,60}$")


# The era's own way of naming a document. A 1940s-50s memorandum has no caption
# line; where it states a subject at all it does so explicitly, on a "SUBJECT:" or
# "RE:" line inside the TO/FROM header block. Reading that — and only that — is
# what keeps `title` a real statement from the page instead of a guess assembled
# out of whatever happened to be typed in capitals.
_SUBJECT_RE = re.compile(
    r"\b(?:SUBJECT|RE|IN\s+RE)\s*[:.—-]\s*(.+)$", re.IGNORECASE
)
# Trailing file marks / clerk initials that ride on the end of a subject line.
_SUBJECT_TRAIL_RE = re.compile(r"[\s.:;,\-]+$")


def find_subject(lines: list[str]) -> str | None:
    """The document's stated subject, from a ``SUBJECT:``/``RE:`` line, else None.

    None is the correct and common answer: most of these instruments simply do
    not state a subject, and an empty ``title`` is right where a fabricated one
    would be a bug.
    """
    for line in lines[:HEAD_LINES]:
        m = _SUBJECT_RE.search(line)
        if not m:
            continue
        subject = _SUBJECT_TRAIL_RE.sub("", m.group(1).strip())
        # A bare "SUBJECT:" with the text on the next line gives nothing useful.
        if len(subject) >= 3:
            return subject
    return None


def find_signer(lines: list[str]) -> str | None:
    """The name printed above a mayoral sign-off role line, if there is one."""
    for i, line in enumerate(lines):
        if not _SIGNOFF_ROLE_RE.match(line.strip()):
            continue
        for back in range(i - 1, max(-1, i - 4), -1):
            candidate = lines[back].strip()
            if _NAME_LINE_RE.match(candidate) and not _is_letterhead_line(candidate):
                return candidate
    return None


# --------------------------------------------------------------------------- #
# Index reconciliation                                                          #
# --------------------------------------------------------------------------- #

def reconcile_with_index(split: VolumeSplit) -> None:
    """Cross-check documents against the volume's own index; repair in place.

    Both sides of every comparison are literal strings printed in the same
    scanned volume — the instrument's own face and the compilers' index — so
    nothing here infers content that is not on the page. What it does is decide
    WHICH of two printed statements to believe, and record that it did.

    Four passes. NUMBERS ARE SETTLED FIRST, then dates — otherwise a document
    still carrying a misread number picks up a date-disagreement flag against the
    wrong index row, and that stale flag survives the later renumbering.

    1. **Resolve a duplicated number** by matching dates against an unclaimed
       index entry. This is what recovers the real "7A" from a page whose suffix
       the OCR dropped, leaving two documents both claiming "7".
    2. **Recover a missing number** for an unnumbered document whose date matches
       exactly one unclaimed index entry — the case where the number sat in a
       part of the page the model did not return.
    3. **Fill a missing date** from the index entry for a number we now trust.
    4. **Flag a date disagreement**, keeping the instrument's own printed date.
       The index is a secondary, hand-compiled finding aid and is demonstrably
       wrong in places (this volume's index dates memoranda 28, 29 and 31 a month
       later than their own faces, which would also put 30 out of sequence).

    Passes 1 and 2 only ever act when the match is UNAMBIGUOUS (exactly one
    unclaimed candidate). Anything else is left alone and flagged.
    """
    index = split.index_entries
    if not index:
        return

    docs = split.documents
    for doc in docs:
        if doc.date_on_page:
            doc.date_source = "page"

    def _claimed() -> set[str]:
        return {d.number for d in docs if d.number}

    # --- 1: split a duplicated number using the index's dates ---------------- #
    by_number: dict[str, list[SplitDocument]] = {}
    for doc in docs:
        if doc.number:
            by_number.setdefault(doc.number, []).append(doc)
    for number, group in sorted(by_number.items()):
        if len(group) < 2:
            continue
        for doc in group:
            if not doc.date_on_page:
                continue
            candidates = [
                n for n, e in index.items()
                if e.date == doc.date_on_page and n not in _claimed()
            ]
            if len(candidates) == 1:
                recovered = candidates[0]
                doc.flags.append(
                    f"number-recovered-from-index: read as {number}, but the "
                    f"index lists {recovered} on {doc.date_on_page} and {number} "
                    "on another date; renumbered to match the index"
                )
                doc.number, doc.number_source = recovered, "index"

    # --- 2: recover a number for an unnumbered document ---------------------- #
    for doc in docs:
        if doc.number or not doc.date_on_page:
            continue
        candidates = [
            n for n, e in index.items()
            if e.date == doc.date_on_page and n not in _claimed()
        ]
        if len(candidates) == 1:
            recovered = candidates[0]
            doc.flags.append(
                f"number-recovered-from-index: no number read off the "
                f"instrument; the index lists exactly one entry ({recovered}) "
                f"dated {doc.date_on_page}"
            )
            doc.number, doc.number_source = recovered, "index"

    # --- 3 + 4: dates, now that every number is settled ---------------------- #
    for doc in docs:
        entry = index.get(doc.number) if doc.number else None
        if entry is None:
            continue
        if doc.date_on_page is None and entry.date:
            doc.date_on_page, doc.date_source = entry.date, "index"
            doc.flags.append(
                f"date-from-index: no date read off the instrument; took "
                f"{entry.date} from the volume's index entry for {doc.number}"
            )
        elif doc.date_on_page and entry.date and doc.date_on_page != entry.date:
            doc.flags.append(
                f"date-mismatch-with-index: instrument reads {doc.date_on_page}, "
                f"index says {entry.date}; kept the instrument's own date"
            )

    # Anything still doubled is a real problem, not a recoverable one.
    remaining: dict[str, int] = {}
    for doc in docs:
        if doc.number:
            remaining[doc.number] = remaining.get(doc.number, 0) + 1
    for number, count in sorted(remaining.items()):
        if count > 1:
            split.flags.append(
                f"duplicate-number: {number} still claimed by {count} documents "
                "after index reconciliation; ids will be suffixed to stay unique"
            )


# --------------------------------------------------------------------------- #
# The split                                                                     #
# --------------------------------------------------------------------------- #

def split_volume(
    records: list[dict],
    *,
    min_year: int,
    max_year: int,
    series_override: str | None = None,
    description: str | None = None,
) -> VolumeSplit:
    """Segment one volume's page records into per-instrument documents.

    ``min_year``/``max_year`` bound date extraction to the volume's own stated
    coverage, so a misread year cannot invent an order outside its span.
    ``description`` is the volume's catalogue record; it feeds
    :func:`resolve_series` (see there for where it sits in the precedence).
    """
    ordered = sorted(records, key=lambda r: int(r.get("page", 0)))

    # Two passes, because whether the bare-label fallback applies is a property
    # of the VOLUME, not of a page. Pass one reads numbers only. If the volume
    # printed any at all it is a numbered volume, and every page that lacks a
    # number is a continuation or an unlabelled opening — never an "unnumbered
    # instrument" — so the fallback stays off and cannot mint phantoms out of
    # body prose. Only a volume that yielded no numbers anywhere (the
    # un-numbered Lindsay memoranda listing) gets pass two.
    infos = [
        classify_page(r, min_year=min_year, max_year=max_year, allow_bare_label=False)
        for r in ordered
    ]
    n_numbered = sum(1 for i in infos if i.number)
    if n_numbered == 0:
        infos = [
            classify_page(r, min_year=min_year, max_year=max_year,
                          allow_bare_label=True)
            for r in ordered
        ]

    result = VolumeSplit()
    result.index_entries = parse_index(ordered, min_year, max_year)
    index_infos = [i for i in infos if i.role == ROLE_INDEX]
    result.index_pages = [i.page for i in index_infos]
    result.furniture_pages = [i.page for i in infos if i.role == ROLE_FURNITURE]
    result.blank_pages = [i.page for i in infos if i.role == ROLE_BLANK]
    result.failed_pages = [i.page for i in infos if i.role == ROLE_FAILED]

    starts = [i for i in infos if i.role == ROLE_START]
    series, series_flags = resolve_series(
        starts, index_infos, series_from_description(description)
    )
    if series_override:
        series = series_override
    result.series = series
    result.flags.extend(series_flags)

    # Group: every START opens a document; CONTINUATION and FAILED pages attach
    # to the open one. A FAILED page attaches (rather than being dropped) so its
    # flag lands on the record whose text is incomplete because of it.
    current: SplitDocument | None = None
    buffered: list[PageInfo] = []

    def _flush() -> None:
        nonlocal current, buffered
        if current is None:
            return
        current.body = assemble_body([i.blocks for i in buffered])
        all_lines = [ln for i in buffered for ln in i.lines]
        current.date_on_page = clean.extract_date_in_range(
            all_lines, min_year, max_year, scan_lines=HEAD_LINES
        )
        current.signed_by = find_signer(all_lines)
        current.subject = find_subject(all_lines)
        for info in buffered:
            for flag in info.flags:
                tagged = f"page {info.page}: {flag}"
                if tagged not in current.flags:
                    current.flags.append(tagged)
        result.documents.append(current)
        current, buffered = None, []

    for info in infos:
        if info.role in (ROLE_BLANK, ROLE_INDEX, ROLE_FURNITURE):
            continue
        if info.role == ROLE_START:
            _flush()
            current = SplitDocument(
                # The VOLUME's series, not this page's printed label — one
                # numbering sequence gets one series (see resolve_series). The
                # page's own wording is preserved verbatim in printed_label.
                series=series,
                number=info.number,
                pages=[info.page],
                body="",
                printed_label=info.printed_label,
            )
            buffered = [info]
            continue
        # CONTINUATION / FAILED before any start: orphaned content.
        if current is None:
            result.flags.append(
                f"orphan-page: page {info.page} carries content but no instrument "
                "has opened yet; not attached to any record"
            )
            continue
        current.pages.append(info.page)
        buffered.append(info)
    _flush()

    reconcile_with_index(result)
    return result
