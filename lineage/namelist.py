"""Build the list of agency names we search for.

Two sources, and the second is the one that matters for tracing lineage:

* **The registry** (``../ny-gov-web-registry/data/registry.json``) — 317 agencies,
  and every one of them is marked ``status: "active"``. Its ``relations`` /
  ``mandates`` / ``founding_date`` / ``dissolution_date`` fields are filled in for
  ZERO agencies (measured 2026-08-26). It is a list of what exists now, nothing
  more, so it cannot name an agency that was shut down: DoITT, "Office of
  Information Technology", and "City of New York Technology Steering Committee" are
  all missing from it.
* **The extra-agencies file** (``lineage/data/extra_agencies.json``) — written by
  hand, committed, and the only place a shut-down body can live. :mod:`discover`
  proposes entries for it; a person approves each one. This is the file that makes
  lineage tracing possible.

One name shape is generated rather than read. The registry writes 48 bodies as
"Mayor's Office of X" or "Mayor's Office for X", and the orders very often write
the same body as "Office of X" — measured over the corpus, 520 spans across 35
names are an exact registry name minus the word "Mayor's", and "Office of
Management and Budget" alone is 166 of them. :func:`mayoral_short_name` strips the
prefix and the short spelling joins the list under the SAME agency id, so
"Mayor's Office of X" stays the canonical name and "Office of X" becomes another
way of spelling it.

Short names need a rule. 168 of the registry's 592 names are 5 characters or
fewer, and matching those without regard to case is ruinous — measured over the
corpus, ``LAW`` gets 4,949 hits when case is ignored against 14 when it is not, and
``UP`` gets 221 against 5. ``lineage/data/name_rules.json`` records the decision
for each one, and :func:`names_needing_a_rule` lets a test fail the build when a
frequently-hit short name is missing from it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .normalize import normalize_name

# How a name is matched against the order text.
MATCH_ANY_CASE = "any-case"    # the default for names longer than the short cutoff
MATCH_SAME_CASE = "same-case"  # the text must use the same capitals as the name
MATCH_NEVER = "never"          # never match this name on its own; longer names still do
MATCH_RULES = frozenset({MATCH_ANY_CASE, MATCH_SAME_CASE, MATCH_NEVER})

FROM_REGISTRY = "registry"
FROM_EXTRA_FILE = "extra-agencies"
# Not read from any file: built by :func:`mayoral_short_name` from a name one of
# the two real sources supplied. Carried through to `Mention.source`, so every find
# the rule is responsible for says so in the output.
FROM_MAYORAL_VARIANT = "mayoral-variant"

# "Mayor's Office of Operations" -> "Office of Operations". Both apostrophes,
# because the registry writes a straight one and a hand-written entry may not; an
# optional leading "the" because a source name sometimes carries it. Only "of" and
# "for" — every mayoral office in the registry uses one or the other, and a wider
# pattern would start cutting names that are not this shape.
_MAYORAL_PREFIX_RE = re.compile(
    r"^(?:the\s+)?mayor(?:'|\u2019)?s\s+(office\s+(?:of|for)\s+\S.*)$",
    re.IGNORECASE)

# Used only when data/name_rules.json is missing or says nothing about a name.
DEFAULT_SHORT_NAME_LENGTH = 5
DEFAULT_MAX_HITS_WITHOUT_A_RULE = 200


@dataclass(frozen=True)
class KnownName:
    """One agency name we look for, and the agency or agencies it belongs to.

    ``name`` is the name spelled exactly as its source wrote it — that is what we
    search for. ``agency_ids`` holds more than one id only when two agencies share
    a name once tidied up; such a name is still matched, but it is never pinned to
    a single agency (see :mod:`lineage.scan`).
    """

    name: str
    normalized: str
    agency_ids: tuple[str, ...]
    match: str
    source: str

    @property
    def shared(self) -> bool:
        """True when this name belongs to more than one agency."""
        return len(self.agency_ids) > 1

    def as_dict(self) -> dict:
        return {"name": self.name, "normalized": self.normalized,
                "agency_ids": list(self.agency_ids), "match": self.match,
                "source": self.source}


@dataclass
class NameList:
    """Every agency name we know, plus the rules that decided how each is matched."""

    names: tuple[KnownName, ...]
    short_name_length: int
    max_hits_without_a_rule: int
    rules: dict[str, dict]

    @property
    def searchable(self) -> tuple[KnownName, ...]:
        """The names we actually look for (those ruled ``never`` are left out)."""
        return tuple(n for n in self.names if n.match != MATCH_NEVER)

    def ids_for(self, normalized: str) -> tuple[str, ...]:
        """The agency ids for a tidied-up name, or an empty tuple."""
        for n in self.names:
            if n.normalized == normalized:
                return n.agency_ids
        return ()

    def counts(self) -> dict[str, int]:
        return {
            "names_total": len(self.names),
            "names_we_search_for": len(self.searchable),
            "names_never_matched": sum(
                1 for n in self.names if n.match == MATCH_NEVER),
            "names_needing_same_case": sum(
                1 for n in self.names if n.match == MATCH_SAME_CASE),
            "from_registry": sum(1 for n in self.names if n.source == FROM_REGISTRY),
            "from_extra_file": sum(
                1 for n in self.names if n.source == FROM_EXTRA_FILE),
            "from_mayoral_variant": sum(
                1 for n in self.names if n.source == FROM_MAYORAL_VARIANT),
            "shared_by_two_agencies": sum(1 for n in self.names if n.shared),
        }

    def generated_names_by_agency(self) -> dict[str, list[str]]:
        """Every generated spelling, filed under each agency it belongs to.

        :mod:`lineage.agencies` carries these into the published ``other_names``,
        so a reader downstream can label "Office of Operations" without having to
        know this rule exists. Sorted, so a re-run writes the same file.
        """
        out: dict[str, list[str]] = {}
        for n in self.names:
            if n.source != FROM_MAYORAL_VARIANT:
                continue
            for agency_id in n.agency_ids:
                out.setdefault(agency_id, []).append(n.name)
        return {k: sorted(v) for k, v in sorted(out.items())}


def mayoral_short_name(name: str) -> str | None:
    """``"Mayor's Office of Operations"`` -> ``"Office of Operations"``, else None.

    The orders write a mayoral office both ways and mean one body, so the short
    spelling belongs to the same agency. One direction only: this strips the
    prefix and never adds it. "Office of Collective Bargaining" and "Office of the
    Comptroller" are registry names that are NOT mayoral offices, and inventing a
    "Mayor's" spelling for them would hand their text to the wrong body — measured,
    the reverse rule matches nothing in the corpus anyway.
    """
    found = _MAYORAL_PREFIX_RE.match(name.strip())
    return found.group(1).strip() if found else None


def _names_of(agency: dict) -> list[str]:
    """Every way one agency's record spells its name.

    ``other_names`` entries are ``{"name": ..., "note": ...}`` in the registry, not
    plain strings — the same shape ``supersede.build_registry_index`` handles.
    Plain strings are accepted too, so a hand-written extra-agencies entry can use
    the simpler form.
    """
    alt = [o.get("name") if isinstance(o, dict) else o
           for o in (agency.get("other_names") or [])]
    return [n for n in (agency.get("name"), agency.get("short_name"), *alt) if n]


def load_registry(path: Path) -> list[dict]:
    """Read registry.json and return the agency records in it.

    Accepts both the wrapped (``{"entities": [...]}``) and plain-list shapes.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    agencies = data.get("entities") if isinstance(data, dict) else data
    if not isinstance(agencies, list):
        raise SystemExit(f"registry at {path} has no 'entities' list")
    return agencies


