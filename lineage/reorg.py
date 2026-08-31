"""Pass three — what an order DOES to an agency.

Passes one and two find where agencies are NAMED. This pass finds the sentences
that link two of them: established, renamed, abolished, transferred, merged. Those
sentences are the family tree.

``src/nyc_executive_orders/supersede.py`` has the ancestor of this code and it
catches very little. Its one pattern reads the existential shape,
``there is hereby established a <Name>``, which is 133 sentences in the whole
corpus. The far commoner shape puts the name FIRST — ``The Office of the Auditor
General ... is hereby established in the Office of the Mayor`` — and it sees none
of them. It also has nowhere to put a rename or an abolition, because it writes one
scalar field. This pass reads both shapes and records an edge either way. Nothing is
imported from that module; this package stands alone.

**How the false friends are kept out.** The two commonest verbs in the corpus are
also the two least trustworthy:

* ``designated`` — 258 hits. ``The Honorable Paul R. Screvane is hereby designated
  Vice-Chairman`` is a person taking a post, not a reorganization. But
  ``designated`` cannot simply be dropped: ``2022-EO-003`` reads *"The Department of
  Information Technology and Telecommunications shall hereafter be designated as
  the Office of Technology and Innovation"*, which is the DoITT-to-OTI hand-off and
  the single most important edge in the corpus.
* ``transferred`` — 61 hits, and ``salaries or wages transferred to "UNCLAIMED"
  account`` is money, not government.

So the filter is not a list of words. **Every side of a sentence must land on a span
that pass one or pass two already found.** A person keeps their post, money keeps
moving, and neither produces an edge, because neither names an agency on both sides.
A sentence that names no agency is not thrown away — it goes to the review list,
where it tells a person which body is missing from
``lineage/data/extra_agencies.json``.

**Nearest span wins, not pass one.** Position is the grammatical signal and the pass
is not. *"There is hereby established in the Office of the Mayor an M/WBE Advisory
Committee"* names the parent first and the new body second; the new body is the one
pass two found. Preferring pass one there would record the Office of the Mayor as
the thing established. So the nearest span to the verb takes the role, and pass one
only breaks a tie at the same spot.

**The read-back promise holds.** ``full_text[start:end] == text`` for the sentence
and for every role in it, so each claim can be checked against the order it came
from.
"""

from __future__ import annotations

import re

from .mentions import (
    FOUND_BY_NAME_LIST,
    FOUND_BY_PATTERN,
    ROLE_FROM,
    ROLE_PARENT,
    ROLE_TO,
    WHY_NO_AGENCY_NAMED,
    WHY_ONE_SIDE_ONLY,
    AgencyEvent,
    Mention,
    ProposedName,
    Role,
    UnresolvedEvent,
)
from .normalize import collapse_spaces, strip_possessive

# --------------------------------------------------------------------------- #
# What an order can do to an agency                                             #
# --------------------------------------------------------------------------- #

KIND_ESTABLISHES = "establishes"
KIND_CONTINUES = "continues"
KIND_ABOLISHES = "abolishes"
KIND_RENAMES = "renames"
KIND_TRANSFERS_TO = "transfers_to"
KIND_MERGES_INTO = "merges_into"
KIND_SUCCEEDS = "succeeds"

# The verb as the order writes it, and what it means. Counts are over the 3,202
# orders that carry text, measured 2026-08-31.
VERB_KINDS: dict[str, str] = {
    "established": KIND_ESTABLISHES,      # 305
    "reestablished": KIND_ESTABLISHES,    # 5
    "re-established": KIND_ESTABLISHES,   # 1
    "created": KIND_ESTABLISHES,          # 51
    "constituted": KIND_ESTABLISHES,      # 5
    "continued": KIND_CONTINUES,          # 125
    "abolished": KIND_ABOLISHES,          # 13
    "renamed": KIND_RENAMES,              # 7
    "redesignated": KIND_RENAMES,         # 1
    "designated": KIND_RENAMES,           # 258, and see the module docstring
    "transferred": KIND_TRANSFERS_TO,     # 61
    "merged": KIND_MERGES_INTO,           # 0 in the passive shape, kept for the pre-1974 volumes
    "consolidated": KIND_MERGES_INTO,     # 1
}

