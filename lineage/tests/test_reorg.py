"""Pass three, one rule at a time, against made-up text.

Every test here builds its own spans, so nothing depends on the registry or on the
committed corpus. The numbers over the real corpus are pinned in
``test_real_corpus.py`` instead.
"""

from __future__ import annotations

from lineage import reorg
from lineage.mentions import (
    FOUND_BY_NAME_LIST,
    FOUND_BY_PATTERN,
    ROLE_FROM,
    ROLE_PARENT,
    ROLE_TO,
    WHY_NO_AGENCY_NAMED,
    WHY_ONE_SIDE_ONLY,
    Mention,
    ONE_AGENCY,
    ProposedName,
)
from lineage.namelist import FROM_REGISTRY


def known(eo_id: str, text: str, name: str, agency_id: str,
          in_letterhead: bool = False) -> Mention:
    """A pass-one find of ``name``, placed where it really sits in ``text``."""
    start = text.index(name)
    return Mention(eo_id=eo_id, start=start, end=start + len(name), text=name,
                   agency_id=agency_id, agency_ids=(agency_id,),
                   found_by=FOUND_BY_NAME_LIST, pins_down=ONE_AGENCY,
                   source=FROM_REGISTRY, in_letterhead=in_letterhead)


def guessed(eo_id: str, text: str, name: str) -> ProposedName:
    """A pass-two proposal of ``name``, placed where it really sits in ``text``."""
    start = text.index(name)
    return ProposedName(eo_id=eo_id, start=start, end=start + len(name), text=name,
                        name=name, found_by=FOUND_BY_PATTERN, why="not-on-the-list")


def run(text: str, *spans):
    """Pass three over one made-up order."""
    return reorg.find_events([{"eo_id": "T-1", "full_text": text}],
                             [s for s in spans if isinstance(s, Mention)],
                             [s for s in spans if isinstance(s, ProposedName)])


def roles_of(event) -> dict:
    return {r.role: r.name for r in event.roles}


# --------------------------------------------------------------------------- #
# Both grammatical shapes of "established"                                      #
# --------------------------------------------------------------------------- #

def test_the_existential_shape_names_the_new_body_after_the_verb():
    """"There is hereby established a <name>" — the shape supersede.py reads."""
    text = "There is hereby established an Interagency Committee on Business."
    events, unresolved = run(
        text, guessed("T-1", text, "Interagency Committee on Business"))
    assert not unresolved
    assert len(events) == 1
    assert events[0].kind == reorg.KIND_ESTABLISHES
    assert roles_of(events[0]) == {ROLE_TO: "Interagency Committee on Business"}


def test_the_commoner_shape_names_the_new_body_before_the_verb():
    """The shape supersede.py misses entirely, and it is the frequent one."""
    text = "The Office of the Auditor General is hereby established today."
    events, unresolved = run(text, guessed("T-1", text, "Office of the Auditor General"))
    assert not unresolved
    assert roles_of(events[0]) == {ROLE_TO: "Office of the Auditor General"}


def test_a_body_placed_inside_another_records_the_parent_separately():
    """"established in the Office of the Mayor" is a parent, not the thing made."""
    text = ("There is hereby established in the Office of the Mayor an M/WBE "
            "Advisory Committee for the City.")
    events, _ = run(text,
                    known("T-1", text, "Office of the Mayor", "office-of-the-mayor"),
                    guessed("T-1", text, "M/WBE Advisory Committee"))
    assert roles_of(events[0]) == {ROLE_TO: "M/WBE Advisory Committee",
                                   ROLE_PARENT: "Office of the Mayor"}


def test_the_parent_is_never_also_the_thing_established():
    """With only the parent named, nothing was established that we can point to."""
    text = "There is hereby established in the Office of the Mayor a new body."
    events, unresolved = run(
        text, known("T-1", text, "Office of the Mayor", "office-of-the-mayor"))
    assert not events
    assert unresolved[0].why == WHY_ONE_SIDE_ONLY


# --------------------------------------------------------------------------- #
# Renames                                                                       #
# --------------------------------------------------------------------------- #

