"""Deterministic supersession-graph extraction (Phase C).

Populates the four graph fields the corpus schema reserves — ``supersedes``,
``superseded_by``, ``establishes_entity``, ``in_effect`` — from text ALREADY in
the corpus. **Rule-based only, no LLM, no network**, same discipline as
:mod:`clean`: an LLM inventing a revocation edge into a mayoral order is the exact
failure this project exists to fight, and it would break reproducibility
(engineering-standards §7). Every edge here is traceable to a literal citation in
``full_text`` or an OCR'd ``dropped_header`` XREF stamp.

Two edge sources, tagged in the edge list:

* ``body-citation`` — operative language in the order text:
  ``Executive Order No. {n}, dated {Month} {day}, {year}, is hereby REVOKED``
  (and rescinded / superseded / repealed / amended / replaced). The order that
  *contains* the sentence is the ACTOR; the cited order is the TARGET.
* ``header-xref`` — the relocated archival routing stamp
  ``XREF: AMENDED BY 'EO 18) 1978'`` in ``dropped_header`` (OCR-noisy). Here the
  order carrying the stamp is the TARGET and the cited order is the ACTOR
  (``X AMENDED BY Y`` ⇒ Y acts on X).

Citations resolve **year-scoped**: the cited *date's* year (never the containing
doc's year, never the number alone) plus the series (Emergency ⇒ EEO, else EO)
mint the target ``eo_id`` via :func:`identity.mint_eo_id`. Per-mayor numbering
resets and emergency numbers collide across administrations, so number-alone
matching is a proven trap (2021 de Blasio vs 2022 Adams EEO 50 are different
orders).

Scope locked for v1 (see the Phase C task brief):

* **EEO extension chains are NOT edges.** ``... is extended for five (5) days``
  is skipped (and counted), not recorded — emergency orders expire by operation
  of law; expiry logic is out of scope.
* **``in_effect`` is computed only for regular EOs.** ``False`` when a resolvable
  in-corpus order *wholly* revokes/supersedes it (an amend edge alone does not
  flip it); ``None`` otherwise (we cannot prove a 1970s order is still in force).
  Never ``True``. Emergency ``in_effect`` stays ``None`` in v1.
* **``establishes_entity`` auto-writes only exact registry matches.** Fuzzy
  candidates go to the report for human review — the same gate philosophy as the
  clean-stage title lexicon.

The per-record fields stay simple ``eo_id`` arrays (schema-preserving); the verb
and provenance of each edge live in the returned edge list (→ ``supersession.json``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .identity import mint_eo_id

# --------------------------------------------------------------------------- #
# Verb classes                                                                  #
# --------------------------------------------------------------------------- #

# Nullifying verbs: a resolvable one of these, targeting a REGULAR EO, flips its
# in_effect to False. "replaced" covers "revoked and replaced by this Order".
REVOKE_VERBS = frozenset({"revoked", "rescinded", "superseded", "repealed", "replaced"})
# Amend is an edge (§ 3-113.1 annotates amendments too) but does NOT flip in_effect.
AMEND_VERBS = frozenset({"amended"})
# Extension language — explicitly NOT a supersession edge in v1 (counted, skipped).
EXTEND_VERBS = frozenset({"extended"})

_ALL_VERBS = REVOKE_VERBS | AMEND_VERBS | EXTEND_VERBS

_MONTHS = (
    "January February March April May June July August September October "
    "November December"
).split()
_MONTH_RE = "|".join(_MONTHS)

# One citation. The year is taken from the cited DATE (`dated <Month> <day>, YYYY`)
# or the `of YYYY` form; a citation with no year is unresolvable (reported, no edge).
_CITE_RE = re.compile(
    r"(?P<emg>Emergency\s+)?Executive\s+Order\s+"
    r"(?:No\.?|Nos\.?|Number)?\s*(?P<num>\d{1,4})"
    r"(?:\s*,?\s*dated\s+(?:" + _MONTH_RE + r")\.?\s+\d{1,2}(?:st|nd|rd|th)?\s*,?\s*"
    r"(?P<yr>\d{4})"
    r"|\s+of\s+(?P<ofyr>\d{4}))?",
    re.IGNORECASE,
)

# Operative verb clause: the cited order is the grammatical SUBJECT of a passive
# verb — "<citation> ... is [hereby] VERBED". Requiring "is/are" (not bare "be"
# or "as amended by") keeps the current doc as the actor and excludes the passive
# actor construction "VERBED by <other EO>" (measured: 0 such cases for regular
# EOs under this pattern).
_VERB_RE = re.compile(
    r"\b(?:is|are|hereby\s+is|hereby\s+are)\s+(?:hereby\s+)?"
    r"(?P<verb>" + "|".join(sorted(_ALL_VERBS)) + r")\b",
    re.IGNORECASE,
)

# How far back from a verb to gather the citations it governs. Covers a
# semicolon-joined list ("EO A, dated ...; EO B, dated ...; and EO C, dated ...,
# are hereby REVOKED") without reaching into an unrelated prior sentence; the
# scan also stops at the nearest clause boundary (§ / "Section " / sentence end).
_CLAUSE_LOOKBACK = 400
_CLAUSE_BOUNDARY_RE = re.compile(r"§|(?<![A-Za-z])Section\s")

# Plausible signing-year window for a cited order. Citations below the 1974 corpus
# floor (down to the 1950s) are real and worth recording as dangles; a "year" like
# 1474 or 2994 is OCR noise and must never mint a bogus target. Bounds are wide but
# finite. Ceiling is generous (no live clock dependency in a deterministic pass).
_MIN_CITED_YEAR = 1950
_MAX_CITED_YEAR = 2099

# A citation is SECTION-SCOPED (a partial edit, not a whole revocation) when it is
# immediately preceded by a "section/paragraph/subdivision ... of" scoping phrase —
# "Section 8 of Executive Order No. 21", "Paragraph (b) of Section 8 of ...",
# "sections 1 through 8 of ...". A partial revoke does NOT flip the target's
# in_effect (only a whole revocation/supersession does); the edge is still recorded.
_SECTION_SCOPE_RE = re.compile(
    r"\b(?:section|sections|paragraph|subdivision|§)\b[^.;§\n]{0,60}\bof\s*$",
    re.IGNORECASE,
)

# Header XREF stamp. OCR-noisy: the verb token and the "EO NNN) YYYY" fragment are
# matched tolerantly (the ')' between number and year is an OCR artifact of the
# archival stamp; a plain space or comma is accepted too). Example seen on
# 1977-EO-091:  XREF: AMENDED BY 'EO 18) 1978"
_XREF_RE = re.compile(
    r"XREF\s*:?\s*(?P<verb>REVOKED|RESCINDED|SUPERSEDED|REPEALED|AMENDED|REPLACED)\s+BY\s+"
    r"['\"‘’“”]?\s*(?:E\.?E\.?O|EO)\s*(?P<num>\d{1,4})[)\s,]+(?P<yr>\d{4})",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Establishes-entity extraction                                                 #
# --------------------------------------------------------------------------- #

# "there is hereby established [within <office>,] the/a <Name> ..." — capture the
# noun phrase up to a clause/relative-clause boundary. Deliberately loose: the
# registry match gate (exact, normalized) is what admits an auto-write, so an
# over-broad capture just routes to human review rather than corrupting a field.
_ESTABLISH_RE = re.compile(
    r"there\s+is\s+hereby\s+establish(?:ed|ing)?\b"
    r"(?:\s*,?\s*within\s+[^,\n]{3,80}\s*,)?\s*"
    r"(?P<art>an?|the)\s+"
    r"(?P<name>[A-Z][^.;:\n]{3,90}?)"
    r"(?=\s*(?:,|\.|;|:|\bwhich\b|\bthat\b|\bto\s+be\b|\bheaded\b|\bconsisting\b"
    r"|\(|\bshall\b|\bwithin\b|\bof\s+the\s+City\s+of\s+New\s+York\b|$))",
    re.IGNORECASE,
)

# Trailing filler that shouldn't stay glued to a captured entity name.
_ENTITY_TRAIL_RE = re.compile(
    r"\s+(?:of|for|on|in|to|within|and|the|a|an)$", re.IGNORECASE
)


def _norm_entity(name: str) -> str:
    """Normalize an entity name for exact matching: fold case, punctuation, space.

    Strips diacritics and non-alphanumerics, collapses whitespace, lowercases. A
    leading article ("the"/"a"/"an") is dropped so "the Office of X" matches
    "Office of X". Deterministic and reversible only up to these equivalences —
    used strictly for the auto-write gate, never written back.
    """
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"^\s*(the|a|an)\s+", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def build_registry_index(registry_entities: list[dict]) -> dict[str, list[str]]:
    """Map normalized name/short_name/other_names -> list of registry ids.

    A normalized string mapping to more than one id is ambiguous (never
    auto-written). Empty/degenerate normalized keys are skipped.
    """
    index: dict[str, list[str]] = {}
    for ent in registry_entities:
        eid = ent.get("id")
        if not eid:
            continue
        # other_names entries are {"name": ..., "note": ...} dicts, not bare strings.
        alt = [o.get("name") if isinstance(o, dict) else o
               for o in (ent.get("other_names") or [])]
        names = [ent.get("name"), ent.get("short_name"), *alt]
        for nm in names:
            if not nm:
                continue
            key = _norm_entity(nm)
            if not key:
                continue
            ids = index.setdefault(key, [])
            if eid not in ids:
                ids.append(eid)
    return index


# --------------------------------------------------------------------------- #
# Edge model                                                                    #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Edge:
    """One directed supersession edge: ``actor`` acts on ``target`` via ``verb``."""

    actor: str        # eo_id of the order doing the revoking/amending
    target: str       # eo_id of the order acted upon
    verb: str         # normalized verb (revoked/amended/...)
    source: str       # "body-citation" | "header-xref"
    partial: bool = False  # section/paragraph-scoped edit (does not flip in_effect)

    def as_dict(self) -> dict:
        return {"actor": self.actor, "target": self.target, "verb": self.verb,
                "source": self.source, "partial": self.partial}


@dataclass
class Dangle:
    """A resolved citation whose target eo_id is not present in the corpus."""

    actor: str
    target: str
    verb: str
    source: str
    reason: str  # "not-in-corpus" | "no-year"

    def as_dict(self) -> dict:
        return {"actor": self.actor, "target": self.target, "verb": self.verb,
                "source": self.source, "reason": self.reason}


@dataclass
class EntityMatch:
    actor: str
    raw_name: str
    registry_id: str

    def as_dict(self) -> dict:
        return {"eo_id": self.actor, "raw_name": self.raw_name,
                "registry_id": self.registry_id}


@dataclass
class EntityCandidate:
    actor: str
    raw_name: str
    reason: str  # "no-match" | "ambiguous" | "multiple-in-order"

    def as_dict(self) -> dict:
        return {"eo_id": self.actor, "raw_name": self.raw_name, "reason": self.reason}


@dataclass
class SupersedeResult:
    """Everything a build/report needs, computed from the corpus + registry."""

    edges: list[Edge] = field(default_factory=list)
    dangles: list[Dangle] = field(default_factory=list)
    extensions_skipped: int = 0
    entity_matches: list[EntityMatch] = field(default_factory=list)
    entity_candidates: list[EntityCandidate] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Citation / edge extraction                                                    #
# --------------------------------------------------------------------------- #

def _resolve_citation(m: re.Match) -> tuple[str | None, str]:
    """Mint the target eo_id for a citation match, year-scoped.

    Returns ``(eo_id, reason)``. ``eo_id`` is None when unresolvable; ``reason`` is
    ``""`` on success, ``"no-year"`` (no date to year-scope, never guessed by number
    alone), or ``"implausible-year"`` (an OCR-garbled year like 1474/2994).
    """
    yr = m.group("yr") or m.group("ofyr")
    if not yr:
        return None, "no-year"
    year = int(yr)
    if not (_MIN_CITED_YEAR <= year <= _MAX_CITED_YEAR):
        return None, "implausible-year"
    is_emg = bool(m.group("emg"))
    return mint_eo_id(year, int(m.group("num")), is_emg), ""


def extract_body_edges(
    actor_id: str, text: str, *, corpus_ids: set[str]
) -> tuple[list[Edge], list[Dangle], int]:
    """Operative-language edges from one order's ``full_text``.

    Verb-anchored: each ``is/are [hereby] VERBED`` occurrence governs the
    citations in its clause (back to the nearest boundary, capped at
    :data:`_CLAUSE_LOOKBACK`). The order carrying the text is the actor. Extension
    verbs are counted and skipped, not recorded.
    """
    edges: list[Edge] = []
    dangles: list[Dangle] = []
    extensions = 0
    cites = list(_CITE_RE.finditer(text))
    for vm in _VERB_RE.finditer(text):
        verb = vm.group("verb").lower()
        lo = max(0, vm.start() - _CLAUSE_LOOKBACK)
        # Nearest clause boundary in the lookback window becomes the real floor.
        boundary = 0
        for bm in _CLAUSE_BOUNDARY_RE.finditer(text, lo, vm.start()):
            boundary = bm.end()
        floor = max(lo, boundary)
        governed = [c for c in cites if floor <= c.start() < vm.start()]
        for c in governed:
            if verb in EXTEND_VERBS:
                extensions += 1
                continue
            target, reason = _resolve_citation(c)
            if target is None:
                dangles.append(Dangle(actor_id, _cite_label(c), verb,
                                      "body-citation", reason))
                continue
            if target == actor_id:  # self-reference, not an edge
                continue
            if target not in corpus_ids:
                dangles.append(Dangle(actor_id, target, verb,
                                      "body-citation", "not-in-corpus"))
                continue
            partial = bool(_SECTION_SCOPE_RE.search(text[max(0, c.start() - 60):c.start()]))
            edges.append(Edge(actor_id, target, verb, "body-citation", partial))
    return edges, dangles, extensions


def _cite_label(m: re.Match) -> str:
    """Human label for an unresolvable citation (no minted id available)."""
    series = "EEO" if m.group("emg") else "EO"
    return f"{series}-{m.group('num')}"


def extract_xref_edges(
    target_id: str, dropped_header: str, *, corpus_ids: set[str]
) -> tuple[list[Edge], list[Dangle]]:
    """Archival XREF edges from one order's ``dropped_header``.

    ``X AMENDED BY Y`` ⇒ Y is the actor, the order carrying the header (X) is the
    target. Year-scoped like body citations.
    """
    edges: list[Edge] = []
    dangles: list[Dangle] = []
    for m in _XREF_RE.finditer(dropped_header or ""):
        verb = m.group("verb").lower()
        actor = mint_eo_id(int(m.group("yr")), int(m.group("num")), False)
        if actor == target_id:
            continue
        if actor not in corpus_ids:
            dangles.append(Dangle(actor, target_id, verb, "header-xref", "not-in-corpus"))
            continue
        edges.append(Edge(actor, target_id, verb, "header-xref"))
    return edges, dangles


def extract_entities(
    actor_id: str, text: str, registry_index: dict[str, list[str]]
) -> tuple[list[EntityMatch], list[EntityCandidate]]:
    """Establishes-entity auto-matches + review candidates for one regular EO.

    Exact normalized match against the registry auto-writes; no match / ambiguous
    key / more than one match in a single order routes to review candidates.
    """
    matches: list[EntityMatch] = []
    candidates: list[EntityCandidate] = []
    for m in _ESTABLISH_RE.finditer(text or ""):
        raw = _ENTITY_TRAIL_RE.sub("", " ".join(m.group("name").split())).strip()
        if not raw:
            continue
        ids = registry_index.get(_norm_entity(raw))
        if not ids:
            candidates.append(EntityCandidate(actor_id, raw, "no-match"))
        elif len(ids) > 1:
            candidates.append(EntityCandidate(actor_id, raw, "ambiguous"))
        else:
            matches.append(EntityMatch(actor_id, raw, ids[0]))
    return matches, candidates


# --------------------------------------------------------------------------- #
# Whole-corpus pass                                                             #
# --------------------------------------------------------------------------- #

def compute(records: list[dict], registry_entities: list[dict]) -> SupersedeResult:
    """Extract every edge, dangle, extension, and entity match over the corpus.

    Pure function of the corpus records + registry: deterministic and idempotent
    (re-running on the same inputs yields identical output). Does NOT mutate the
    records — use :func:`annotate_records` to write the four fields.
    """
    corpus_ids = {r["eo_id"] for r in records}
    registry_index = build_registry_index(registry_entities)
    result = SupersedeResult()
    seen_edges: set[tuple[str, str, str, str, bool]] = set()

    for r in records:
        eo_id = r["eo_id"]
        text = r.get("full_text") or ""
        b_edges, b_dangles, ext = extract_body_edges(eo_id, text, corpus_ids=corpus_ids)
        x_edges, x_dangles = extract_xref_edges(
            eo_id, r.get("dropped_header") or "", corpus_ids=corpus_ids)
        result.extensions_skipped += ext
        result.dangles.extend(b_dangles)
        result.dangles.extend(x_dangles)
        for e in (*b_edges, *x_edges):
            key = (e.actor, e.target, e.verb, e.source, e.partial)
            if key in seen_edges:  # dedupe repeated citations in one doc
                continue
            seen_edges.add(key)
            result.edges.append(e)
        if not r.get("is_emergency"):
            em, ec = extract_entities(eo_id, text, registry_index)
            result.entity_matches.extend(em)
            result.entity_candidates.extend(ec)

    return result


def annotate_records(records: list[dict], result: SupersedeResult) -> None:
    """Write the four Phase-C fields into ``records`` in place from ``result``.

    Idempotent: the arrays are rebuilt from scratch each call (never appended), so
    re-running never doubles an edge. ``in_effect`` is computed regular-only and
    conservative (see module docstring).
    """
    by_id = {r["eo_id"]: r for r in records}

    # Reset the fields we own (so a re-run is clean and deterministic).
    for r in records:
        r["supersedes"] = []
        r["superseded_by"] = []
        # establishes_entity / in_effect reset below.
        r["establishes_entity"] = None
        r["in_effect"] = None

    # Directed arrays from the edge list.
    for e in result.edges:
        by_id[e.actor]["supersedes"].append(e.target)
        by_id[e.target]["superseded_by"].append(e.actor)

    # Dedupe + stable-sort the arrays for reproducible output.
    for r in records:
        r["supersedes"] = sorted(set(r["supersedes"]))
        r["superseded_by"] = sorted(set(r["superseded_by"]))

    # Nullifying-target set: regular EOs WHOLLY revoked/superseded by a resolvable
    # order. Amend edges and section/paragraph-scoped (partial) revocations are
    # intentionally excluded — a partial repeal doesn't take the order out of force.
    revoked_targets = {
        e.target for e in result.edges if e.verb in REVOKE_VERBS and not e.partial
    }
    for r in records:
        if r.get("is_emergency"):
            r["in_effect"] = None  # emergency expiry out of v1 scope
        elif r["eo_id"] in revoked_targets:
            r["in_effect"] = False
        else:
            r["in_effect"] = None  # can't prove still-in-force; never guess True

    # establishes_entity: auto-write only when EXACTLY one entity matched in the
    # order (a scalar field; multiple matches route to review, handled in report).
    from collections import Counter
    match_counts = Counter(m.actor for m in result.entity_matches)
    for m in result.entity_matches:
        if match_counts[m.actor] == 1:
            by_id[m.actor]["establishes_entity"] = m.registry_id
