"""Pass four — order-to-order supersession, read out of the orders themselves.

One order revokes, amends, or supersedes another by CITING it:
``Executive Order No. 21, dated March 3, 1978, is hereby revoked``. The order
carrying the sentence is the ACTOR, the cited order is the TARGET. A second, older
source says the same thing backwards: the archival ``XREF: AMENDED BY 'EO 18) 1978"``
stamp that the cleaner moved into ``dropped_header``, where the order carrying the
stamp is the target and the cited order is the actor.

Ported from ``src/nyc_executive_orders/supersede.py`` and
``src/nyc_executive_orders/identity.py`` rather than imported, because this package
stands alone (README: "never imports ``nyc_executive_orders``"). Keep the two in
step — if the patterns drift, the two halves of the project stop agreeing on which
orders act on which.

Two things here are NOT what ``supersede.py`` does, and both are deliberate:

* **Every order is read, not just 1974 onward.** ``supersede.py`` runs over
  ``corpus/eo.json`` alone; this runs over the pre-1974 volumes as well.
* **An id must match exactly.** Only 526 of the 978 pre-1974 orders carry the plain
  ``YYYY-EO-NNN`` shape. The rest carry a suffix -- a printed label
  (``1966-EO-019B``) or a page anchor (``1966-EO-019-p007``) -- or a
  different series (``EM``, ``AM``) that :func:`mint_eo_id` cannot produce, and 86
  id stems are shared by more than one order — ``1966-EO-019`` alone covers 50.
  So a citation whose minted id is absent, but whose stem does match orders in the
  corpus, is recorded as a dangle saying ``suffix-variant``. It is never attached to
  one of them. A guess is a bug.

``in_effect`` is not computed here. That field belongs to the corpus records, and
this package writes only into its own output.
"""

from __future__ import annotations

import re

from .mentions import (
    REASON_IMPLAUSIBLE_YEAR,
    REASON_NOT_IN_CORPUS,
    REASON_NO_YEAR,
    REASON_SUFFIX_VARIANT,
    SOURCE_BODY_CITATION,
    SOURCE_HEADER_XREF,
    OrderDangle,
    OrderEdge,
)

# --------------------------------------------------------------------------- #
# The eo_id minter, ported from identity.mint_eo_id                             #
# --------------------------------------------------------------------------- #

def mint_eo_id(year: int, number: int | str | None, is_emergency: bool) -> str:
    """Build the synthetic id for one order — the corpus's own naming scheme.

    A regular order's number is a plain integer sequence and is padded to three
    digits, so ``42`` becomes ``2024-EO-042``. An emergency order's number label is
    kept LITERALLY, because two administrations number them differently and the
    dotted Mamdani-era labels ``1.37`` and ``2.37`` are different orders signed the
    same day. Padding those would collapse them into one.
    """
    series = "EEO" if is_emergency else "EO"
    if number is None:
        return f"{year}-{series}-UNK"
    label = str(number).strip()
    if is_emergency:
        num = label
    elif label.isdigit():
        num = f"{int(label):03d}"
    else:
        # Unexpected, but keep it verbatim rather than crash: the row stays
        # identifiable and shows up as a dangle instead of vanishing.
        num = label
    return f"{year}-{series}-{num}"


# The stem of an order id: everything up to the letter or ``-NN`` suffix the
# pre-1974 volumes add. Used ONLY to explain a miss, never to attach an edge.
_ID_STEM_RE = re.compile(r"^(\d{4}-(?:EEO|EO)-\d+)")


def id_stem(eo_id: str) -> str | None:
    """The part of an id a minted id could match, or ``None`` for another series."""
    m = _ID_STEM_RE.match(eo_id)
    return m.group(1) if m else None


# --------------------------------------------------------------------------- #
# Verb classes                                                                  #
# --------------------------------------------------------------------------- #

# Verbs that take an order out of force. "replaced" covers "revoked and replaced".
REVOKE_VERBS = frozenset({"revoked", "rescinded", "superseded", "repealed", "replaced"})
# An amendment is an edge too, but it is a different thing from a revocation.
AMEND_VERBS = frozenset({"amended"})
# Extension language. Counted, then skipped: an emergency order expires by
# operation of law, and expiry is not supersession.
EXTEND_VERBS = frozenset({"extended"})

_ALL_VERBS = REVOKE_VERBS | AMEND_VERBS | EXTEND_VERBS