def test_a_rename_records_both_the_old_name_and_the_new_one():
    """1990-EO-013's shape, the plainest rename in the corpus."""
    text = ("The Office of Municipal Labor Relations is hereby renamed the "
            "Office of Labor Relations.")
    events, _ = run(text,
                    guessed("T-1", text, "Office of Municipal Labor Relations"),
                    guessed("T-1", text, "Office of Labor Relations"))
    assert events[0].kind == reorg.KIND_RENAMES
    assert roles_of(events[0]) == {ROLE_FROM: "Office of Municipal Labor Relations",
                                   ROLE_TO: "Office of Labor Relations"}


def test_designated_as_carries_a_rename_because_2022_eo_003_says_it_that_way():
    """The DoITT-to-OTI hand-off does not use the word "renamed"."""
    text = ("The Department of Information Technology and Telecommunications "
            "shall hereafter be designated as the Office of Technology and "
            "Innovation, except in court documents.")
    events, _ = run(
        text,
        known("T-1", text, "Department of Information Technology and "
                           "Telecommunications", "oti"),
        known("T-1", text, "Office of Technology and Innovation", "oti"))
    assert events[0].kind == reorg.KIND_RENAMES
    assert events[0].verb == "designated"
    assert events[0].same_agency


# --------------------------------------------------------------------------- #
# The false friends                                                             #
# --------------------------------------------------------------------------- #

def test_a_person_taking_a_post_is_not_a_reorganization():
    """"is hereby designated Vice-Chairman" names a person, and no agency."""
    text = "The Honorable Paul R. Screvane is hereby designated Vice-Chairman."
    events, unresolved = run(text)
    assert not events
    assert unresolved[0].why == WHY_NO_AGENCY_NAMED


def test_money_moving_between_accounts_is_not_a_transfer_of_work():
    text = 'Salaries or wages transferred to "UNCLAIMED" account are adjusted.'
    events, _ = run(text)
    assert not events


def test_orders_continued_in_force_are_not_an_agency_event():
    """2022-EO-001 continues every order, not an agency."""
    text = ("All Executive Orders in effect on December 31, 2021 are hereby "
            "continued unless specifically revoked.")
    events, _ = run(text)
    assert not events


def test_a_deputy_mayor_roster_is_not_a_chain_of_renames():
    """The commonest wrong reading in the corpus, and the rule that stops it.

    Each post is a separate appointment. The nearest name before the second verb
    is the FIRST post, so without the rule this reads as a rename of one deputy
    mayor into another — 84 false renames over the corpus.
    """
    text = ("One shall be designated the First Deputy Mayor, one shall be "
            "designated the Deputy Mayor for Operations, one shall be designated "
            "the Deputy Mayor for Policy.")
    events, unresolved = run(
        text,
        known("T-1", text, "First Deputy Mayor", "first-deputy-mayor"),
        known("T-1", text, "Deputy Mayor for Operations", "dm-operations"),
        known("T-1", text, "Deputy Mayor for Policy", "dm-policy"))
    assert not events
    assert all(u.why == WHY_ONE_SIDE_ONLY for u in unresolved)


def test_a_name_inside_an_in_phrase_is_not_the_subject():
    """"The X and the Y, in the Office of the Mayor, are hereby consolidated"."""
    text = ("The Mayor's Reception Committee, in the Office of the Mayor, is "
            "hereby consolidated into the Department of Public Events.")
    events, _ = run(text,
                    guessed("T-1", text, "Mayor's Reception Committee"),
                    known("T-1", text, "Office of the Mayor", "office-of-the-mayor"),
                    guessed("T-1", text, "Department of Public Events"))
    assert roles_of(events[0])[ROLE_FROM] == "Mayor's Reception Committee"


def test_the_letterhead_never_takes_a_role():
    """The stationery names the Office of the Mayor 2,560 times and means none."""
    text = "OFFICE OF THE MAYOR\nThere is hereby established a body."
    events, unresolved = run(
        text, known("T-1", text, "OFFICE OF THE MAYOR", "office-of-the-mayor",
                    in_letterhead=True))
    assert not events
    assert unresolved[0].why == WHY_NO_AGENCY_NAMED


# --------------------------------------------------------------------------- #
# Where a sentence starts and stops                                             #
# --------------------------------------------------------------------------- #

