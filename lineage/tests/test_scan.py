"""Offline tests for :mod:`lineage.scan` (the known-name pass).

Fixture strings mirror real corpus text; the source order is named in a comment
(engineering-standards §0). The contract under test throughout is
``full_text[start:end] == text``, character for character.
"""

from __future__ import annotations

import pytest
from lineage import namelist as nl
from lineage import scan
from lineage.mentions import ONE_AGENCY, SEVERAL_AGENCIES


def _ent(eid, name, short_name=None):
    return {"id": eid, "name": name, "short_name": short_name, "other_names": []}


def _scan(text, agencies, rules=None, eo_id="TEST-EO-001"):
    """Scan one string. Mirrors what scan.scan() does per record, guard included:
    a name list where every name is ruled 'never' compiles to no pattern at all."""
    g = nl.build(agencies, [], rules or {})
    matcher = scan.build_matcher(g)
    if matcher is None:
        return []
    ids_by_name = {n.normalized: (n.agency_ids, n.source) for n in g.searchable}
    return scan.scan_record(eo_id, text, matcher, ids_by_name)


# --------------------------------------------------------------------------- #
# The offset contract                                                           #
# --------------------------------------------------------------------------- #


def test_positions_point_into_full_text_exactly():
    text = "and the Office of Technology and Innovation shall report."
    (m,) = _scan(text, [_ent("oti", "Office of Technology and Innovation")])
    assert text[m.start:m.end] == m.text
    assert m.text == "Office of Technology and Innovation"


def test_a_name_split_across_lines_is_found_and_its_position_stays_exact():
    """Shape of 2022-EO-003, where extraction wraps the name across a line."""
    text = "the NYC Cyber\nCommand shall coordinate"
    (m,) = _scan(text, [_ent("cc", "NYC Cyber Command")])
    assert m.text == "NYC Cyber\nCommand"
    assert text[m.start:m.end] == m.text
    assert m.agency_id == "cc"


# --------------------------------------------------------------------------- #
# Longest match wins                                                            #
# --------------------------------------------------------------------------- #


def test_the_longest_name_wins_at_any_given_spot():
    text = "the New York City Police Department shall"
    ents = [_ent("nypd", "New York City Police Department"),
            _ent("pd", "Police Department")]
    (m,) = _scan(text, ents)
    assert m.text == "New York City Police Department"
    assert m.agency_id == "nypd"


def test_finds_never_overlap():
    text = "Department of Buildings and Department of Correction"
    ents = [_ent("dob", "Department of Buildings"),
            _ent("doc", "Department of Correction")]
    spans = sorted((m.start, m.end) for m in _scan(text, ents))
    assert len(spans) == 2
    assert spans[0][1] <= spans[1][0]


# --------------------------------------------------------------------------- #
# Word boundaries                                                               #
# --------------------------------------------------------------------------- #


def test_a_name_inside_a_longer_word_is_not_found():
    assert _scan("SUPEROTIC", [_ent("oti", "OTI")]) == []


def test_a_hyphen_does_not_shield_a_name_from_the_word_edge_check():
    """Corpus: "EMPLOYER PICK-UP PURSUANT TO SECTION 414(h)". This is exactly why
    'UP' is ruled 'never' — the word-edge check alone cannot save it."""
    hits = _scan("EMPLOYER PICK-UP PURSUANT", [_ent("up", "UP")])
    assert [m.text for m in hits] == ["UP"]

    rules = {"UP": {"match": nl.MATCH_NEVER, "note": "..."}}
    assert _scan("EMPLOYER PICK-UP PURSUANT", [_ent("up", "UP")], rules) == []


# --------------------------------------------------------------------------- #
# Case rules                                                                    #
# --------------------------------------------------------------------------- #


def test_a_short_name_needs_the_same_capitals_by_default():
    ents = [_ent("law", "LAW")]
    # Corpus: "SECTION 210 OF THE CIVIL SERVICE LAW" — caps, so it does match.
    assert len(_scan("THE CIVIL SERVICE LAW, Whereas", ents)) == 1
    # But the ordinary English word does not.
    assert _scan("as required by law, the agency", ents) == []


def test_a_long_name_matches_whatever_the_capitals():
    ents = [_ent("oti", "Office of Technology and Innovation")]
    # Shape of 2022-EO-003, whose heading is set in full caps.
    (m,) = _scan("THE OFFICE OF TECHNOLOGY AND INNOVATION shall", ents)
    assert m.text == "OFFICE OF TECHNOLOGY AND INNOVATION"
    assert m.agency_id == "oti"


def test_a_name_ruled_never_does_not_match_alone_but_a_longer_one_does():
    ents = [_ent("loft", "LOFT"), _ent("lb", "New York City Loft Board")]
    rules = {"LOFT": {"match": nl.MATCH_NEVER, "note": "..."}}
    # Shape of 1982-EO-066: "NEW YORK CITY LOFT BOARD".
    hits = _scan("NEW YORK CITY LOFT BOARD yi By the power", ents, rules)
    assert [m.agency_id for m in hits] == ["lb"]


# --------------------------------------------------------------------------- #
# Ambiguity — recorded, never guessed                                           #
# --------------------------------------------------------------------------- #


def test_a_shared_name_records_every_agency_and_pins_down_none():
    ents = [_ent("a", "Planning Council"), _ent("b", "planning council")]
    (m,) = _scan("the Planning Council met", ents)
    assert m.pins_down == SEVERAL_AGENCIES
    assert m.agency_id is None
    assert set(m.agency_ids) == {"a", "b"}


def test_a_name_one_agency_owns_pins_that_agency_down():
    (m,) = _scan("the Loft Board met", [_ent("lb", "Loft Board")])
    assert m.pins_down == ONE_AGENCY
    assert m.agency_id == "lb"


# --------------------------------------------------------------------------- #
# Whole-corpus pass                                                             #
# --------------------------------------------------------------------------- #


def test_the_pass_gives_the_same_answer_every_time():
    recs = [{"eo_id": "A", "full_text": "the Loft Board and the Loft Board"}]
    g = nl.build([_ent("lb", "Loft Board")], [], {})
    assert scan.scan(recs, g) == scan.scan(recs, g)


def test_an_empty_name_list_yields_no_matcher_and_no_mentions():
    assert scan.build_matcher(nl.build([], [], {})) is None
    assert scan.scan([{"eo_id": "A", "full_text": "text"}], nl.build([], [], {})) == []


def test_name_hit_counts_group_case_variants_onto_the_name_list_form():
    recs = [{"eo_id": "A", "full_text": "Loft Board, LOFT BOARD, loft board"}]
    g = nl.build([_ent("lb", "Loft Board")], [], {})
    assert scan.name_hit_counts(scan.scan(recs, g), g) == {"Loft Board": 3}


@pytest.mark.parametrize("raw,mode,should_match", [
    ("OTI", nl.MATCH_SAME_CASE, True),
    ("oti", nl.MATCH_SAME_CASE, False),
])
def test_name_pattern_honors_its_own_case_mode(raw, mode, should_match):
    import re
    rx = re.compile(scan.name_pattern("OTI", mode))
    assert bool(rx.fullmatch(raw)) is should_match
