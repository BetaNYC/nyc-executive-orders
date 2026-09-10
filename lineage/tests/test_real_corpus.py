"""Checks against the real committed corpus and the real registry.

The one that matters. Everything else in this suite tests a rule against a made-up
string; this pins what the rules actually do over 3,202 real orders, so a change in
behaviour shows up as a failing number rather than as a quiet drift in the output.

Skips cleanly when the corpus or the registry is missing, the same way
``tests/test_gpp.py`` guards its own committed-data check.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from lineage import agencies as agencies_mod
from lineage import citations as citations_mod
from lineage import discover as discover_mod
from lineage import namelist as nl
from lineage import records as records_mod
from lineage import reorg as reorg_mod
from lineage import scan as scan_mod
from lineage.mentions import (
    ROLE_FROM,
    ROLE_PARENT,
    ROLE_TO,
    WHY_NO_AGENCY_NAMED,
    WHY_ONE_SIDE_ONLY,
    RunResult,
    build_payload,
    dumps,
)
from lineage.normalize import normalize_name as normalize

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = [REPO_ROOT / "corpus" / "eo.json", REPO_ROOT / "corpus" / "eo_pre1974.json"]
REGISTRY = REPO_ROOT.parent / "ny-gov-web-registry" / "data" / "registry.json"
DESCRIPTIONS = REGISTRY.parent / "descriptions.json"
EXTRA = REPO_ROOT / "lineage" / "data" / "extra_agencies.json"
RULES = REPO_ROOT / "lineage" / "data" / "name_rules.json"

# Measured 2026-09-10. A change here is a real change in behaviour, not a flake.
EXPECT_ORDERS = 3269
# Every order now carries text. The 67 `_No text available_` placeholders the
# earlier measurement counted were filled in by the VLM re-OCR of the bound volumes.
EXPECT_WITH_TEXT = 3269
EXPECT_WITHOUT_TEXT = 0
EXPECT_REGISTRY_NAMES = 592
# The 48 mayoral short spellings the name list builds on top of those 592.
EXPECT_MAYORAL_VARIANTS = 48
# 194 of the registry's 317 — the corpus names a little over half of what exists
# today — plus `doitt`, which only the extra-agencies file holds. 12 of the 194 are
# reached only by the mayoral short spelling: the orders never write their
# "Mayor's" name at all.
EXPECT_AGENCIES_FOUND = 195
EXPECT_FROM_REGISTRY = 194
# The mayoral stationery, and what is left once it is set aside. Of 2976 finds of
# "Office of the Mayor", 2681 are the letterhead at the head of the page and 295
# are the order actually naming the office.
EXPECT_LETTERHEAD = 2681
EXPECT_MAYOR_IN_BODY = 295
MAYOR_ID = "office-of-the-mayor"

# Pass three, measured 2026-09-10. 400 sentences link two bodies; 131 of them pin
# down a registry agency on every side, and the rest name at least one body that is
# not on the name list yet. 497 more named nothing we could attach and wait in the
# review list.
EXPECT_EVENTS = 400
EXPECT_EVENTS_FULLY_RESOLVED = 131
EXPECT_UNRESOLVED_EVENTS = 497
EXPECT_EVENTS_BY_KIND = {
    "establishes": 296, "continues": 47, "transfers_to": 23, "renames": 20,
    "abolishes": 12, "merges_into": 1, "succeeds": 1,
}

# Pass four, measured 2026-09-10, over all 3,269 orders that carry text.
# `supersede.py` reads only the 2,291 orders of corpus/eo.json.
EXPECT_ORDER_EDGES = 353
EXPECT_ORDER_DANGLES = 139
EXPECT_EXTENSIONS_SKIPPED = 1680

needs_corpus = pytest.mark.skipif(
    not all(p.exists() for p in CORPUS), reason="committed corpus not present")
needs_registry = pytest.mark.skipif(
    not REGISTRY.exists(),
    reason="ny-gov-web-registry sibling checkout not present")


@pytest.fixture(scope="module")
def corpus():
    return records_mod.load_corpus(CORPUS)


@pytest.fixture(scope="module")
def name_list():
    """What a real run uses: the registry plus the hand-written extra agencies."""
    rules, short_name_length, max_hits = nl.load_rules(RULES)
    return nl.build(nl.load_registry(REGISTRY), nl.load_extra_agencies(EXTRA),
                    rules, short_name_length, max_hits)


@pytest.fixture(scope="module")
def registry_only():
    """The registry on its own, for asking what the registry does and does not
    know. Several facts below are about the REGISTRY, not about the run."""
    rules, short_name_length, max_hits = nl.load_rules(RULES)
    return nl.build(nl.load_registry(REGISTRY), [], rules, short_name_length,
                    max_hits)


@pytest.fixture(scope="module")
def all_four_passes(corpus, name_list):
    """Every pass over the whole corpus, run once and shared by the tests below."""
    orders, skipped, mentions, proposed, discarded = _both_passes(corpus, name_list)
    events, unresolved = reorg_mod.find_events(orders, mentions, proposed)
    edges, dangles, extensions = citations_mod.extract(orders)
    return {"orders": orders, "skipped": skipped, "mentions": mentions,
            "proposed": proposed, "discarded": discarded, "events": events,
            "unresolved": unresolved, "order_edges": edges,
            "order_dangles": dangles, "extensions": extensions}


def _both_passes(corpus, name_list):
    """Run both passes over the whole corpus and hand back everything."""
    orders, skipped = records_mod.with_text(corpus)
    mentions = scan_mod.scan(orders, name_list)
    proposed, discarded = discover_mod.find_new_names(
        orders, frozenset(n.normalized for n in name_list.names), mentions)
    return orders, skipped, mentions, proposed, discarded


# --------------------------------------------------------------------------- #
# What the corpus looks like                                                    #
# --------------------------------------------------------------------------- #


@needs_corpus
def test_order_and_placeholder_counts(corpus):
    orders, skipped = records_mod.with_text(corpus)
    assert len(corpus) == EXPECT_ORDERS
    assert len(orders) == EXPECT_WITH_TEXT
    assert skipped == EXPECT_WITHOUT_TEXT


@needs_corpus
def test_the_two_corpus_files_share_no_order_ids(corpus):
    ids = [r["eo_id"] for r in corpus]
    assert len(set(ids)) == len(ids)


# --------------------------------------------------------------------------- #
# The name list                                                                 #
# --------------------------------------------------------------------------- #


@needs_registry
def test_the_registry_yields_the_expected_number_of_names(registry_only):
    """Counted apart, because the list holds names from more than one place. The
    592 are what the registry actually spells; the 48 are the mayoral short
    spellings built from them, and they are the only names in the list that no
    file contains."""
    counts = registry_only.counts()
    assert counts["from_registry"] == EXPECT_REGISTRY_NAMES
    assert counts["from_mayoral_variant"] == EXPECT_MAYORAL_VARIANTS
    assert counts["names_total"] == EXPECT_REGISTRY_NAMES + EXPECT_MAYORAL_VARIANTS


@needs_registry
def test_the_extra_agencies_file_adds_to_what_the_registry_knows(name_list,
                                                                registry_only):
    """The hand-written file is meant to be additive, never a replacement."""
    extra = name_list.counts()["names_total"] - registry_only.counts()["names_total"]
    assert extra > 0
    assert name_list.counts()["from_extra_file"] == extra
    # The extra file adds no mayoral office today, so the built spellings are the
    # same on both sides and the subtraction above is a fair one.
    assert (name_list.counts()["from_mayoral_variant"]
            == registry_only.counts()["from_mayoral_variant"])


@needs_registry
def test_the_registry_holds_no_agency_that_was_shut_down(registry_only):
    """The fact the whole design rests on. Asks the REGISTRY alone, on purpose —
    the extra-agencies file exists precisely to fill these in. If this ever fails,
    the registry has gained historical coverage and the new-name pass should be
    rethought."""
    for gone in ("doitt",
                 "department of information technology and telecommunications",
                 "office of information technology",
                 "city of new york technology steering committee"):
        assert registry_only.ids_for(gone) == (), f"{gone} is now in the registry"


@needs_registry
def test_doitt_is_its_own_agency_and_not_an_alias_of_oti(name_list):
    """DoITT and OTI are one office in law and two in time, and a lineage needs
    both ends to be separate things. Filing the old name under `oti` turns the
    2022 rename into an edge whose two ends are the same node, and there is then
    nothing left to say that anything changed.

    The registry cannot hold this: it marks all 317 agencies active. MODA does know
    the pair — record NYC_GOID_000382 lists both values under its alternate-or-
    former fields — and fixing that upstream would give OTI the DoITT names as
    other_names and undo the split. Those two names must stay off the `oti` row."""
    assert name_list.ids_for("doitt") == ("doitt",)
    assert name_list.ids_for(
        "department of information technology and telecommunications") == ("doitt",)
    assert name_list.ids_for("office of technology and innovation") == ("oti",)


@needs_registry
def test_every_agency_in_the_registry_is_marked_active(name_list):
    agencies = nl.load_registry(REGISTRY)
    assert {a.get("status") for a in agencies} == {"active"}


# --------------------------------------------------------------------------- #
# The promise, over every real find                                             #
# --------------------------------------------------------------------------- #


@needs_corpus
@needs_registry
def test_every_find_can_be_read_back_out_of_the_order(corpus, name_list):
    """``full_text[start:end] == text``, character for character, on 23k+ finds."""
    orders, _, mentions, proposed, _ = _both_passes(corpus, name_list)
    text = {r["eo_id"]: r["full_text"] for r in orders}
    bad = [x for x in (*mentions, *proposed)
           if text[x.eo_id][x.start:x.end] != x.text]
    assert bad == []


# --------------------------------------------------------------------------- #
# The letterhead                                                                #
# --------------------------------------------------------------------------- #


@needs_corpus
@needs_registry
def test_the_mayoral_letterhead_is_marked_and_the_body_references_are_not(
        corpus, name_list):
    """Both numbers matter. The first says the stationery is off the counts; the
    second says the rule did not take the real references with it."""
    orders, _ = records_mod.with_text(corpus)
    mentions = scan_mod.scan(orders, name_list)
    marked = [m for m in mentions if m.in_letterhead]
    assert len(marked) == EXPECT_LETTERHEAD
    assert {m.agency_id for m in marked} == {MAYOR_ID}
    body = [m for m in mentions
            if m.agency_id == MAYOR_ID and not m.in_letterhead]
    assert len(body) == EXPECT_MAYOR_IN_BODY


@needs_corpus
@needs_registry
def test_the_letterhead_is_marked_and_not_deleted(corpus, name_list):
    """Marking, not dropping, is what keeps the rest of the run still true: the
    span answers the read-back promise, the agency keeps its registry row, and
    pass two still sees the span as already matched — otherwise it would propose
    "CITY OF NEW YORK OFFICE OF THE MAYOR NEW YORK" as a new agency."""
    orders, _, mentions, _, discarded = _both_passes(corpus, name_list)
    text = {r["eo_id"]: r["full_text"] for r in orders}
    marked = [m for m in mentions if m.in_letterhead]
    assert all(text[m.eo_id][m.start:m.end] == m.text for m in marked)
    assert MAYOR_ID in agencies_mod.ids_in(mentions)
    # The two letterhead phrases pass two would otherwise put up as new agencies.
    # They reach `discarded` only because pass one still holds their spans.
    thrown_out = {d.name: d.count for d in discarded
                  if d.why == discover_mod.WHY_ALREADY_MATCHED}
    assert thrown_out["CITY OF NEW YORK OFFICE OF THE MAYOR NEW YORK"] == 776
    assert thrown_out["THE CITY OF NEW YORK OFFICE OF THE MAYOR NEW YORK"] == 1540


@needs_corpus
@needs_registry
def test_the_report_counts_add_up(corpus, name_list):
    """Every list still has a count that matches its length, and the split of the
    mentions list adds back up to the whole of it."""
    orders, skipped, mentions, proposed, discarded = _both_passes(corpus, name_list)
    result = RunResult(mentions=mentions, proposed=proposed, discarded=discarded,
                       corpus_records=len(corpus), orders_read=len(orders),
                       orders_without_text=skipped)
    payload = build_payload(result, generated_by="test", review_from=3)
    assert payload["mention_count"] == len(payload["mentions"])
    assert (payload["body_mention_count"] + payload["letterhead_count"]
            == payload["mention_count"])
    assert payload["letterhead_count"] == EXPECT_LETTERHEAD


# --------------------------------------------------------------------------- #
# The safety checks                                                             #
# --------------------------------------------------------------------------- #


@needs_corpus
@needs_registry
def test_no_frequently_found_short_name_slips_in_on_the_default(corpus, name_list):
    """The check that keeps 'LAW' (4,949 hits) and 'UP' (221) out of the output.
    A failure here means a decision is missing from name_rules.json."""
    orders, _ = records_mod.with_text(corpus)
    hits = scan_mod.name_hit_counts(scan_mod.scan(orders, name_list), name_list)
    assert nl.names_needing_a_rule(name_list, hits) == []


@needs_registry
def test_only_the_one_known_hidden_rule_goes_unused(name_list):
    """'Law' tidies down onto 'LAW', so only one of the two spellings keeps its
    rule. Both say never, so nothing changes — but the list must not grow."""
    assert nl.unused_rules(name_list) == ["Law"]


# --------------------------------------------------------------------------- #
# What a run produces                                                           #
# --------------------------------------------------------------------------- #


@needs_corpus
@needs_registry
def test_the_it_lineage_can_be_traced(corpus, name_list):
    """The job this package exists for. Before it, `establishes_entity` was filled
    in on 2 of 2,291 orders and not one DoITT mention was recorded anywhere."""
    _, _, mentions, proposed, _ = _both_passes(corpus, name_list)

    orders_by_name: dict[str, set[str]] = {}
    for p in proposed:
        orders_by_name.setdefault(p.name, set()).add(p.eo_id)
    for m in mentions:
        if m.agency_id:
            orders_by_name.setdefault(m.agency_id, set()).add(m.eo_id)

    # Two agencies, two spans of time, and one order where they meet. Every
    # spelling of the old name lands on `doitt`, which the extra-agencies file
    # supplies because the registry cannot hold a body that was renamed away.
    doitt = orders_by_name["doitt"]
    oti = orders_by_name["oti"]
    assert len(doitt) >= 55
    assert min(doitt) < "2000"          # the old name starts in the 1990s
    assert min(oti) >= "2022"           # the new one starts in 2022
    assert "2022-EO-003" in doitt       # the order that renames one to the other
    assert "2022-EO-003" in oti

    # Including the bare acronym, in both spellings the orders actually use.
    spellings = {m.text for m in mentions if m.agency_id == "doitt"}
    assert "DoITT" in spellings
    assert "DOITT" in spellings

    # And the earliest link in the chain is still only a proposal, awaiting review.
    assert orders_by_name["City of New York Technology Steering Committee"]


@needs_corpus
@needs_registry
def test_running_twice_writes_identical_bytes(corpus, name_list):
    """Same input, same output (engineering-standards §6)."""
    orders, skipped, mentions, proposed, discarded = _both_passes(corpus, name_list)

    def once():
        result = RunResult(
            mentions=mentions, proposed=proposed, discarded=discarded,
            corpus_records=len(corpus), orders_read=len(orders),
            orders_without_text=skipped, name_list_counts=name_list.counts(),
            passes_run=("known-names", "new-names"))
        return dumps(build_payload(result, generated_by="test", review_from=3))

    assert once() == once()


# --------------------------------------------------------------------------- #
# The agency records carried with the finds                                     #
# --------------------------------------------------------------------------- #


@needs_corpus
@needs_registry
def test_every_agency_id_found_can_be_named(corpus, name_list):
    """Nothing downstream should hold an id it cannot put a name to.

    This is what makes the published `corpus/mentions.json` stand on its own. A
    viewer reading it has no registry beside it, so an `agency_id` with no row in
    `agencies` would render as a bare slug — or as nothing at all.
    """
    _, _, mentions, _, _ = _both_passes(corpus, name_list)
    rows = agencies_mod.build(
        agencies_mod.ids_in(mentions), nl.load_registry(REGISTRY),
        agencies_mod.load_descriptions(DESCRIPTIONS),
        nl.load_extra_agencies(EXTRA))

    by_id = {r["id"]: r for r in rows}
    assert agencies_mod.ids_in(mentions) == set(by_id)
    missing_a_name = sorted(i for i, r in by_id.items() if not r.get("name"))
    assert missing_a_name == []


@needs_corpus
@needs_registry
def test_the_mayoral_short_spelling_reaches_the_published_row(corpus, name_list):
    """The rule is invisible downstream unless the row says so. `../nyc-eo-explorer`
    builds its agency pages from this file and nothing else, so a mention reading
    "Office of Operations" would otherwise sit on a row that never spells it.

    The canonical name is untouched: "Mayor's Office of X" is what the agency is
    called, and "Office of X" is another way of writing it."""
    _, _, mentions, _, _ = _both_passes(corpus, name_list)
    rows = agencies_mod.build(
        agencies_mod.ids_in(mentions), nl.load_registry(REGISTRY),
        agencies_mod.load_descriptions(DESCRIPTIONS),
        nl.load_extra_agencies(EXTRA), name_list.generated_names_by_agency())
    by_id = {r["id"]: r for r in rows}

    ops = by_id["mayor-s-office-of-operations"]
    assert ops["name"] == "Mayor's Office of Operations"
    assert "Office of Operations" in ops["other_names"]
    # The registry's own entries are still there, and the built one is not doubled.
    assert "OPS" in ops["other_names"]
    assert ops["other_names"].count("Office of Operations") == 1

    # Every built spelling that belongs to a found agency reaches its row.
    built = name_list.generated_names_by_agency()
    for agency_id, names in built.items():
        if agency_id not in by_id or "name" not in by_id[agency_id]:
            continue
        assert set(names) <= set(by_id[agency_id]["other_names"]), agency_id

    # And leaving the argument out changes nothing else about the rows.
    plain = agencies_mod.build(
        agencies_mod.ids_in(mentions), nl.load_registry(REGISTRY),
        agencies_mod.load_descriptions(DESCRIPTIONS), nl.load_extra_agencies(EXTRA))
    assert [r["id"] for r in plain] == [r["id"] for r in rows]
    assert "Office of Operations" not in next(
        r for r in plain if r["id"] == "mayor-s-office-of-operations")["other_names"]


@needs_corpus
@needs_registry
def test_the_short_spelling_is_matched_and_no_longer_proposed(corpus, name_list):
    """What the rule is for. "Office of Management and Budget" is the largest case
    in the corpus: the orders write it that way, the registry writes it
    "Mayor's Office of Management and Budget", and before this rule every one of
    those spans went to the review queue instead of to the agency."""
    _, _, mentions, proposed, _ = _both_passes(corpus, name_list)
    omb = [m for m in mentions
           if m.agency_id == "mayor-s-office-of-management-and-budget"]
    assert [m for m in omb
            if normalize(m.text) == "office of management and budget"]
    still_proposed = {normalize(p.name) for p in proposed}
    for gone in ("office of management and budget", "office of operations",
                 "office of contract services", "office for people with disabilities"):
        assert gone not in still_proposed, gone


@needs_corpus
@needs_registry
def test_only_the_agencies_actually_found_are_carried(corpus, name_list):
    """The registry holds 317; the corpus names far fewer.

    Shipping all of them would both add weight and say something untrue — that
    the orders mention every agency that exists today.
    """
    _, _, mentions, _, _ = _both_passes(corpus, name_list)
    rows = agencies_mod.build(
        agencies_mod.ids_in(mentions), nl.load_registry(REGISTRY),
        agencies_mod.load_descriptions(DESCRIPTIONS),
        nl.load_extra_agencies(EXTRA))

    assert len(rows) == EXPECT_AGENCIES_FOUND
    assert len(rows) < len(nl.load_registry(REGISTRY))
    # Sorted by id, so a re-run cannot reorder the file.
    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)

    # Where each row came from. A hand-written body has a row nowhere else, so
    # without the extra file `doitt` would ship as a bare slug with no name.
    registry_ids = {a["id"] for a in nl.load_registry(REGISTRY)}
    from_registry = [r for r in rows if r["id"] in registry_ids]
    assert len(from_registry) == EXPECT_FROM_REGISTRY
    doitt = next(r for r in rows if r["id"] == "doitt")
    assert doitt["name"] == ("Department of Information Technology and "
                             "Telecommunications")
    assert doitt["short_name"] == "DoITT"
    assert doitt["status"] == "renamed"
    assert doitt["dissolution_date"] == "2022-01-19"


@needs_registry
def test_a_description_is_only_carried_when_the_capture_worked():
    """Records that say why they are empty must not become empty descriptions."""
    described = agencies_mod.load_descriptions(DESCRIPTIONS)
    assert described, "descriptions.json is present but held nothing"

    refused = {"no_url", "no_about_found", "robots_disallowed", "fetch_failed",
               "extraction_empty"}
    sample = [a for a in nl.load_registry(REGISTRY)
              if described.get(a["id"], {}).get("status") in refused]
    assert sample, "expected the registry to hold at least one uncaptured agency"
    for agency in sample:
        row = agencies_mod.details_for(agency, described)
        assert row["description"] is None
        assert row["description_source_url"] is None


@needs_registry
def test_a_carried_description_is_cut_on_a_word_boundary():
    described = agencies_mod.load_descriptions(DESCRIPTIONS)
    rows = [agencies_mod.details_for(a, described)
            for a in nl.load_registry(REGISTRY)]
    carried = [r for r in rows if r["description"]]
    assert carried, "expected at least one captured description"

    for row in carried:
        text = row["description"]
        assert len(text) <= agencies_mod.DESCRIPTION_LIMIT + 1  # +1 for the ellipsis
        assert "\n" not in text
        assert row["description_source_url"], row["id"]
        if text.endswith("…"):
            assert not text[:-1].endswith(" ")


# --------------------------------------------------------------------------- #
# Pass three — what the orders do to agencies                                   #
# --------------------------------------------------------------------------- #


@needs_corpus
@needs_registry
def test_the_reorganization_counts_hold(all_four_passes):
    """A change in any of these is a real change in behaviour, not a flake."""
    events = all_four_passes["events"]
    assert len(events) == EXPECT_EVENTS
    assert sum(1 for e in events if e.fully_resolved) == EXPECT_EVENTS_FULLY_RESOLVED
    assert len(all_four_passes["unresolved"]) == EXPECT_UNRESOLVED_EVENTS
    assert reorg_mod.events_by_kind(events) == EXPECT_EVENTS_BY_KIND


@needs_corpus
@needs_registry
def test_every_event_can_be_read_back_out_of_the_order(corpus, all_four_passes):
    """The promise, extended to pass three: the sentence AND every role in it."""
    text = {r["eo_id"]: r["full_text"] for r in all_four_passes["orders"]}
    bad = []
    for e in all_four_passes["events"]:
        if text[e.eo_id][e.start:e.end] != e.text:
            bad.append(e)
        bad.extend(r for r in e.roles if text[e.eo_id][r.start:r.end] != r.text)
    for u in all_four_passes["unresolved"]:
        if text[u.eo_id][u.start:u.end] != u.text:
            bad.append(u)
    assert bad == []


@needs_corpus
@needs_registry
def test_every_event_fills_the_sides_its_kind_needs(all_four_passes):
    """An event is only recorded when every required side found an agency."""
    for e in all_four_passes["events"]:
        filled = {r.role for r in e.roles}
        assert set(reorg_mod.REQUIRED_ROLES[e.kind]) <= filled


@needs_corpus
@needs_registry
def test_no_event_takes_a_role_from_the_letterhead(all_four_passes):
    """The stationery names the Office of the Mayor 2,560 times and means none
    of them. A letterhead span taking a role would tie the office to sentences it
    has nothing to do with."""
    letterhead = {(m.eo_id, m.start, m.end)
                  for m in all_four_passes["mentions"] if m.in_letterhead}
    used = {(e.eo_id, r.start, r.end)
            for e in all_four_passes["events"] for r in e.roles}
    assert used & letterhead == set()


@needs_corpus
@needs_registry
def test_the_doitt_handoff_is_recorded_as_a_rename(all_four_passes):
    """The acceptance case. 2022-EO-003 does not use the word "renamed" — it says
    "shall hereafter be designated as" — and it is the most important edge in the
    corpus. The two ends are two different agencies, which is the whole point: an
    edge from a node to itself says nothing changed."""
    events = [e for e in all_four_passes["events"]
              if e.eo_id == "2022-EO-003" and e.kind == reorg_mod.KIND_RENAMES]
    assert len(events) == 1
    by_role = {r.role: r for r in events[0].roles}
    assert by_role[ROLE_FROM].name == ("Department of Information Technology and "
                                       "Telecommunications")
    assert by_role[ROLE_FROM].agency_id == "doitt"
    assert by_role[ROLE_TO].name == "Office of Technology and Innovation"
    assert by_role[ROLE_TO].agency_id == "oti"
    assert events[0].fully_resolved
    assert not events[0].same_agency


@needs_corpus
@needs_registry
def test_one_verb_can_move_a_whole_list_of_bodies(all_four_passes):
    """2022-EO-003 § 3: "The Office of Cyber Command ..., the Office of Data
    Analytics ... and the Office of Information Privacy ... shall be continued and
    established within the Office of Technology and Innovation."

    Three offices move, not one. Taking only the nearest name records the Office of
    Information Privacy and drops two thirds of what the order did. Each office is
    its own event, and all three share the parent and the sentence."""
    events = [e for e in all_four_passes["events"]
              if e.eo_id == "2022-EO-003" and e.kind == reorg_mod.KIND_ESTABLISHES]
    assert len(events) == 3
    moved = [r.agency_id for e in events for r in e.roles if r.role == ROLE_TO]
    assert moved == ["cyber-command", "office-of-data-analytics",
                     "office-of-information-privacy"]
    for e in events:
        parent = [r.agency_id for r in e.roles if r.role == ROLE_PARENT]
        assert parent == ["oti"]
        assert e.fully_resolved
    assert len({(e.start, e.end) for e in events}) == 1


@needs_corpus
@needs_registry
def test_no_body_is_ever_established_inside_itself(all_four_passes):
    """A parenthetical acronym restating the parent must not become the new body.

    2021-EO-063: "The Center for Creative Conflict Resolution ("the Center") is
    hereby continued and formally established within the Office of Administrative
    Trials and Hearings ("OATH")". The trailing "OATH" is a second span naming the
    parent again, and taking it records OATH inside OATH and loses the Center."""
    for e in all_four_passes["events"]:
        parent = next((r for r in e.roles if r.role == ROLE_PARENT), None)
        if parent is None:
            continue
        for r in e.roles:
            if r.role in (ROLE_FROM, ROLE_TO) and r.agency_id and parent.agency_id:
                assert r.agency_id != parent.agency_id, e.eo_id


@needs_corpus
@needs_registry
def test_no_event_ever_fills_the_same_role_twice(all_four_passes):
    """The shape everything downstream reads: one event, one body per role.

    A list sentence becomes several events rather than one event with a list in
    it, so a reader can flatten an event to a row without losing a body. The
    explorer does exactly that, and a repeated role would drop silently."""
    for e in all_four_passes["events"]:
        names = [r.role for r in e.roles]
        assert len(names) == len(set(names)), e.eo_id


@needs_corpus
@needs_registry
def test_a_list_sentence_becomes_one_event_per_body(all_four_passes):
    """Narrow on purpose. Four sentences in the corpus name a list, and every one
    is a list a person would read the same way:

    * 2022-EO-003  three offices continued and established within OTI
    * 1976-EO-063  three planning offices abolished together
    * 1965-EO-181-p261 "a Housing Policy Board and a Housing Executive Committee"
    * 1966-EO-028-p068 "the Anti-Poverty Operations Board and the Economic
      Opportunity Committee are abolished" — the case the README used to cite as
      a known miss.

    The fifth is a WRONG join, pinned here so it cannot grow unnoticed. The re-OCR
    of the bound volumes brought 1996-EO-034 into range: "the Head Start Program
    and Child Day Care Services formerly administered by the Agency for Child
    Development of HRA shall be continued". "HRA" is the parent inside an
    "administered by" phrase, not a second body being continued, and the gap before
    it is short, comma-free and verb-free, so the glue test passes on it. The "in"/
    "within" wrapper rule does not cover "administered by ... of". Recorded, not
    hidden; fixing it means widening that wrapper rule.
    """
    by_span: dict[tuple, list] = {}
    for e in all_four_passes["events"]:
        by_span.setdefault((e.eo_id, e.start, e.end, e.verb), []).append(e)
    listed = {k[0]: len(v) for k, v in by_span.items() if len(v) > 1}
    assert listed == {"2022-EO-003": 3, "1976-EO-063": 3,
                      "1965-EO-181-p261": 2, "1966-EO-028-p068": 2,
                      "1996-EO-034": 2}


@needs_corpus
@needs_registry
def test_an_apposition_moves_only_the_body_it_names(all_four_passes):
    """1955-EO-022: "The Division of Analysis, Bureau of the Budget, together with
    its functions and staff, is hereby transferred to..." names one body and its
    parent. A bare comma cannot tell an apposition from a list, so a pair joined by
    one takes the nearest name alone."""
    events = [e for e in all_four_passes["events"]
              if e.eo_id == "1955-EO-022" and e.kind == reorg_mod.KIND_TRANSFERS_TO]
    for e in events:
        assert sum(1 for r in e.roles if r.role == ROLE_FROM) == 1


@needs_corpus
@needs_registry
def test_a_deputy_mayor_roster_produces_no_rename(all_four_passes):
    """"One shall be designated the First Deputy Mayor, one shall be designated
    the Deputy Mayor for Operations, ..." is a list of appointments. Without the
    spoken-for rule it reads as a chain of renames — 84 false edges over the
    corpus, and it was the single largest wrong class measured."""
    renames = [e for e in all_four_passes["events"]
               if e.kind == reorg_mod.KIND_RENAMES]
    rosters = [e for e in renames if "one shall be designated" in e.text.lower()]
    assert rosters == []


@needs_corpus
@needs_registry
def test_a_sentence_that_named_nothing_is_kept_for_review(all_four_passes):
    """Nothing is quietly dropped. Each one says which body is missing from
    extra_agencies.json."""
    whys = {u.why for u in all_four_passes["unresolved"]}
    assert whys == {WHY_NO_AGENCY_NAMED, WHY_ONE_SIDE_ONLY}


# --------------------------------------------------------------------------- #
# Pass four — what the orders do to each other                                  #
# --------------------------------------------------------------------------- #


@needs_corpus
@needs_registry
def test_the_order_to_order_counts_hold(all_four_passes):
    assert len(all_four_passes["order_edges"]) == EXPECT_ORDER_EDGES
    assert len(all_four_passes["order_dangles"]) == EXPECT_ORDER_DANGLES
    assert all_four_passes["extensions"] == EXPECT_EXTENSIONS_SKIPPED


@needs_corpus
@needs_registry
def test_both_ends_of_every_order_edge_are_orders_we_hold(corpus, all_four_passes):
    """A dangle is the only way out. An edge always points at two real orders."""
    ids = {r["eo_id"] for r in corpus}
    for e in all_four_passes["order_edges"]:
        assert e.actor in ids
        assert e.target in ids
        assert e.actor != e.target


@needs_corpus
@needs_registry
def test_pass_four_reads_the_pre1974_volumes_that_supersede_py_never_sees(
        all_four_passes):
    """`supersede.py` runs over corpus/eo.json alone, so the 978 orders of the
    bound volumes contribute nothing to corpus/supersession.json. This pass reads
    them."""
    touched = {e.actor for e in all_four_passes["order_edges"]}
    touched |= {e.target for e in all_four_passes["order_edges"]}
    touched |= {d.actor for d in all_four_passes["order_dangles"]}
    assert any(t < "1974" for t in touched)


# --------------------------------------------------------------------------- #
# The whole payload                                                             #
# --------------------------------------------------------------------------- #


@needs_corpus
@needs_registry
def test_the_new_counts_match_the_lists_they_describe(corpus, name_list,
                                                      all_four_passes):
    payload = _full_payload(corpus, name_list, all_four_passes)
    assert payload["agency_event_count"] == len(payload["agency_events"])
    assert payload["unresolved_event_count"] == len(payload["unresolved_events"])
    assert payload["order_edge_count"] == len(payload["order_edges"])
    assert payload["order_dangle_count"] == len(payload["order_dangles"])
    assert sum(payload["agency_events_by_kind"].values()) == EXPECT_EVENTS
    assert payload["fully_resolved_event_count"] <= payload["agency_event_count"]


@needs_corpus
@needs_registry
def test_running_all_four_passes_twice_writes_identical_bytes(
        corpus, name_list, all_four_passes):
    """Same input, same output (engineering-standards §6), new lists included."""
    first = dumps(_full_payload(corpus, name_list, all_four_passes))
    second = dumps(_full_payload(corpus, name_list, all_four_passes))
    assert first == second


def _full_payload(corpus, name_list, ran) -> dict:
    """The whole payload a real run writes, built from one shared set of results."""
    result = RunResult(
        mentions=ran["mentions"], proposed=ran["proposed"],
        discarded=ran["discarded"], agency_events=ran["events"],
        unresolved_events=ran["unresolved"], order_edges=ran["order_edges"],
        order_dangles=ran["order_dangles"], extensions_skipped=ran["extensions"],
        corpus_records=len(corpus), orders_read=len(ran["orders"]),
        orders_without_text=ran["skipped"], name_list_counts=name_list.counts(),
        passes_run=("known-names", "new-names"))
    return build_payload(result, generated_by="test", review_from=3)