# Which sides a sentence must fill before it counts as an edge. "parent" is never
# required — plenty of bodies are established without being placed inside another.
REQUIRED_ROLES: dict[str, tuple[str, ...]] = {
    KIND_ESTABLISHES: (ROLE_TO,),
    KIND_CONTINUES: (ROLE_TO,),
    KIND_ABOLISHES: (ROLE_FROM,),
    KIND_RENAMES: (ROLE_FROM, ROLE_TO),
    KIND_TRANSFERS_TO: (ROLE_FROM, ROLE_TO),
    KIND_MERGES_INTO: (ROLE_FROM, ROLE_TO),
    KIND_SUCCEEDS: (ROLE_FROM, ROLE_TO),
}

# The kinds that can place a result inside another body.
_TAKES_A_PARENT = frozenset({KIND_ESTABLISHES, KIND_CONTINUES})

# --------------------------------------------------------------------------- #
# Finding the sentences                                                         #
# --------------------------------------------------------------------------- #

_VERB_ALTERNATION = "|".join(
    sorted(VERB_KINDS, key=len, reverse=True))

# The passive shape, in both of its forms:
#   "<name> is hereby established"        /  "there is hereby established <name>"
#   "<name> shall hereafter be designated as <name>"
# The "(?:\w+\s+and\s+)?" arm carries "shall be continued and established within
# the Office of Technology and Innovation" (2022-EO-003), where two verbs share one
# "shall be".
VERB_RE = re.compile(
    r"(?:\b(?:is|are|was|were)\s+(?:hereby\s+|hereafter\s+)?"
    r"|\bshall\s+(?:hereby\s+|hereafter\s+)?be\s+(?:\w+\s+and\s+)?)"
    r"(?P<verb>" + _VERB_ALTERNATION + r")\b",
    re.IGNORECASE,
)

# Two shapes that carry no passive verb but say the same thing.
NAMED_SHAPES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\bshall\s+(?:hereafter\s+)?be\s+known\s+as\b", re.IGNORECASE),
     "known as", KIND_RENAMES),
    (re.compile(r"\bsuccessor\s+(?:to|of)\b", re.IGNORECASE),
     "successor", KIND_SUCCEEDS),
)

# Where one sentence stops. A stray full stop ("Dept. of Health") cuts the window
# short, which loses an edge; it never invents one, so short is the safe way to be
# wrong. "Section" is matched with its capital only, because the lower-case
# "section 20-f of the Charter" is a citation inside a sentence, not a new one.
#
# A BLANK LINE IS NOT A BOUNDARY, however much it looks like one. Pulling text out
# of a PDF drops blank lines into the middle of sentences: 2022-EO-003 reads
# "...and Telecommunications\n\nshall hereafter be designated as the Office of
# Technology and Innovation", and treating that gap as a full stop cuts the subject
# off its own verb and loses the DoITT-to-OTI edge outright. Measured, not imagined.
_BOUNDARY_RE = re.compile(r"[.;§]|(?<![A-Za-z])Section\s")

# How far a sentence may reach on either side of its verb.
WINDOW = 300

# A body named straight after "in"/"within" is the PARENT, not the thing acted on.
_PARENT_LEAD_RE = re.compile(
    r"\b(?:in|within|inside)\s+(?:the\s+|a\s+|an\s+)?$", re.IGNORECASE)

