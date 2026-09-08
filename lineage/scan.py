"""Pass one — look for every agency name we already know, in every order.

This is the pass ``supersede.py`` never had. There, the registry is only a CHECK:
one pattern pulls a name out of a "there is hereby established" sentence, and the
registry is asked whether that name is known. Nothing ever reads the corpus asking
where a known name appears, so 39 orders mention DoITT and not one of them is
recorded. Here the registry does the searching.

How: one compiled pattern holding every name we search for, longest first, so
Python's first-match-wins alternation lands on the longest name at any given spot.
No Aho-Corasick library — 592 names over roughly 12 MB of text is a single pass,
about 4 seconds, and plain standard-library code keeps engineering-standards §7
(local, repeatable, no network) obviously true.

Three details that matter:

* **Positions point into ``full_text`` untouched.** The order text is never tidied
  before searching, so ``full_text[start:end]`` gives back exactly what was found.
  The NAME is tidied instead, and the found text is tidied only to look that name
  back up.
* **Spaces inside a name stretch.** Pulling text out of a PDF wraps phrases across
  lines — "Department of\\nInformation Technology" is the normal case, not the
  exception. Each space in a name becomes ``\\s+``, which finds those while leaving
  the recorded position exact.
* **The mayoral letterhead is marked, not deleted.** Nearly every order opens with
  ``OFFICE OF THE MAYOR`` on its stationery, and counting that would put the office
  at the top of the report on the strength of its notepaper. Those finds carry
  ``in_letterhead``; see :mod:`lineage.letterhead`. Nothing is dropped, so the
  read-back promise and pass two are both untouched.
"""

from __future__ import annotations

import re

from . import letterhead
from .mentions import (
    FOUND_BY_NAME_LIST,
    ONE_AGENCY,
    SEVERAL_AGENCIES,
    Mention,
)
from .namelist import MATCH_ANY_CASE, NameList
from .normalize import normalize_name

# Agencies whose name is PRINTED ON THE STATIONERY, so a find of it may be
# letterhead rather than a reference. Only the mayoral masthead qualifies: over
# the whole corpus the letterhead test marks 2547 of this agency's 2810 finds and
# got none of them wrong in a hand check, while letting it judge every agency
# marked 63 more finds of which about 30 were plain sentences that OCR had broken
# onto their own line. A narrow list is the honest one.
MASTHEAD_AGENCY_IDS = frozenset({"office-of-the-mayor"})

# A name must start and end at a word edge. Explicit look-arounds rather than \b,
# because \b after a name ending in something other than a letter or digit asserts
# the wrong thing.
_LEFT_EDGE = r"(?<![A-Za-z0-9])"
_RIGHT_EDGE = r"(?![A-Za-z0-9])"

# re.escape turns a space into "\ "; let that run stretch.
_ESCAPED_SPACE = "\\ "


def name_pattern(name: str, match: str) -> str:
    """One name's piece of the big pattern, with its own take on capitals.

    ``(?i:...)`` limits case-blindness to this piece alone, so a name that needs its
    capitals and one that does not can sit in the same pattern.
    """
    body = re.escape(name).replace(_ESCAPED_SPACE, r"\s+")
    return f"(?i:{body})" if match == MATCH_ANY_CASE else body


def build_matcher(name_list: NameList) -> re.Pattern[str] | None:
    """Compile the big pattern, or return ``None`` when there is nothing to look for.

    ``name_list.searchable`` is already longest-first from :func:`namelist.build`,
    which is what makes the pattern prefer the longest name at any spot — "New York
    City Police Department" beats "Police Department".
    """
    pieces = [name_pattern(n.name, n.match) for n in name_list.searchable]
    if not pieces:
        return None
    return re.compile(_LEFT_EDGE + "(?:" + "|".join(pieces) + ")" + _RIGHT_EDGE)


def scan_record(eo_id: str, text: str, matcher: re.Pattern[str],
                ids_by_name: dict[str, tuple[tuple[str, ...], str]]) -> list[Mention]:
    """Every known name found in one order.

    ``re.finditer`` never returns overlapping results and reads left to right, so
    what comes back is already separate — no second pass is needed to remove nested
    finds. A name belonging to more than one agency is recorded with
    ``agency_id: None`` and ``pins_down: "several-agencies"``; it is never settled
    by picking one (README: "a guess is a bug").
    """
    out: list[Mention] = []
    for m in matcher.finditer(text):
        found = m.group(0)
        entry = ids_by_name.get(normalize_name(found))
        if entry is None:
            # Only reachable if the name list and the pattern disagree. Skipping is
            # right — recording something we cannot attribute would be a guess.
            continue
        agency_ids, source = entry
        several = len(agency_ids) > 1
        # Asked only of the masthead agencies, so the cost falls on a fraction of
        # the finds. A name shared by two agencies is never judged: we do not know
        # whose stationery it would be.
        on_stationery = (not several and agency_ids[0] in MASTHEAD_AGENCY_IDS
                         and letterhead.is_letterhead(text, m.start(), m.end()))
        out.append(Mention(
            eo_id=eo_id,
            start=m.start(),
            end=m.end(),
            text=found,
            agency_id=None if several else agency_ids[0],
            agency_ids=agency_ids,
            found_by=FOUND_BY_NAME_LIST,
            pins_down=SEVERAL_AGENCIES if several else ONE_AGENCY,
            source=source,
            in_letterhead=on_stationery,
        ))
    return out


def scan(records: list[dict], name_list: NameList) -> list[Mention]:
    """Run pass one over every order that has text.

    Depends on nothing but its two inputs, so it gives the same answer every time.
    """
    matcher = build_matcher(name_list)
    if matcher is None:
        return []
    ids_by_name = {n.normalized: (n.agency_ids, n.source)
                   for n in name_list.searchable}
    out: list[Mention] = []
    for r in records:
        out.extend(
            scan_record(r["eo_id"], r.get("full_text") or "", matcher, ids_by_name))
    return out


def name_hit_counts(mentions: list[Mention], name_list: NameList, *,
                    body_only: bool = False) -> dict[str, int]:
    """How many times each NAME was hit, not each spelling of it.

    Feeds :func:`namelist.names_needing_a_rule`, the safety check on short names.
    Counting by the name list's own spelling keeps "Parks", "PARKS", and "parks" on
    one line.

    ``body_only`` leaves out the letterhead finds. Use it for anything a person
    READS as a measure of how often an agency comes up. Do NOT use it for the
    safety check: that check asks whether a short name is slipping in on the
    default rule, and it has to see every hit to answer.
    """
    spelling_by_normalized = {n.normalized: n.name for n in name_list.names}
    counts: dict[str, int] = {}
    for m in mentions:
        if body_only and m.in_letterhead:
            continue
        name = spelling_by_normalized.get(normalize_name(m.text))
        if name is None:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts
