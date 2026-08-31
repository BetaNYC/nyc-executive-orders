"""Pass two — find phrases shaped like agency names that we do not know yet.

This is the pass the lineage work depends on, not a backstop. The registry holds
317 agencies and marks every one ``status: "active"``, so pass one cannot possibly
see a body that was shut down. DoITT, "Office of Information Technology", and the
1998 "City of New York Technology Steering Committee" are invisible to it. They
show up here.

The rule: an optional run of capitalized words, then one of the nouns a New York
City body is named after (:data:`AGENCY_NOUNS`), then an optional "of"/"for"/"on"
tail. It finds a lot and it is wrong often, on purpose — what it finds goes to a
person, and only a person moves a name into
``lineage/data/extra_agencies.json``.

Five rules earn their place, and each has a test:

* **Small joining words stay inside the name.** Without them "Taxi and Limousine
  Commission" is cut down to "Limousine Commission", and "Department of Health and
  Mental Hygiene" to "Department of Health".
* **A noun on its own is thrown out.** "Commission" alone was the single most
  common find under a first-draft rule (1,174 hits), and it names nothing.
* **The noun's first letter must be a capital.** Letting it be any case pulls in
  228 spans of plain prose — "Each agency shall report", "every City agency".
* **The possessive is a whole token.** ``str.rstrip("'’s")`` eats a real trailing
  "s" and turns "Department of Buildings" into "Department of Building" — measured,
  not imagined.
* **Words from the next clause are trimmed off.** The tail rule otherwise runs on:
  "Board of Estimate on June" (37 hits) and "Bureau of the Budget for" (24) were
  both real finds.
"""

from __future__ import annotations

import re

from .mentions import (
    FOUND_BY_PATTERN,
    WHY_ALREADY_MATCHED,
    WHY_JUST_A_NOUN,
    WHY_NOT_ON_THE_LIST,
    WHY_UNREADABLE,
    Discarded,
    Mention,
    ProposedName,
)
from .normalize import collapse_spaces, normalize_name, strip_possessive
from .textquality import readable

# The nouns a New York City body is named after. Longest first inside the pattern,
# so "Task Force" wins over a bare "Force" would-be match.
AGENCY_NOUNS = (
    "Task Force", "Taskforce", "Administration", "Corporation", "Commission",
    "Department", "Committee", "Authority", "Division", "Council", "Bureau",
    "Agency", "Office", "Center", "Panel", "Board", "Fund", "Unit", "Program",
)

# Small words allowed to sit INSIDE a name without ending it.
JOINING_WORDS = ("and", "of", "for", "the", "on")

# A modifier word must be CAPITALIZED — that is what separates a proper name from
# the prose around it, and it is the one part that must respect case.
_WORD = r"[A-Z][A-Za-z'’\-]+"
# Joining words and tail prepositions ignore case, because plenty of orders are set
# in full capitals; "THE MAYOR'S DOMESTIC VIOLENCE COORDINATING COUNCIL" is the
# literal title of 1993-EO-057. A rule that respects case everywhere finds nothing
# in a document like that.
_JOIN = "(?i:" + "|".join(JOINING_WORDS) + ")"


def _noun_pattern(noun: str) -> str:
    """A noun whose FIRST LETTER must be a capital, with the rest case-blind.

    "Department" and "DEPARTMENT" both head a name; "agency" in "Each agency" or
    "City agency" does not.
    """
    parts = []
    for token in noun.split():
        parts.append(token[0].upper() + (f"(?i:{token[1:]})" if token[1:] else ""))
    return r"\s+".join(parts)


_NOUN = "(?:" + "|".join(_noun_pattern(n) for n in AGENCY_NOUNS) + ")"
# "with" continues a name ("Office for People with Disabilities") but may never end
# one, so it belongs here and not in JOINING_WORDS.
_TAIL_WORD = "(?i:" + "|".join((*JOINING_WORDS, "with")) + ")"
# Up to 5 capitalized words before the noun, each optionally followed by a joining
# word. This is what keeps "Taxi and Limousine" attached.
_BEFORE = rf"(?:{_WORD}\s+(?:{_JOIN}\s+)?){{0,5}}"
# An "of/for/on ..." tail, itself allowing capitalized words and joining words.
_AFTER = (rf"(?:\s+(?i:of|for|on)\s+(?:(?i:the)\s+)?{_WORD}"
          rf"(?:\s+(?:{_WORD}|{_TAIL_WORD}))*)?")

CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9])" + _BEFORE + _NOUN + _AFTER + r"(?:[’']s)?"
    + r"(?![A-Za-z0-9])")

MONTHS = ("January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December")

# Words from the following clause that the tail rule picks up. Trimmed off the text
# as found, so the recorded end position shrinks with it.
_TRIM_POSSESSIVE_RE = re.compile(r"[’']s$")
_TRIM_JOINING_RE = re.compile(
    r"\s+(?:" + "|".join((*JOINING_WORDS, "with", "to", "in", "by", "at")) + r")$",
    re.IGNORECASE)
_TRIM_MONTH_RE = re.compile(
    r"(?:\s+(?:of|on))?\s+(?:" + "|".join(MONTHS) + r")$", re.IGNORECASE)
_TRIM_RES = (_TRIM_POSSESSIVE_RE, _TRIM_MONTH_RE, _TRIM_JOINING_RE)

# A phrase that is JUST one of the nouns names nothing. Compared after tidying, so
# "The Commission" folds down to "commission".
_JUST_A_NOUN = frozenset(normalize_name(n) for n in AGENCY_NOUNS)

MIN_WORDS = 2


def trim_trailing_words(found: str) -> str:
    """Cut a trailing possessive, month, or dangling joining word off a find.

    Works on the text exactly as found and returns the START of it, so a caller can
    pull the end position back by the difference and keep ``full_text[start:end]``
    exact. Applied over and over until nothing changes, because the debris stacks:
    "Board of Estimate on June" needs the month rule, while "... Budget for" needs
    only the joining-word rule.
    """
    previous = None
    out = found.rstrip()
    while out and out != previous:
        previous = out
        for rx in _TRIM_RES:
            out = rx.sub("", out).rstrip()
    return out


def tidy_name(found: str) -> str:
    """Fold a trimmed find into its comparable, single-spaced form.

    For grouping and name-list lookup only. The recorded position comes from the
    regex match adjusted by :func:`trim_trailing_words`, never from this string's
    length — squeezing the spaces here would break the tie to ``full_text``.
    """
    return strip_possessive(collapse_spaces(found)).strip()


def _spans_by_order(mentions: list[Mention]) -> dict[str, list[tuple[int, int]]]:
    out: dict[str, list[tuple[int, int]]] = {}
    for m in mentions:
        out.setdefault(m.eo_id, []).append((m.start, m.end))
    for spans in out.values():
        spans.sort()
    return out


def _overlaps(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < s_end and s_start < end for s_start, s_end in spans)


def find_in_record(
    eo_id: str,
    text: str,
    known_names: frozenset[str],
    already_matched: list[tuple[int, int]],
) -> tuple[list[ProposedName], list[tuple[str, str]]]:
    """Proposals from one order, plus ``(name, why)`` pairs that were thrown out.

    A spot pass one already claimed is thrown out: a name-list match says more than
    a pattern guess, so pass one wins every overlap.
    """
    proposed: list[ProposedName] = []
    discarded: list[tuple[str, str]] = []

    for m in CANDIDATE_RE.finditer(text):
        trimmed = trim_trailing_words(m.group(0))
        if not trimmed:
            continue
        start, end = m.start(), m.start() + len(trimmed)
        # `trimmed` is what the order literally says (newlines included); `name` is
        # the tidied form used for grouping and lookup. The promise
        # full_text[start:end] == text holds on `trimmed`, not on `name`.
        name = tidy_name(trimmed)
        if not name:
            continue
        if normalize_name(name) in _JUST_A_NOUN or len(name.split()) < MIN_WORDS:
            discarded.append((name, WHY_JUST_A_NOUN))
            continue
        if not readable(name):
            discarded.append((name, WHY_UNREADABLE))
            continue
        if normalize_name(name) in known_names:
            # Already on the name list. Either pass one recorded it, or its rule
            # says never match; either way pass two must not propose it again.
            continue
        if _overlaps(start, end, already_matched):
            discarded.append((name, WHY_ALREADY_MATCHED))
            continue
        proposed.append(ProposedName(
            eo_id=eo_id, start=start, end=end, text=trimmed, name=name,
            found_by=FOUND_BY_PATTERN, why=WHY_NOT_ON_THE_LIST))

    return proposed, discarded


def find_new_names(records: list[dict], known_names: frozenset[str],
                   mentions: list[Mention]
                   ) -> tuple[list[ProposedName], list[Discarded]]:
    """Run pass two over every order that has text.

    Depends on nothing but its inputs, so it gives the same answer every time.
    """
    spans = _spans_by_order(mentions)
    proposed: list[ProposedName] = []
    counts: dict[tuple[str, str], int] = {}

    for r in records:
        eo_id = r["eo_id"]
        found, discarded = find_in_record(
            eo_id, r.get("full_text") or "", known_names, spans.get(eo_id, []))
        proposed.extend(found)
        for pair in discarded:
            counts[pair] = counts.get(pair, 0) + 1

    thrown_out = [Discarded(name=name, why=why, count=n)
                  for (name, why), n in counts.items()]
    return proposed, thrown_out