# --------------------------------------------------------------------------- #
# Coordinated subject lists                                                     #
# --------------------------------------------------------------------------- #
#
# One verb can govern several bodies at once. 2022-EO-003 § 3 is the clearest one
# in the corpus:
#
#     The Office of Cyber Command established pursuant to section 20-j of the
#     Charter, the Office of Data Analytics established pursuant to section 20-f of
#     the Charter and the Office of Information Privacy established pursuant to
#     section 8-h of the Charter shall be continued and established within the
#     Office of Technology and Innovation.
#
# Three offices move into OTI, not one. Taking only the nearest name records the
# Office of Information Privacy and silently drops Cyber Command and Data
# Analytics — two thirds of what the order actually did.
#
# So the nearest name is kept, and each name before it joins as well while the text
# BETWEEN them is nothing but list glue. Four things make a gap glue, and each one
# is there to stop a different wrong join:
#
#   * It is short. A list item's modifier is a phrase, not a clause.
#   * It holds no sentence break (`.` `;` `:` `§`). Those end the list.
#   * It holds a coordinator — a comma, "and", or "&". Without one, the two names
#     are simply next to each other and are not a list at all.
#   * It holds NO finite verb. This is the load-bearing one. Without it,
#     "...revoking the Board of Estimate, and the Committee on the Judiciary
#     established thereunder is hereby abolished" reads as abolishing both, because
#     "is hereby revoked and the" is otherwise perfectly good glue.
#
# A single bare comma is not enough. Two names joined by nothing but a comma are as
# likely to be an apposition as a list: 1955-EO-022 reads "The Division of Analysis,
# Bureau of the Budget, together with its functions and staff, is hereby transferred
# to...", where the second name is the first one's PARENT and nothing is moving but
# the Division. So a chain has to earn its length one of two ways:
#
#   * a gap uses "and" or "&" — the ordinary "A, B and C"; or
#   * the chain reaches three names. A comma chain that long is a list, and no
#     apposition in the corpus runs to three.
#
# 1976-EO-063 needs the second arm. It abolishes four offices, and its final "and"
# sits INSIDE a pass-two span ("Office of Downtown Brooklyn Development and the
# Upper Manhattan Planning and Development Office") rather than in a gap, so the
# first arm alone would record one office out of four.
#
# The rule is asked ONLY of names BEFORE the verb, which is where the corpus
# evidence is. An "established a Committee ... and the Council" tail after the verb
# is far likelier to be the body being advised than a second body being created, so
# that side still takes one name.
_LIST_GLUE_MAX = 90
_LIST_BREAK_RE = re.compile(r"[.;:§]")
_LIST_COORDINATOR_RE = re.compile(r",|&|\band\b", re.IGNORECASE)
# The coordinator a two-name chain must use to count as a list.
_LIST_AND_RE = re.compile(r"&|\band\b", re.IGNORECASE)
# How many names a comma-only chain needs before it counts as a list anyway.
_LIST_MIN_WITHOUT_AND = 3
_LIST_FINITE_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|be|been|being|shall|will|would|may|must|can|has|have"
    r"|had|does|do|did)\b", re.IGNORECASE)
_LIST_GLUE_TAIL_RE = re.compile(
    r"(?:,\s*|\s+)(?:and\s+|&\s+)?(?:the\s+|an?\s+)?$", re.IGNORECASE)


def is_list_glue(gap: str) -> bool:
    """True when the text between two names joins them into one list."""
    if not gap or len(gap) > _LIST_GLUE_MAX:
        return False
    if _LIST_BREAK_RE.search(gap):
        return False
    if not _LIST_COORDINATOR_RE.search(gap):
        return False
    if _LIST_FINITE_VERB_RE.search(gap):
        return False
    return bool(_LIST_GLUE_TAIL_RE.search(gap))


def _list_before(text: str, before: list[_Span]) -> list[_Span]:
    """The nearest name before the verb, plus every list item joined to it.

    Returns them in the order the order writes them, so the roles read the way the
    sentence does. A pair joined by nothing but a comma is an apposition, not a
    list, and falls back to the nearest name alone.
    """
    if not before:
        return []
    chosen = [before[-1]]
    joined_by_and = False
    for span in reversed(before[:-1]):
        gap = text[span.end:chosen[0].start]
        if not is_list_glue(gap):
            break
        joined_by_and = joined_by_and or bool(_LIST_AND_RE.search(gap))
        chosen.insert(0, span)
    if joined_by_and or len(chosen) >= _LIST_MIN_WITHOUT_AND:
        return chosen
    return [before[-1]]


def sentence_bounds(text: str, verb_start: int, verb_end: int) -> tuple[int, int]:
    """The stretch of text one verb governs: back to a stop, forward to the next.

    Capped at :data:`WINDOW` characters each way, so a page with no punctuation left
    in it after OCR cannot swallow the whole order.
    """
    lo = max(0, verb_start - WINDOW)
    floor = lo
    for m in _BOUNDARY_RE.finditer(text, lo, verb_start):
        floor = m.end()
    hi = min(len(text), verb_end + WINDOW)
    ceiling = hi
    m = _BOUNDARY_RE.search(text, verb_end, hi)
    if m:
        ceiling = m.start()
    return floor, ceiling


