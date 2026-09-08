"""What one run records, and how it is written to disk.

Follows the ``corpus/supersession.json`` pattern exactly: one wrapping object with
a ``generated_by`` line saying what made it, a count next to every list, and
separate lists for what worked and what did not, where each failure says why.
Records are flat and hold plain values only, so the file stays easy to grep and to
diff.

**The promise, and it holds for both passes:** ``full_text[start:end] == text``,
exactly, character for character. ``text`` is what the order literally says,
newlines and all — pulling text out of a PDF wraps agency names across lines
constantly, so ``"Cyber\\nCommand"`` is an ordinary find, not a fault. Every record
can therefore be checked against the order it came from, and nothing here has to be
taken on trust.

Grouping needs the tidied form instead, so a proposed name also carries ``name``
(single-spaced, no trailing possessive). A known-name match needs no equivalent:
its ``agency_id`` already gathers every spelling under one agency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import agencies as agencies_mod

# Which pass found it.
FOUND_BY_NAME_LIST = "known-name"   # matched a name we already knew
FOUND_BY_PATTERN = "new-name"       # looks like an agency name, but is not on the list

# Whether a known name pinned down one agency.
ONE_AGENCY = "one-agency"
SEVERAL_AGENCIES = "several-agencies"

WHY_NOT_ON_THE_LIST = "not-on-the-list"
WHY_UNREADABLE = "unreadable"
WHY_JUST_A_NOUN = "just-a-noun"
WHY_ALREADY_MATCHED = "already-matched-by-name"

# Which side of a reorganization an agency stands on. "parent" is the body a new
# office is placed INSIDE ("established in the Office of the Mayor"), which is a
# different fact from the one being established.
ROLE_FROM = "from"      # the body the order acts ON
ROLE_TO = "to"          # the body the order produces, or hands work to
ROLE_PARENT = "parent"  # the body the result sits within

# Why a reorganization sentence produced no edge.
WHY_NO_AGENCY_NAMED = "no-agency-named"   # neither side named a body we know of
WHY_ONE_SIDE_ONLY = "one-side-only"       # one side named a body, the other did not

# Where an order-to-order edge was read.
SOURCE_BODY_CITATION = "body-citation"   # operative language in the order
SOURCE_HEADER_XREF = "header-xref"       # the archival stamp in the dropped header

# Why a citation produced no order-to-order edge.
REASON_NO_YEAR = "no-year"                    # nothing to scope the number by
REASON_IMPLAUSIBLE_YEAR = "implausible-year"  # OCR damage, such as "1474"
REASON_NOT_IN_CORPUS = "not-in-corpus"        # a real order we do not hold
REASON_SUFFIX_VARIANT = "suffix-variant"      # the stem matches, the exact id does not


@dataclass(frozen=True)
class Mention:
    """One agency name we already knew, found at a known spot in an order."""

    eo_id: str
    start: int
    end: int
    text: str
    agency_id: str | None
    agency_ids: tuple[str, ...]
    found_by: str
    pins_down: str
    source: str
    # True when the find sits in the letterhead at the head of the page rather
    # than in the order — see :mod:`lineage.letterhead`. Kept, not deleted: the
    # span still answers the read-back promise, and pass two still needs it.
    in_letterhead: bool = False

    def as_dict(self) -> dict:
        d = {"eo_id": self.eo_id, "start": self.start, "end": self.end,
             "text": self.text, "agency_id": self.agency_id,
             "found_by": self.found_by, "pins_down": self.pins_down,
             "source": self.source}
        # Only carried when it says something; a one-agency name just repeats itself.
        if self.pins_down == SEVERAL_AGENCIES:
            d["agency_ids"] = list(self.agency_ids)
        # Same rule: written only when true, so the ordinary find stays short.
        if self.in_letterhead:
            d["in_letterhead"] = True
        return d


@dataclass(frozen=True)
class ProposedName:
    """A phrase shaped like an agency name that is not on the list yet."""

    eo_id: str
    start: int
    end: int
    text: str
    name: str
    found_by: str
    why: str

    def as_dict(self) -> dict:
        return {"eo_id": self.eo_id, "start": self.start, "end": self.end,
                "text": self.text, "name": self.name,
                "found_by": self.found_by, "why": self.why}


@dataclass(frozen=True)
class Discarded:
    """A count of the phrases thrown out before they reached the review list."""

    name: str
    why: str
    count: int

    def as_dict(self) -> dict:
        return {"name": self.name, "why": self.why, "count": self.count}


@dataclass(frozen=True)
class Role:
    """One agency standing on one side of a reorganization sentence.

    The span is not found here. It is a span one of the two earlier passes already
    recorded, reused — so ``agency_id`` is filled in when the known-name pass found
    it, and ``None`` when only the new-name pass did. A ``None`` is not a failure:
    it says the body is not on the name list yet, which is exactly the case the
    extra-agencies file exists to fix.
    """

    role: str
    agency_id: str | None
    name: str
    start: int
    end: int
    text: str
    found_by: str

    def as_dict(self) -> dict:
        return {"role": self.role, "agency_id": self.agency_id, "name": self.name,
                "start": self.start, "end": self.end, "text": self.text,
                "found_by": self.found_by}


@dataclass(frozen=True)
class AgencyEvent:
    """One thing an order does to an agency: establish, rename, abolish, transfer.

    ``start``/``end``/``text`` cover the SENTENCE, so the claim can be read back out
    of the order and judged. Each role carries its own span for the same reason.
    """

    eo_id: str
    kind: str
    verb: str
    start: int
    end: int
    text: str
    roles: tuple[Role, ...]

    @property
    def fully_resolved(self) -> bool:
        """True when every side pins down an agency the name list already knows."""
        return bool(self.roles) and all(r.agency_id for r in self.roles)

    @property
    def same_agency(self) -> bool:
        """True when both sides land on ONE agency id — a rename already absorbed.

        ``2022-EO-003`` renames DoITT to the Office of Technology and Innovation,
        and both names resolve to ``oti``, because
        ``lineage/data/extra_agencies.json`` deliberately files the old name under
        the new agency. So the edge is real and its two ends are the same node. It
        is counted rather than hidden: it says the name list has already made the
        judgement the graph would otherwise have to make.
        """
        sides = [r for r in self.roles if r.role in (ROLE_FROM, ROLE_TO)]
        ids = {r.agency_id for r in sides}
        return len(sides) == 2 and len(ids) == 1 and None not in ids

    def as_dict(self) -> dict:
        return {"eo_id": self.eo_id, "kind": self.kind, "verb": self.verb,
                "start": self.start, "end": self.end, "text": self.text,
                "fully_resolved": self.fully_resolved,
                "roles": [r.as_dict() for r in self.roles]}


@dataclass(frozen=True)
class UnresolvedEvent:
    """A reorganization sentence that named no agency we could attach it to.

    Kept, never quietly dropped: this is the review list that tells a person which
    bodies are missing from ``lineage/data/extra_agencies.json``.
    """

    eo_id: str
    verb: str
    start: int
    end: int
    text: str
    why: str

    def as_dict(self) -> dict:
        return {"eo_id": self.eo_id, "verb": self.verb, "start": self.start,
                "end": self.end, "text": self.text, "why": self.why}


@dataclass(frozen=True)
class OrderEdge:
    """One order acting on another: ``actor`` revokes or amends ``target``."""

    actor: str
    target: str
    verb: str
    source: str
    partial: bool = False

    def as_dict(self) -> dict:
        return {"actor": self.actor, "target": self.target, "verb": self.verb,
                "source": self.source, "partial": self.partial}


@dataclass(frozen=True)
class OrderDangle:
    """A citation we resolved to an order we do not hold, and why."""

    actor: str
    target: str
    verb: str
    source: str
    reason: str

    def as_dict(self) -> dict:
        return {"actor": self.actor, "target": self.target, "verb": self.verb,
                "source": self.source, "reason": self.reason}


@dataclass
class RunResult:
    """Everything one run produced, before it is turned into JSON or Markdown."""

    mentions: list[Mention] = field(default_factory=list)
    proposed: list[ProposedName] = field(default_factory=list)
    discarded: list[Discarded] = field(default_factory=list)
    # The registry's own record for each agency the run matched — see
    # :mod:`lineage.agencies`. Empty when the known-name pass did not run.
    agencies: list[dict] = field(default_factory=list)
    # What the orders DO to agencies, and the sentences we could not attach.
    agency_events: list[AgencyEvent] = field(default_factory=list)
    unresolved_events: list[UnresolvedEvent] = field(default_factory=list)
    # What the orders do to each other.
    order_edges: list[OrderEdge] = field(default_factory=list)
    order_dangles: list[OrderDangle] = field(default_factory=list)
    extensions_skipped: int = 0
    corpus_records: int = 0
    orders_read: int = 0
    orders_without_text: int = 0
    name_list_counts: dict = field(default_factory=dict)
    passes_run: tuple[str, ...] = ()

    def name_counts(self, proposed: list[ProposedName]) -> dict[str, int]:
        """How often each tidied name came up, so wrapped spellings count as one."""
        counts: dict[str, int] = {}
        for p in proposed:
            counts[p.name] = counts.get(p.name, 0) + 1
        return counts


def build_payload(result: RunResult, *, generated_by: str,
                  review_from: int) -> dict:
    """Assemble the object that gets written to disk.

    Splits the proposed names at ``review_from`` into a ``to_review`` list (the set
    a person works through) and ``rare`` (recorded, not queued). Nothing is thrown
    away — a one-off body stays findable, following the project rule that a gap is
    stated rather than papered over.

    Always the same output: every list is sorted on a key that cannot tie, so
    running again on unchanged input produces an identical file.
    """
    counts = result.name_counts(result.proposed)
    orders_by_name: dict[str, set[str]] = {}
    for p in result.proposed:
        orders_by_name.setdefault(p.name, set()).add(p.eo_id)

    to_review, rare = [], []
    for name, n in counts.items():
        row = {"name": name, "times_seen": n,
               "example_orders": sorted(orders_by_name[name])[:5]}
        (to_review if n >= review_from else rare).append(row)
    to_review.sort(key=lambda r: (-r["times_seen"], r["name"]))
    rare.sort(key=lambda r: r["name"])

    mentions = sorted(result.mentions, key=lambda m: (m.eo_id, m.start, m.end))
    proposed = sorted(result.proposed, key=lambda p: (p.eo_id, p.start, p.end))
    discarded = sorted(result.discarded, key=lambda d: (-d.count, d.name, d.why))
    letterhead_count = sum(1 for m in mentions if m.in_letterhead)

    events = sorted(result.agency_events,
                    key=lambda e: (e.eo_id, e.start, e.end, e.verb, e.kind))
    unresolved = sorted(result.unresolved_events,
                        key=lambda e: (e.eo_id, e.start, e.end, e.verb))
    order_edges = sorted(result.order_edges,
                         key=lambda e: (e.actor, e.target, e.verb, e.source,
                                        e.partial))
    order_dangles = sorted(result.order_dangles,
                           key=lambda d: (d.actor, d.target, d.verb, d.source,
                                          d.reason))
    events_by_kind: dict[str, int] = {}
    for e in events:
        events_by_kind[e.kind] = events_by_kind.get(e.kind, 0) + 1

    return {
        "generated_by": generated_by,
        "corpus_records": result.corpus_records,
        "orders_read": result.orders_read,
        "orders_without_text": result.orders_without_text,
        "passes_run": list(result.passes_run),
        "name_list": result.name_list_counts,
        "review_from": review_from,
        # Carried so the file stands on its own: a reader handed an `agency_id`
        # can name it without also holding the registry. See lineage/agencies.py.
        "agency_count": len(result.agencies),
        "agencies": result.agencies,
        "agency_description_credit": agencies_mod.DESCRIPTION_CREDIT,
        "mention_count": len(mentions),
        # The split of that same list: what sits in the order, and what sits in
        # the letterhead. Both are in `mentions`; nothing is held back.
        "body_mention_count": len(mentions) - letterhead_count,
        "letterhead_count": letterhead_count,
        "proposed_count": len(proposed),
        "to_review_count": len(to_review),
        "rare_count": len(rare),
        "mentions": [m.as_dict() for m in mentions],
        "proposed": [p.as_dict() for p in proposed],
        "to_review": to_review,
        "rare": rare,
        "discarded": [d.as_dict() for d in discarded],
        # What the orders do to agencies. `fully_resolved` counts the events where
        # EVERY side pins down an agency the name list already knows; the rest name
        # at least one body that is not on the list yet, and say so rather than
        # guess an id for it.
        "agency_event_count": len(events),
        "fully_resolved_event_count": sum(1 for e in events if e.fully_resolved),
        "same_agency_event_count": sum(1 for e in events if e.same_agency),
        "agency_events_by_kind": dict(sorted(events_by_kind.items())),
        "unresolved_event_count": len(unresolved),
        "agency_events": [e.as_dict() for e in events],
        "unresolved_events": [e.as_dict() for e in unresolved],
        # What the orders do to each other.
        "order_edge_count": len(order_edges),
        "order_dangle_count": len(order_dangles),
        "extensions_skipped": result.extensions_skipped,
        "order_edges": [e.as_dict() for e in order_edges],
        "order_dangles": [d.as_dict() for d in order_dangles],
    }


def dumps(payload: dict) -> str:
    """Write it out the way every other side file in this project is written."""
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