def test_a_blank_line_does_not_end_a_sentence():
    """PDF extraction drops blank lines mid-sentence; 2022-EO-003 does exactly this."""
    text = ("Section 1. The Department of Information Technology and "
            "Telecommunications\n\nshall hereafter be designated as the Office of "
            "Technology and Innovation.")
    events, _ = run(
        text,
        known("T-1", text, "Department of Information Technology and "
                           "Telecommunications", "oti"),
        known("T-1", text, "Office of Technology and Innovation", "oti"))
    assert len(events) == 1
    assert roles_of(events[0])[ROLE_FROM].startswith("Department of Information")


def test_a_full_stop_does_end_a_sentence():
    """A name in the sentence BEFORE the verb must not be pulled in."""
    text = ("The Board of Estimate met on Tuesday. There is hereby established a "
            "Committee on Buildings.")
    events, _ = run(text,
                    guessed("T-1", text, "Board of Estimate"),
                    guessed("T-1", text, "Committee on Buildings"))
    assert roles_of(events[0]) == {ROLE_TO: "Committee on Buildings"}


def test_every_recorded_span_reads_back_out_of_the_order():
    """The promise: full_text[start:end] == text, for the sentence and each role."""
    text = ("The Office of Municipal Labor Relations is hereby renamed the "
            "Office of Labor Relations.")
    events, _ = run(text,
                    guessed("T-1", text, "Office of Municipal Labor Relations"),
                    guessed("T-1", text, "Office of Labor Relations"))
    for e in events:
        assert text[e.start:e.end] == e.text
        for r in e.roles:
            assert text[r.start:r.end] == r.text


# --------------------------------------------------------------------------- #
# Resolution                                                                    #
# --------------------------------------------------------------------------- #

def test_a_body_only_pass_two_knows_carries_no_agency_id():
    """Not a failure: it says the name is missing from extra_agencies.json."""
    text = "There is hereby established a Technology Steering Committee."
    events, _ = run(text, guessed("T-1", text, "Technology Steering Committee"))
    assert events[0].roles[0].agency_id is None
    assert not events[0].fully_resolved


def test_a_known_name_beats_a_proposed_one_at_the_same_spot():
    text = "There is hereby established a Department of Buildings."
    events, _ = run(text,
                    known("T-1", text, "Department of Buildings", "department-of-buildings"),
                    guessed("T-1", text, "Department of Buildings"))
    assert events[0].roles[0].agency_id == "department-of-buildings"


def test_the_same_input_gives_the_same_answer_every_time():
    text = ("The Office of Municipal Labor Relations is hereby renamed the "
            "Office of Labor Relations.")
    spans = (guessed("T-1", text, "Office of Municipal Labor Relations"),
             guessed("T-1", text, "Office of Labor Relations"))
    first = run(text, *spans)
    second = run(text, *spans)
    assert first == second


# --------------------------------------------------------------------------- #
# Coordinated subject lists                                                     #
# --------------------------------------------------------------------------- #

def test_one_verb_can_move_a_whole_list_of_bodies():
    """2022-EO-003 § 3. Three offices move into OTI, not one.

    Taking only the nearest name records the Office of Information Privacy and
    drops Cyber Command and Data Analytics — two thirds of what the order did.
    """
    text = ("The Office of Cyber Command established pursuant to section 20-j of "
            "the Charter, the Office of Data Analytics established pursuant to "
            "section 20-f of the Charter and the Office of Information Privacy "
            "established pursuant to section 8-h of the Charter shall be continued "
            "and established within the Office of Technology and Innovation.")
    events, _ = run(text,
                    known("T-1", text, "Cyber Command", "cyber-command"),
                    known("T-1", text, "Office of Data Analytics", "oda"),
                    known("T-1", text, "Office of Information Privacy", "oip"),
                    known("T-1", text, "Office of Technology and Innovation", "oti"))
    assert len(events) == 1
    moved = [r.agency_id for r in events[0].roles if r.role == ROLE_TO]
    assert moved == ["cyber-command", "oda", "oip"]
    parent = [r.agency_id for r in events[0].roles if r.role == ROLE_PARENT]
    assert parent == ["oti"]


