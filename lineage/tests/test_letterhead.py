"""Offline tests for :mod:`lineage.letterhead` (telling stationery from a reference).

Fixture strings are cut from real orders; the source order is named in a comment
(engineering-standards §0). The one thing under test throughout: the mayoral
masthead at the head of the page must be marked, and a real sentence naming the
Office of the Mayor must not.
"""

from __future__ import annotations

import pytest
from lineage import letterhead
from lineage import namelist as nl
from lineage import scan

NAME = "Office of the Mayor"


def _mark(text, name=NAME):
    """Where ``name`` sits in ``text``, and whether that spot is letterhead."""
    start = text.index(name)
    return letterhead.is_letterhead(text, start, start + len(name))


def _mark_wrapped(text, first, last):
    """Same, for a find that extraction wrapped across lines."""
    start = text.index(first)
    end = text.index(last, start) + len(last)
    return letterhead.is_letterhead(text, start, end)


# --------------------------------------------------------------------------- #
# The masthead line                                                             #
# --------------------------------------------------------------------------- #


def test_the_plain_letterhead_is_marked():
    """Shape of 1946-EO-002 and most of the corpus."""
    text = ("# CITY OF NEW YORK\n"
            "OFFICE OF THE MAYOR\n"
            "NEW YORK 7, N. Y.\n"
            "January 10, 1955.\n")
    assert _mark(text, "OFFICE OF THE MAYOR")


def test_a_markdown_heading_mark_does_not_hide_the_letterhead():
    """Shape of 1966-EM-D0112, where the letterhead became an H1."""
    text = ("# OFFICE OF THE MAYOR\n"
            "CITY OF NEW YORK\n"
            "January 12, 1966\n")
    assert _mark(text, "OFFICE OF THE MAYOR")


def test_a_trailing_ocr_mark_does_not_hide_the_letterhead():
    """Shape of 1977-EO-077, where a rule under the line came out as a dash."""
    text = ("THE CITY OF NEW YORK\n"
            "OFFICE OF THE MAYOR —\n"
            "EXECUTIVE ORDER NO. 77\n")
    assert _mark(text, "OFFICE OF THE MAYOR")


def test_a_letterhead_wrapped_across_lines_is_marked():
    """Shape of 2012-EO-190, one word per line down the page."""
    text = ("THE\nCITY OF\nNEW YORK\n"
            "OFFICE\nOF THE\nMAYOR\n"
            "NEW YORK, N. Y. 10007\n"
            "EXECUTIVE ORDER NO. 190\n")
    assert _mark_wrapped(text, "OFFICE\n", "MAYOR")


def test_a_second_letterhead_further_down_the_page_is_marked():
    """Shape of 2014-EO-001: two scanned pages joined, so the stationery comes
    round again after the signature. 132 real finds sit like this, which is why
    the rule asks nothing about how far into the order the find is."""
    text = ("This Order shall take effect immediately.\n"
            "Bill de Blasio\nMayor\n\n"
            "THE CITY OF NEW YORK\n"
            "OFFICE OF THE MAYOR\n"
            "NEW YORK, N.Y. 10007\n"
            "EMERGENCY EXECUTIVE ORDER NO. 1\n")
    assert _mark(text, "OFFICE OF THE MAYOR")


# --------------------------------------------------------------------------- #
# What must survive                                                             #
# --------------------------------------------------------------------------- #


def test_a_reference_inside_a_sentence_is_left_alone():
    """Shape of 1969-EO-108. The thing the whole rule exists to protect."""
    text = ("it is hereby ordered as follows:\n"
            "Section 1. There is created in the Office of the Mayor a Commission "
            "on Inflation.\n")
    assert not _mark(text)


def test_a_reference_the_scan_wrapped_onto_its_own_line_is_left_alone():
    """Shape of 1986-EO-102. Line-alone alone would get this wrong: the name has
    its line to itself. The masthead test is what saves it — the lines around it
    are a sentence, not stationery."""
    text = ('The Office of the\nAuditor General (the "Office") is hereby '
            "established in the\n"
            "Office of the Mayor.\n"
            "The Office shall be headed by the\nAuditor General.\n")
    start = text.rindex("Office of the Mayor")
    assert not letterhead.is_letterhead(text, start, start + len(NAME))


def test_a_line_of_its_own_with_no_masthead_beside_it_is_left_alone():
    text = ("The Office shall have the following functions and duties:\n"
            "(1) To advise\n"
            "Office of the Mayor\n"
            "regarding the establishment of citywide strategies.\n")
    assert not _mark(text)


def test_a_caps_title_holding_a_masthead_word_is_not_a_masthead_line():
    """The bound that stops a real subject line from passing as stationery."""
    assert not letterhead.is_masthead_line(
        "ESTABLISHMENT OF THE MAYOR'S MIDTOWN ACTION OFFICE FOR THE CITY")
    assert letterhead.is_masthead_line("OFFICE OF THE MAYOR")


def test_a_blank_line_is_not_a_masthead_line():
    assert not letterhead.is_masthead_line("")
    assert not letterhead.is_masthead_line("   \t ")


def test_an_address_line_is_a_masthead_line_whatever_it_says():
    assert letterhead.is_masthead_line("250 BROADWAY, NEW YORK, N. Y. 10007")
    assert letterhead.is_masthead_line("NEW YORK 7, N. Y.")


# --------------------------------------------------------------------------- #
# Through the scan                                                              #
# --------------------------------------------------------------------------- #


def _ent(eid, name):
    return {"id": eid, "name": name, "short_name": None, "other_names": []}


def _scan(text, agencies, eo_id="TEST-EO-001"):
    g = nl.build(agencies, [], {})
    matcher = scan.build_matcher(g)
    ids_by_name = {n.normalized: (n.agency_ids, n.source) for n in g.searchable}
    return scan.scan_record(eo_id, text, matcher, ids_by_name)


LETTERHEAD_PAGE = ("THE CITY OF NEW YORK\n"
                   "{name}\n"
                   "NEW YORK, N. Y. 10007\n"
                   "EXECUTIVE ORDER NO. 38\n")


def test_the_scan_marks_the_mayor_letterhead():
    text = LETTERHEAD_PAGE.format(name="OFFICE OF THE MAYOR")
    (m,) = _scan(text, [_ent("office-of-the-mayor", NAME)])
    assert m.in_letterhead
    assert text[m.start:m.end] == m.text


def test_the_scan_leaves_a_mayor_reference_in_the_body_unmarked():
    text = "There is created in the Office of the Mayor a Commission.\n"
    (m,) = _scan(text, [_ent("office-of-the-mayor", NAME)])
    assert not m.in_letterhead


def test_an_agency_that_is_not_on_the_stationery_is_never_marked():
    """Only the mayoral masthead is judged. A department name on its own
    letterhead line stays an ordinary find — the rule is too blunt to judge it,
    and a wrong mark would delete a real reference from the counts."""
    text = LETTERHEAD_PAGE.format(name="DEPARTMENT OF INVESTIGATION")
    (m,) = _scan(text, [_ent("doi", "Department of Investigation")])
    assert not m.in_letterhead
    assert "doi" not in scan.MASTHEAD_AGENCY_IDS


def test_a_name_shared_by_two_agencies_is_never_marked():
    """We do not know whose stationery it would be, and a guess is a bug."""
    text = LETTERHEAD_PAGE.format(name="OFFICE OF THE MAYOR")
    (m,) = _scan(text, [_ent("office-of-the-mayor", NAME), _ent("other", NAME)])
    assert m.agency_id is None
    assert not m.in_letterhead


def test_the_mark_is_written_out_only_when_it_is_true():
    text = LETTERHEAD_PAGE.format(name="OFFICE OF THE MAYOR")
    (marked,) = _scan(text, [_ent("office-of-the-mayor", NAME)])
    (plain,) = _scan("created in the Office of the Mayor a Commission.",
                     [_ent("office-of-the-mayor", NAME)])
    assert marked.as_dict()["in_letterhead"] is True
    assert "in_letterhead" not in plain.as_dict()


# --------------------------------------------------------------------------- #
# The counts                                                                    #
# --------------------------------------------------------------------------- #


def test_body_only_counting_leaves_the_letterhead_out():
    text = (LETTERHEAD_PAGE.format(name="OFFICE OF THE MAYOR")
            + "There is created in the Office of the Mayor a Commission.\n")
    agencies = [_ent("office-of-the-mayor", NAME)]
    g = nl.build(agencies, [], {})
    mentions = _scan(text, agencies)
    assert len(mentions) == 2
    assert scan.name_hit_counts(mentions, g)[NAME] == 2
    assert scan.name_hit_counts(mentions, g, body_only=True)[NAME] == 1