def load_extra_agencies(path: Path) -> list[dict]:
    """Read the hand-written extra-agencies file, or return nothing if it is absent.

    A missing file is normal before the first review pass, not an error.
    """
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    agencies = data.get("entities") if isinstance(data, dict) else data
    return agencies if isinstance(agencies, list) else []


def load_rules(path: Path) -> tuple[dict[str, dict], int, int]:
    """Read name_rules.json -> (rules, short_name_length, max_hits_without_a_rule)."""
    if not path.exists():
        return (
            {}, DEFAULT_SHORT_NAME_LENGTH, DEFAULT_MAX_HITS_WITHOUT_A_RULE)
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = data.get("rules") or {}
    for name, rule in rules.items():
        how = rule.get("match")
        if how not in MATCH_RULES:
            raise SystemExit(
                f"name_rules.json: name {name!r} asks for unknown match "
                f"{how!r}; expected one of {sorted(MATCH_RULES)}")
    return (rules,
            int(data.get("short_name_length", DEFAULT_SHORT_NAME_LENGTH)),
            int(data.get("max_hits_without_a_rule",
                         DEFAULT_MAX_HITS_WITHOUT_A_RULE)))


def how_to_match(name: str, rules: dict[str, dict], short_name_length: int) -> str:
    """Decide how one name is matched against the order text.

    A rule written for the name always wins. Otherwise a name at or below
    ``short_name_length`` characters needs the same capitals (an acronym written in
    capitals should be matched in capitals), and anything longer matches whatever
    the case.
    """
    rule = rules.get(name)
    if rule:
        return rule["match"]
    return MATCH_SAME_CASE if len(name) <= short_name_length else MATCH_ANY_CASE