_MONTHS = (
    "January February March April May June July August September October "
    "November December"
).split()
_MONTH_RE = "|".join(_MONTHS)

# One citation. The year comes from the cited DATE, or from the "of YYYY" form. A
# citation carrying no year cannot be resolved and is never guessed by number
# alone: per-mayor numbering resets, so the same number names several orders.
_CITE_RE = re.compile(
    r"(?P<emg>Emergency\s+)?Executive\s+Order\s+"
    r"(?:No\.?|Nos\.?|Number)?\s*(?P<num>\d{1,4})"
    r"(?:\s*,?\s*dated\s+(?:" + _MONTH_RE + r")\.?\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*"
    r"(?P<yr>\d{4})"
    r"|\s+of\s+(?P<ofyr>\d{4}))?",
    re.IGNORECASE,
)

# The operative clause: the cited order is the subject of a passive verb. Asking
# for "is"/"are" keeps the order carrying the sentence as the actor, and leaves out
# the backwards construction "VERBED by <another order>".
_VERB_RE = re.compile(
    r"\b(?:is|are|hereby\s+is|hereby\s+are)\s+(?:hereby\s+)?"
    r"(?P<verb>" + "|".join(sorted(_ALL_VERBS)) + r")\b",
    re.IGNORECASE,
)

# How far back from a verb its citations may sit. Long enough for a list joined by
# semicolons, short enough not to reach into the sentence before it.
_CLAUSE_LOOKBACK = 400
_CLAUSE_BOUNDARY_RE = re.compile(r"§|(?<![A-Za-z])Section\s")

# A plausible signing year. A citation below the corpus floor is real and worth
# recording as a dangle; a "year" like 1474 is OCR damage and must never mint an id.
_MIN_CITED_YEAR = 1950
_MAX_CITED_YEAR = 2099

# A citation is section-scoped — a partial edit, not a whole revocation — when a
# "section/paragraph/subdivision ... of" phrase sits immediately in front of it.
_SECTION_SCOPE_RE = re.compile(
    r"\b(?:section|sections|paragraph|subdivision|§)\b[^.;§\n]{0,60}\bof\s*$",
    re.IGNORECASE,
)

