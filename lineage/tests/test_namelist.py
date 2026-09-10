"""Offline tests for :mod:`lineage.name_list`.

Every made-up agency here mirrors a shape actually present in
``../ny-gov-web-registry/data/registry.json`` (engineering-standards §0 — mocks
encode documented shapes, not guesses). In particular ``other_names`` entries are
``{"name": ..., "note": ...}`` dicts there, not bare strings.
"""

from __future__ import annotations

import json

import pytest
from lineage import namelist as nl


def _ent(eid, name, short_name=None, other_names=None):
    """One registry-shaped agency."""
    return {"id": eid, "name": name, "short_name": short_name,
            "other_names": other_names or []}


# --------------------------------------------------------------------------- #
# Surface extraction                                                            #
# --------------------------------------------------------------------------- #


def test_build_picks_up_name_short_name_and_other_names():
    # Shape of the "oti" record: name + acronym + a dict-form alternate.
    ents = [_ent("oti", "Office of Technology and Innovation", "OTI",
                 [{"name": "NYC Office of Technology", "note": "former web title"}])]
    g = nl.build(ents, [], {})
    assert g.ids_for("office of technology and innovation") == ("oti",)
    assert g.ids_for("oti") == ("oti",)
    assert g.ids_for("nyc office of technology") == ("oti",)


def test_other_names_accepts_plain_strings_for_hand_written_entries():
    ents = [_ent("doitt", "Department of Information Technology", "DoITT",
                 ["Dept. of Information Technology"])]
    g = nl.build([], ents, {})
    assert g.ids_for("dept of information technology") == ("doitt",)


def test_an_agency_without_an_id_is_skipped():
    g = nl.build([{"name": "Nameless Office"}], [], {})
    assert g.names == ()


def test_a_name_that_tidies_to_nothing_is_dropped():
    g = nl.build([_ent("x", "---")], [], {})
    assert g.names == ()


# --------------------------------------------------------------------------- #
# Ambiguity — never resolved by guessing                                        #
# --------------------------------------------------------------------------- #


def test_a_name_two_agencies_share_is_marked_shared():
    ents = [_ent("a", "Commission on Human Rights"),
            _ent("b", "commission on human rights")]
    g = nl.build(ents, [], {})
    known = g.names[0]
    assert known.shared
    assert set(known.agency_ids) == {"a", "b"}


def test_an_extra_agency_sharing_a_registry_name_is_added_not_swapped_in():
    g = nl.build([_ent("reg", "Loft Board")], [_ent("ovl", "Loft Board")], {})
    assert g.ids_for("loft board") == ("reg", "ovl")
    # First writer keeps the source label, so registry provenance is not lost.
    assert g.names[0].source == nl.FROM_REGISTRY


# --------------------------------------------------------------------------- #
# Ordering — what makes longest-match-first work in the scanner                  #
# --------------------------------------------------------------------------- #


def test_names_come_back_longest_first():
    ents = [_ent("a", "Police Department"),
            _ent("b", "New York City Police Department")]
    g = nl.build(ents, [], {})
    assert [n.name for n in g.names] == [
        "New York City Police Department", "Police Department"]


# --------------------------------------------------------------------------- #
# Match rules                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,expected_mode", [
    # <= short_name_length (5) defaults to exact-case: an acronym in caps, matched in caps.
    ("OTI", nl.MATCH_SAME_CASE),
    ("DOHMH", nl.MATCH_SAME_CASE),
    # Longer names match case-insensitively.
    ("Office of Technology and Innovation", nl.MATCH_ANY_CASE),
    ("DOITT!", nl.MATCH_ANY_CASE),
])
def test_default_mode_follows_the_short_name_length(name, expected_mode):
    g = nl.build([_ent("x", name)], [], {})
    assert g.names[0].match == expected_mode


def test_a_written_rule_beats_the_default():
    # LAW: 4,949 case-insensitive hits and 14 case-sensitive, all of them false.
    rules = {"LAW": {"match": nl.MATCH_NEVER, "note": "..."}}
    g = nl.build([_ent("law", "LAW")], [], rules)
    assert g.names[0].match == nl.MATCH_NEVER
    assert g.searchable == ()