# --------------------------------------------------------------------------- #
# Reusing the spans the earlier passes found                                    #
# --------------------------------------------------------------------------- #

class _Span:
    """One agency span from an earlier pass, ready to be given a role."""

    __slots__ = ("start", "end", "text", "name", "agency_id", "found_by")

    def __init__(self, start: int, end: int, text: str, name: str,
                 agency_id: str | None, found_by: str) -> None:
        self.start = start
        self.end = end
        self.text = text
        self.name = name
        self.agency_id = agency_id
        self.found_by = found_by

    def as_role(self, role: str) -> Role:
        return Role(role=role, agency_id=self.agency_id, name=self.name,
                    start=self.start, end=self.end, text=self.text,
                    found_by=self.found_by)


def spans_by_order(mentions: list[Mention], proposed: list[ProposedName]
                   ) -> dict[str, list[_Span]]:
    """Gather both passes' spans, per order, in position order.

    The letterhead finds are left out. The stationery at the head of the page names
    the Office of the Mayor 2,560 times and none of them is a reference to it, so
    letting them take a role would attach the office to sentences it has nothing to
    do with.
    """
    out: dict[str, list[_Span]] = {}
    for m in mentions:
        if m.in_letterhead:
            continue
        out.setdefault(m.eo_id, []).append(_Span(
            m.start, m.end, m.text,
            strip_possessive(collapse_spaces(m.text)).strip(),
            m.agency_id, FOUND_BY_NAME_LIST))
    for p in proposed:
        out.setdefault(p.eo_id, []).append(_Span(
            p.start, p.end, p.text, p.name, None, FOUND_BY_PATTERN))
    for spans in out.values():
        # Position first; a known name beats a proposed one at the same spot, which
        # is the only place the pass is allowed to decide anything.
        spans.sort(key=lambda s: (s.start, s.end,
                                  0 if s.found_by == FOUND_BY_NAME_LIST else 1))
    return out


def _in_window(spans: list[_Span], lo: int, hi: int) -> list[_Span]:
    return [s for s in spans if s.start >= lo and s.end <= hi]


def _is_parent(text: str, span: _Span, window_start: int) -> bool:
    """True when "in"/"within" sits immediately in front of this name.

    Asked on both sides of the verb. After it, the name is the body the result is
    placed inside. Before it, the name is the wrapper rather than the subject:
    *"The Department of Commerce and the Mayor's Reception Committee, in the Office
    of the Mayor, are hereby consolidated"* is about the committee, not about the
    Office of the Mayor.
    """
    return bool(_PARENT_LEAD_RE.search(text[window_start:span.start]))


# --------------------------------------------------------------------------- #
# One sentence                                                                  #
# --------------------------------------------------------------------------- #

