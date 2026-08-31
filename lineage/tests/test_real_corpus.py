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
from lineage import discover as discover_mod
from lineage import namelist as nl
from lineage import records as records_mod
from lineage import scan as scan_mod
from lineage.mentions import RunResult, build_payload, dumps

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = [REPO_ROOT / "corpus" / "eo.json", REPO_ROOT / "corpus" / "eo_pre1974.json"]
REGISTRY = REPO_ROOT.parent / "ny-gov-web-registry" / "data" / "registry.json"
DESCRIPTIONS = REGISTRY.parent / "descriptions.json"
EXTRA = REPO_ROOT / "lineage" / "data" / "extra_agencies.json"
RULES = REPO_ROOT / "lineage" / "data" / "name_rules.json"

# Measured 2026-08-26. A change here is a real change in behaviour, not a flake.
EXPECT_ORDERS = 3269
EXPECT_WITH_TEXT = 3202
EXPECT_WITHOUT_TEXT = 67
EXPECT_REGISTRY_NAMES = 592
# Of the registry's 317. The corpus names a little over half of what exists today.
EXPECT_AGENCIES_FOUND = 178
# The mayoral stationery, and what is left once it is set aside. Of 2810 finds of
# "Office of the Mayor", 2560 are the letterhead at the head of the page and 250
# are the order actually naming the office.
EXPECT_LETTERHEAD = 2560
EXPECT_MAYOR_IN_BODY = 250
MAYOR_ID = "office-of-the-mayor"

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
    assert registry_only.counts()["names_total"] == EXPECT_REGISTRY_NAMES


@needs_registry
def test_the_extra_agencies_file_adds_to_what_the_registry_knows(name_list,
                                                                registry_only):
    """The hand-written file is meant to be additive, never a replacement."""
    extra = name_list.counts()["names_total"] - registry_only.counts()["names_total"]
    assert extra > 0
    assert name_list.counts()["from_extra_file"] == extra


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
def test_the_extra_agencies_file_supplies_doitt_that_the_registry_lost(name_list):
    """MODA's own record NYC_GOID_000382 lists 'Department of Information
    Technology and Telecommunications' and 'DoITT' under its alternate-or-former
    fields. Those two values are the ONLY ones out of all 306 MODA records that
    ../ny-gov-web-registry is missing, because the 'oti' record was never merged
    with its MODA record. The extra-agencies file stands in until that is fixed
    upstream. Delete this test along with that entry."""
    assert name_list.ids_for("doitt") == ("oti",)
    assert name_list.ids_for(
        "department of information technology and telecommunications") == ("oti",)


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
    assert thrown_out["THE CITY OF NEW YORK OFFICE OF THE MAYOR NEW YORK"] == 601


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

    # Every spelling of DoITT and of OTI now lands on the one agency, because the
    # extra-agencies file supplies the former name the registry dropped.
    oti = orders_by_name["oti"]
    assert len(oti) >= 55
    assert min(oti) < "2000"          # the chain starts in the 1990s
    assert "2022-EO-003" in oti       # the order that hands DoITT's work to OTI

    # Including the bare acronym, in both spellings the orders actually use.
    spellings = {m.text for m in mentions if m.agency_id == "oti"}
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
        agencies_mod.load_descriptions(DESCRIPTIONS))

    by_id = {r["id"]: r for r in rows}
    assert agencies_mod.ids_in(mentions) == set(by_id)
    missing_a_name = sorted(i for i, r in by_id.items() if not r.get("name"))
    assert missing_a_name == []


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
        agencies_mod.load_descriptions(DESCRIPTIONS))

    assert len(rows) == EXPECT_AGENCIES_FOUND
    assert len(rows) < len(nl.load_registry(REGISTRY))
    # Sorted by id, so a re-run cannot reorder the file.
    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)


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