def test_a_rule_can_make_a_short_name_match_whatever_the_case():
    rules = {"H+H": {"match": nl.MATCH_ANY_CASE, "note": "..."}}
    g = nl.build([_ent("hh", "H+H")], [], rules)
    assert g.names[0].match == nl.MATCH_ANY_CASE


def test_an_unknown_match_rule_is_refused_at_load(tmp_path):
    p = tmp_path / "name_rules.json"
    p.write_text(json.dumps({"rules": {"X": {"match": "sometimes"}}}))
    with pytest.raises(SystemExit, match="unknown match"):
        nl.load_rules(p)


# --------------------------------------------------------------------------- #
# The audit gates                                                               #
# --------------------------------------------------------------------------- #


def test_a_short_name_found_often_with_no_rule_is_flagged():
    g = nl.build([_ent("doc", "DOC")], [], {}, max_hits_without_a_rule=200)
    assert nl.names_needing_a_rule(g, {"DOC": 2117}) == [("DOC", 2117)]


def test_names_needing_a_rule_is_quiet_once_a_decision_is_recorded():
    rules = {"DOC": {"match": nl.MATCH_SAME_CASE, "note": "genuine"}}
    g = nl.build([_ent("doc", "DOC")], [], rules, max_hits_without_a_rule=200)
    assert nl.names_needing_a_rule(g, {"DOC": 2117}) == []


def test_a_long_name_is_never_flagged_however_often_it_is_found():
    g = nl.build([_ent("m", "Office of the Mayor")], [], {}, max_hits_without_a_rule=200)
    assert nl.names_needing_a_rule(g, {"Office of the Mayor": 2810}) == []


def test_unused_rules_reports_a_shadowed_entry():
    """'LAW' and 'Law' normalize alike, so only one literal spelling survives."""
    rules = {"LAW": {"match": nl.MATCH_NEVER}, "Law": {"match": nl.MATCH_NEVER}}
    g = nl.build([_ent("law", "LAW")], [], rules)
    assert nl.unused_rules(g) == ["Law"]


# --------------------------------------------------------------------------- #
# Loading                                                                       #
# --------------------------------------------------------------------------- #


def test_load_registry_accepts_both_the_wrapped_and_bare_shapes(tmp_path):
    wrapped = tmp_path / "w.json"
    wrapped.write_text(json.dumps({"entities": [_ent("a", "Office A")]}))
    bare = tmp_path / "b.json"
    bare.write_text(json.dumps([_ent("a", "Office A")]))
    assert nl.load_registry(wrapped) == nl.load_registry(bare)


def test_load_registry_refuses_a_file_with_no_entities_list(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"nope": 1}))
    with pytest.raises(SystemExit, match="no 'entities' list"):
        nl.load_registry(p)


def test_a_missing_extra_agencies_file_is_normal_not_an_error(tmp_path):
    """There is no extra-agencies file before the first review pass."""
    assert nl.load_extra_agencies(tmp_path / "absent.json") == []


def test_a_missing_rules_file_falls_back_to_the_defaults(tmp_path):
    rules, length, max_hits = nl.load_rules(tmp_path / "absent.json")
    assert rules == {}
    assert length == nl.DEFAULT_SHORT_NAME_LENGTH
    assert max_hits == nl.DEFAULT_MAX_HITS_WITHOUT_A_RULE


# --------------------------------------------------------------------------- #
# The mayoral short spelling                                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name,expected", [
    ("Mayor's Office of Operations", "Office of Operations"),
    ("Mayor’s Office of Operations", "Office of Operations"),      # curly
    ("Mayors Office of Operations", "Office of Operations"),       # no apostrophe
    ("Mayor's Office for Economic Opportunity",
     "Office for Economic Opportunity"),
    ("The Mayor's Office of Food Policy", "Office of Food Policy"),
    ("MAYOR'S OFFICE OF OPERATIONS", "OFFICE OF OPERATIONS"),
])
def test_the_mayoral_prefix_is_stripped(name, expected):
    assert nl.mayoral_short_name(name) == expected