def build(
    registry_agencies: list[dict],
    extra_agencies: list[dict],
    rules: dict[str, dict],
    short_name_length: int = DEFAULT_SHORT_NAME_LENGTH,
    max_hits_without_a_rule: int = DEFAULT_MAX_HITS_WITHOUT_A_RULE,
) -> NameList:
    """Build the name list from both sources.

    The result is always in the same order: longest name first, then alphabetical.
    That is also the order the scanner needs, because it is what makes the longest
    name win at any given spot in the text.

    A name that appears in both sources is kept once, and the extra file's agency id
    is added to the registry's rather than replacing it — a shared name belongs to
    both, and that is reported rather than settled by guessing.

    The mayoral short spellings are added LAST, after both real sources have been
    read, and a spelling either source already supplied is left alone. Both halves
    of that matter. The first spelling of a key wins the spelling and the source
    label, so a real name would lose its own provenance to a generated one that ran
    first. And adding a generated agency id to a key a real source already holds
    would turn a good name into a shared one, which :mod:`lineage.scan` refuses to
    pin to any agency — the name would go from right to useless without a word.
    """
    ids_by_normalized: dict[str, list[str]] = {}
    spelling_by_normalized: dict[str, str] = {}
    source_by_normalized: dict[str, str] = {}
    generated_by_key: dict[str, str] = {}

    for agencies, source in ((registry_agencies, FROM_REGISTRY),
                             (extra_agencies, FROM_EXTRA_FILE)):
        for agency in agencies:
            agency_id = agency.get("id")
            if not agency_id:
                continue
            for name in _names_of(agency):
                key = normalize_name(name)
                if not key:
                    continue
                ids = ids_by_normalized.setdefault(key, [])
                if agency_id not in ids:
                    ids.append(agency_id)
                # First one wins the spelling and the source label, so a registry
                # name keeps registry provenance when the extra file repeats it.
                spelling_by_normalized.setdefault(key, name)
                source_by_normalized.setdefault(key, source)

    # Last, and never over the top of a name a real source supplied.
    for key, spelling in list(spelling_by_normalized.items()):
        short = mayoral_short_name(spelling)
        if not short:
            continue
        short_key = normalize_name(short)
        if not short_key or short_key in spelling_by_normalized:
            continue
        # Two mayoral offices could in principle shorten to one spelling. Union the
        # ids the way a shared name is handled everywhere else, rather than letting
        # whichever came first quietly win.
        ids = ids_by_normalized.setdefault(short_key, [])
        for agency_id in ids_by_normalized[key]:
            if agency_id not in ids:
                ids.append(agency_id)
        generated_by_key[short_key] = short

    for short_key, short in generated_by_key.items():
        spelling_by_normalized[short_key] = short
        source_by_normalized[short_key] = FROM_MAYORAL_VARIANT

    names = [
        KnownName(
            name=spelling_by_normalized[key],
            normalized=key,
            agency_ids=tuple(ids_by_normalized[key]),
            match=how_to_match(
                spelling_by_normalized[key], rules, short_name_length),
            source=source_by_normalized[key],
        )
        for key in ids_by_normalized
    ]
    names.sort(key=lambda n: (-len(n.name), n.name))
    return NameList(tuple(names), short_name_length, max_hits_without_a_rule, rules)


def names_needing_a_rule(
    name_list: NameList, hit_counts: dict[str, int]) -> list[tuple[str, int]]:
    """Short names hit more than ``max_hits_without_a_rule`` times with no rule.

    The safety check. A short name that matches thousands of times is almost
    certainly an ordinary English word (``LAW``, ``UP``, ``CORE``), and it must not
    slip in on the default. A test calls this and fails when the list is not empty,
    which forces the decision into ``name_rules.json`` where a person can read it.
    Most-frequent first.
    """
    out = [
        (n.name, hit_counts.get(n.name, 0))
        for n in name_list.names
        if len(n.name) <= name_list.short_name_length
        and n.name not in name_list.rules
        and hit_counts.get(n.name, 0) > name_list.max_hits_without_a_rule
    ]
    out.sort(key=lambda t: (-t[1], t[0]))
    return out


def unused_rules(name_list: NameList) -> list[str]:
    """Rules written for a name that is not in the list.

    A rule goes dead in two ways. It can name something the registry dropped, or it
    can be hidden: names are deduplicated after tidying, so "LAW" and "Law" become
    one entry and only the first spelling keeps its rule. Harmless when both rules
    agree, silent and wrong when they do not — so a test reports the list rather
    than letting it rot.
    """
    live = {n.name for n in name_list.names}
    return sorted(name for name in name_list.rules if name not in live)