# The archival routing stamp, as OCR read it. The ")" between number and year is a
# scanning artefact of the stamp itself; a space or a comma is accepted too.
_XREF_RE = re.compile(
    r"XREF\s*:?\s*(?P<verb>REVOKED|RESCINDED|SUPERSEDED|REPEALED|AMENDED|REPLACED)\s+BY\s+"
    r"['\"‘’“”]?\s*(?:E\.?E\.?O|EO)\s*(?P<num>\d{1,4})[)\s,]+(?P<yr>\d{4})",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Resolving one citation                                                        #
# --------------------------------------------------------------------------- #

def _resolve_citation(m: re.Match) -> tuple[str | None, str]:
    """Mint the cited order's id, scoped by the year in the citation itself."""
    yr = m.group("yr") or m.group("ofyr")
    if not yr:
        return None, REASON_NO_YEAR
    year = int(yr)
    if not (_MIN_CITED_YEAR <= year <= _MAX_CITED_YEAR):
        return None, REASON_IMPLAUSIBLE_YEAR
    return mint_eo_id(year, int(m.group("num")), bool(m.group("emg"))), ""


def _cite_label(m: re.Match) -> str:
    """A readable name for a citation we could not resolve to an id."""
    return f"{'EEO' if m.group('emg') else 'EO'}-{m.group('num')}"


def _why_not_found(target: str, corpus_ids: set[str], stems: dict[str, list[str]]) -> str:
    """Say WHY a minted id is not in the corpus: a near miss, or simply absent."""
    stem = id_stem(target)
    if stem and stems.get(stem):
        return REASON_SUFFIX_VARIANT
    return REASON_NOT_IN_CORPUS


def build_stem_index(corpus_ids: set[str]) -> dict[str, list[str]]:
    """Map an id stem to the orders that carry it, so a near miss can be named.

    ``1966-EO-019`` maps to all 50 orders the volumes filed under that number.
    Used to explain a dangle, never to pick one of them.
    """
    stems: dict[str, list[str]] = {}
    for eo_id in corpus_ids:
        stem = id_stem(eo_id)
        if stem and stem != eo_id:
            stems.setdefault(stem, []).append(eo_id)
    for ids in stems.values():
        ids.sort()
    return stems


# --------------------------------------------------------------------------- #
# The two edge sources                                                          #
# --------------------------------------------------------------------------- #

def extract_body_edges(
    actor_id: str, text: str, *, corpus_ids: set[str], stems: dict[str, list[str]]
) -> tuple[list[OrderEdge], list[OrderDangle], int]:
    """Edges from the operative language in one order.

    Anchored on the verb: each ``is/are [hereby] VERBED`` governs the citations in
    its own clause. Returns the edges, the citations that went nowhere, and how many
    extensions were counted and skipped.
    """
    edges: list[OrderEdge] = []
    dangles: list[OrderDangle] = []
    extensions = 0
    cites = list(_CITE_RE.finditer(text))
    for vm in _VERB_RE.finditer(text):
        verb = vm.group("verb").lower()
        lo = max(0, vm.start() - _CLAUSE_LOOKBACK)
        boundary = 0
        for bm in _CLAUSE_BOUNDARY_RE.finditer(text, lo, vm.start()):
            boundary = bm.end()
        floor = max(lo, boundary)
        for c in [c for c in cites if floor <= c.start() < vm.start()]:
            if verb in EXTEND_VERBS:
                extensions += 1
                continue
            target, reason = _resolve_citation(c)
            if target is None:
                dangles.append(OrderDangle(actor_id, _cite_label(c), verb,
                                           SOURCE_BODY_CITATION, reason))
                continue
            if target == actor_id:  # an order citing itself is not an edge
                continue
            if target not in corpus_ids:
                dangles.append(OrderDangle(
                    actor_id, target, verb, SOURCE_BODY_CITATION,
                    _why_not_found(target, corpus_ids, stems)))
                continue
            partial = bool(
                _SECTION_SCOPE_RE.search(text[max(0, c.start() - 60):c.start()]))
            edges.append(OrderEdge(actor_id, target, verb, SOURCE_BODY_CITATION,
                                   partial))
    return edges, dangles, extensions


def extract_xref_edges(
    target_id: str, dropped_header: str, *, corpus_ids: set[str],
    stems: dict[str, list[str]]
) -> tuple[list[OrderEdge], list[OrderDangle]]:
    """Edges from the archival stamp in one order's dropped header.

    ``X AMENDED BY Y`` means Y acted on X, so the order carrying the stamp is the
    TARGET here, the other way round from a body citation.
    """
    edges: list[OrderEdge] = []
    dangles: list[OrderDangle] = []
    for m in _XREF_RE.finditer(dropped_header or ""):
        verb = m.group("verb").lower()
        actor = mint_eo_id(int(m.group("yr")), int(m.group("num")), False)
        if actor == target_id:
            continue
        if actor not in corpus_ids:
            dangles.append(OrderDangle(
                actor, target_id, verb, SOURCE_HEADER_XREF,
                _why_not_found(actor, corpus_ids, stems)))
            continue
        edges.append(OrderEdge(actor, target_id, verb, SOURCE_HEADER_XREF))
    return edges, dangles


def extract(records: list[dict]) -> tuple[list[OrderEdge], list[OrderDangle], int]:
    """Run over every order and gather both edge sources.

    Depends on nothing but the records handed to it, so it gives the same answer
    every time. A citation repeated inside one order makes one edge, not two.
    """
    corpus_ids = {r["eo_id"] for r in records}
    stems = build_stem_index(corpus_ids)
    edges: list[OrderEdge] = []
    dangles: list[OrderDangle] = []
    extensions = 0
    seen_edges: set[tuple[str, str, str, str, bool]] = set()
    seen_dangles: set[tuple[str, str, str, str, str]] = set()

    for r in records:
        eo_id = r["eo_id"]
        b_edges, b_dangles, ext = extract_body_edges(
            eo_id, r.get("full_text") or "", corpus_ids=corpus_ids, stems=stems)
        x_edges, x_dangles = extract_xref_edges(
            eo_id, r.get("dropped_header") or "", corpus_ids=corpus_ids, stems=stems)
        extensions += ext
        for d in (*b_dangles, *x_dangles):
            key = (d.actor, d.target, d.verb, d.source, d.reason)
            if key in seen_dangles:
                continue
            seen_dangles.add(key)
            dangles.append(d)
        for e in (*b_edges, *x_edges):
            key = (e.actor, e.target, e.verb, e.source, e.partial)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(e)

    return edges, dangles, extensions