@pytest.mark.parametrize("name", [
    "Office of the Mayor",                    # the office itself, not "Mayor's X"
    "Office of Collective Bargaining",        # never had the prefix
    "Mayor's Fund to Advance New York City",  # possessive, but not an office
    "Mayor's Office",                         # no "of X" to keep
    "Mayor's Office of",                      # nothing after the joining word
    "Mayoral Office of Operations",           # not the possessive
])
def test_a_name_of_another_shape_yields_nothing(name):
    assert nl.mayoral_short_name(name) is None


def test_the_short_spelling_joins_the_list_under_the_same_agency():
    """The point of the rule: the orders write "Office of Operations" and mean the
    body the registry files as "Mayor's Office of Operations"."""
    name_list = nl.build([_ent("ops", "Mayor's Office of Operations", "OPS")],
                         [], {})
    assert name_list.ids_for("office of operations") == ("ops",)
    assert name_list.ids_for("mayor s office of operations") == ("ops",)
    built = [n for n in name_list.names if n.source == nl.FROM_MAYORAL_VARIANT]
    assert [n.name for n in built] == ["Office of Operations"]
    assert name_list.counts()["from_mayoral_variant"] == 1


def test_the_rule_works_one_way_only():
    """It strips "Mayor's" and never adds it. "Office of Collective Bargaining" is
    a registry name and not a mayoral office, and inventing a "Mayor's" spelling
    for it would hand its text to the wrong body."""
    name_list = nl.build([_ent("ocb", "Office of Collective Bargaining")], [], {})
    assert name_list.counts()["from_mayoral_variant"] == 0
    assert name_list.ids_for("mayor s office of collective bargaining") == ()


def test_a_spelling_a_real_source_already_holds_is_left_alone():
    """The registry files both spellings on `office-of-data-analytics` itself. The
    rule must not add its id a second time, and must not take the source label off
    a name the registry actually wrote."""
    ents = [_ent("oda", "Office of Data Analytics", "ODA",
                 [{"name": "Mayor's Office of Data Analytics"}])]
    name_list = nl.build(ents, [], {})
    assert name_list.ids_for("office of data analytics") == ("oda",)
    short = next(n for n in name_list.names
                 if n.normalized == "office of data analytics")
    assert short.source == nl.FROM_REGISTRY
    assert name_list.counts()["from_mayoral_variant"] == 0


def test_the_short_spelling_never_takes_a_name_off_another_agency():
    """A generated spelling that lands on another agency's real name would make
    that name shared, and `scan` pins a shared name to nobody. The real name wins
    and keeps its single id."""
    ents = [_ent("a", "Mayor's Office of Nightlife"),
            _ent("b", "Office of Nightlife")]
    name_list = nl.build(ents, [], {})
    assert name_list.ids_for("office of nightlife") == ("b",)
    assert name_list.counts()["from_mayoral_variant"] == 0
    assert name_list.counts()["shared_by_two_agencies"] == 0


def test_a_shared_long_name_hands_its_shared_ness_to_the_short_one():
    """Two agencies spelling one mayoral name is already reported rather than
    settled by picking one. The short spelling must inherit that, not quietly
    choose whichever agency the loop reached first."""
    ents = [_ent("a", "Mayor's Office of Equity"),
            _ent("b", "Some Other Body", None, ["The Mayor's Office of Equity"])]
    name_list = nl.build(ents, [], {})
    assert name_list.ids_for("mayor s office of equity") == ("a", "b")
    assert name_list.ids_for("office of equity") == ("a", "b")
    assert name_list.generated_names_by_agency() == {
        "a": ["Office of Equity"], "b": ["Office of Equity"]}


def test_the_built_spelling_is_matched_by_the_ordinary_rules():
    """No exemption. `how_to_match` is asked for it like any other name."""
    ents = [_ent("ops", "Mayor's Office of Operations")]
    built = next(n for n in nl.build(ents, [], {}).names
                 if n.source == nl.FROM_MAYORAL_VARIANT)
    assert built.match == nl.MATCH_ANY_CASE


def test_generated_names_are_filed_under_every_agency_that_owns_them():
    ents = [_ent("ops", "Mayor's Office of Operations"),
            _ent("ocb", "Office of Collective Bargaining")]
    assert nl.build(ents, [], {}).generated_names_by_agency() == {
        "ops": ["Office of Operations"]}