def test_a_list_of_bodies_can_be_abolished_together():
    """1976-EO-063's shape: four offices abolished by one verb."""
    text = ("The Office of Lower Manhattan Development, the Office of Jamaica "
            "Planning and Development and the Office of Downtown Brooklyn "
            "Development are hereby abolished.")
    events, _ = run(text,
                    guessed("T-1", text, "Office of Lower Manhattan Development"),
                    guessed("T-1", text, "Office of Jamaica Planning and Development"),
                    guessed("T-1", text, "Office of Downtown Brooklyn Development"))
    assert len(events[0].roles) == 3
    assert all(r.role == ROLE_FROM for r in events[0].roles)


def test_a_pair_joined_by_a_bare_comma_is_an_apposition_not_a_list():
    """1955-EO-022. "The Division of Analysis, Bureau of the Budget" names ONE
    body and its parent. Only the Division moves."""
    text = ("The Division of Analysis, Bureau of the Budget, together with its "
            "functions and staff, is hereby transferred to the Division of "
            "Administration.")
    events, _ = run(text,
                    guessed("T-1", text, "Division of Analysis"),
                    guessed("T-1", text, "Bureau of the Budget"),
                    guessed("T-1", text, "Division of Administration"))
    moved = [r.name for r in events[0].roles if r.role == ROLE_FROM]
    assert moved == ["Bureau of the Budget"]


def test_three_names_on_commas_alone_still_count_as_a_list():
    """No apposition in the corpus runs to three, and 1976-EO-063 needs this arm:
    its final "and" sits inside a pass-two span rather than in a gap."""
    text = ("The Board of Alpha, the Board of Beta, the Board of Gamma are hereby "
            "abolished.")
    events, _ = run(text,
                    guessed("T-1", text, "Board of Alpha"),
                    guessed("T-1", text, "Board of Beta"),
                    guessed("T-1", text, "Board of Gamma"))
    assert len(events[0].roles) == 3


def test_a_finite_verb_between_two_names_ends_the_list():
    """The load-bearing guard. Without it "is hereby revoked and the" is perfectly
    good glue, and the revoked order's body gets abolished along with the real
    one."""
    text = ("Executive Order No. 4 establishing the Board of Estimate hereby is "
            "revoked and the Committee on the Judiciary is hereby abolished.")
    events, _ = run(text,
                    guessed("T-1", text, "Board of Estimate"),
                    guessed("T-1", text, "Committee on the Judiciary"))
    gone = [r.name for e in events for r in e.roles if r.role == ROLE_FROM]
    assert gone == ["Committee on the Judiciary"]


def test_a_sentence_break_between_two_names_ends_the_list():
    text = ("The Board of Alpha is retained; the Board of Beta and the Board of "
            "Gamma are hereby abolished.")
    events, _ = run(text,
                    guessed("T-1", text, "Board of Alpha"),
                    guessed("T-1", text, "Board of Beta"),
                    guessed("T-1", text, "Board of Gamma"))
    gone = [r.name for r in events[0].roles]
    assert "Board of Alpha" not in gone


def test_a_long_stretch_of_text_between_two_names_is_not_glue():
    """A list item carries a phrase, not a clause."""
    long_gap = " " + ("of the City of New York for purposes described at length " * 2)
    assert not reorg.is_list_glue(long_gap + " and the ")


def test_only_names_before_the_verb_form_a_list():
    """After the verb, "and the Council" is likelier the body being advised than a
    second body being created."""
    text = ("There is hereby established a Committee on Housing to advise the "
            "Mayor and the Council of the City.")
    events, _ = run(text,
                    guessed("T-1", text, "Committee on Housing"),
                    guessed("T-1", text, "Council of the City"))
    assert [r.name for r in events[0].roles] == ["Committee on Housing"]


def test_a_rename_never_takes_a_list():
    """One body becomes one other. A list here is a mis-read, not a multi-way
    rename."""
    text = ("The Office of Alpha and the Office of Beta are hereby renamed the "
            "Office of Gamma.")
    events, _ = run(text,
                    guessed("T-1", text, "Office of Alpha"),
                    guessed("T-1", text, "Office of Beta"),
                    guessed("T-1", text, "Office of Gamma"))
    assert [r.name for r in events[0].roles] == ["Office of Beta", "Office of Gamma"]