def _roles_for(kind: str, text: str, spans: list[_Span],
               floor: int, verb_start: int, verb_end: int, ceiling: int,
               spoken_for: set[tuple[int, int]]) -> list[Role]:
    """Give each side of one sentence to the nearest agency named on that side.

    ``spoken_for`` holds the spans that an EARLIER sentence in the same order
    already handed a ``to`` role. They are barred from taking a ``from`` role here.
    That one rule clears out the commonest wrong reading in the corpus, the deputy
    mayor roster: *"One shall be designated the First Deputy Mayor, one shall be
    designated the Deputy Mayor for Operations, one shall be designated ..."*. Each
    post is a separate appointment, but the nearest name before the second verb is
    the FIRST post, so the sentence reads as a rename of one deputy mayor into
    another. It is not one. Measured over the corpus, this rule and the "in/within"
    rule below take the rename count from 105 down to the real ones.
    """
    before = [s for s in _in_window(spans, floor, verb_start)
              if not _is_parent(text, s, floor)]
    after = _in_window(spans, verb_end, ceiling)

    parent: _Span | None = None
    if kind in _TAKES_A_PARENT:
        for s in after:
            if _is_parent(text, s, verb_end):
                parent = s
                break
    # A name claimed as the parent can never also be the thing established.
    after_body = [s for s in after if s is not parent]

    free = [s for s in before if (s.start, s.end) not in spoken_for]

    roles: list[Role] = []
    if kind in (KIND_ESTABLISHES, KIND_CONTINUES):
        # Both shapes at once: the existential puts the new body after the verb,
        # the commoner shape puts it before. Prefer after, fall back to before —
        # and the before side can be a whole list of bodies sharing one verb.
        made = ([after_body[0]] if after_body else _list_before(text, before))
        roles.extend(sp.as_role(ROLE_TO) for sp in made)
        if parent is not None:
            roles.append(parent.as_role(ROLE_PARENT))
        return roles

    if kind == KIND_ABOLISHES:
        # "The Board of Estimate is hereby abolished" — and the existential form,
        # "there is hereby abolished the Board of X", reads the other way.
        gone = _list_before(text, free)
        if not gone and after_body:
            gone = [after_body[0]]
        roles.extend(sp.as_role(ROLE_FROM) for sp in gone)
        return roles

    if kind in (KIND_TRANSFERS_TO, KIND_MERGES_INTO):
        # Several bodies can be transferred or merged in one sentence; only one
        # place receives them.
        roles.extend(sp.as_role(ROLE_FROM) for sp in _list_before(text, free))
        if after_body:
            roles.append(after_body[0].as_role(ROLE_TO))
        return roles

    # renames / succeeds map ONE body onto one other. A list on either side is far
    # likelier to be a mis-read than a real multi-way rename, so both stay single.
    if free:
        roles.append(free[-1].as_role(ROLE_FROM))
    if after_body:
        roles.append(after_body[0].as_role(ROLE_TO))
    return roles


def find_in_record(eo_id: str, text: str, spans: list[_Span]
                   ) -> tuple[list[AgencyEvent], list[UnresolvedEvent]]:
    """Every reorganization sentence in one order, sorted into kept and to-review."""
    events: list[AgencyEvent] = []
    unresolved: list[UnresolvedEvent] = []

    found: list[tuple[int, int, str, str]] = []
    for m in VERB_RE.finditer(text):
        verb = m.group("verb").lower()
        found.append((m.start("verb"), m.end("verb"), verb, VERB_KINDS[verb]))
    for pattern, verb, kind in NAMED_SHAPES:
        for m in pattern.finditer(text):
            found.append((m.start(), m.end(), verb, kind))
    found.sort()

    spoken_for: set[tuple[int, int]] = set()
    for verb_start, verb_end, verb, kind in found:
        floor, ceiling = sentence_bounds(text, verb_start, verb_end)
        roles = _roles_for(kind, text, spans, floor, verb_start, verb_end, ceiling,
                           spoken_for)
        for r in roles:
            if r.role == ROLE_TO:
                spoken_for.add((r.start, r.end))
        by_role = {r.role for r in roles}
        missing = [r for r in REQUIRED_ROLES[kind] if r not in by_role]
        if not missing:
            events.append(AgencyEvent(
                eo_id=eo_id, kind=kind, verb=verb,
                start=floor, end=ceiling, text=text[floor:ceiling],
                roles=tuple(roles)))
            continue
        unresolved.append(UnresolvedEvent(
            eo_id=eo_id, verb=verb, start=floor, end=ceiling,
            text=text[floor:ceiling],
            why=WHY_NO_AGENCY_NAMED if not roles else WHY_ONE_SIDE_ONLY))

    return events, unresolved


def find_events(records: list[dict], mentions: list[Mention],
                proposed: list[ProposedName]
                ) -> tuple[list[AgencyEvent], list[UnresolvedEvent]]:
    """Run pass three over every order that has text.

    Reads the spans the two earlier passes produced and nothing else, so it depends
    only on its inputs and gives the same answer every time.
    """
    by_order = spans_by_order(mentions, proposed)
    events: list[AgencyEvent] = []
    unresolved: list[UnresolvedEvent] = []
    for r in records:
        eo_id = r["eo_id"]
        found, missed = find_in_record(
            eo_id, r.get("full_text") or "", by_order.get(eo_id, []))
        events.extend(found)
        unresolved.extend(missed)
    return events, unresolved


def events_by_kind(events: list[AgencyEvent]) -> dict[str, int]:
    """How many events of each kind, for the report."""
    counts: dict[str, int] = {}
    for e in events:
        counts[e.kind] = counts.get(e.kind, 0) + 1
    return counts
