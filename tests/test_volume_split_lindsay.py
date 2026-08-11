"""Regression tests for three segmentation bugs found on the Lindsay Orders volume.

Every fixture under ``tests/fixtures/pre1974_lindsay/`` is a REAL page record
copied verbatim from the 1972-02-01 .. 1973-11-12 Lindsay Orders run (same
convention as ``tests/fixtures/pre1974/``). All three bugs are the same mistake
in three places — a signal that is only meaningful as a WHOLE LINE was matched
anywhere inside one, so ordinary body prose opened a document mid-instrument:

    pages 106-111   Executive Order No. 84 runs 107-110 and was split into a
                    one-page record and a three-page phantom. Page 108 opens
                    with the "-2-" Page-header ornament, but its body says
                    "...vested in the Mayor's Office for Veteran Action by this
                    Executive Order", and the unanchored BARE-LABEL fallback
                    read that as a new unnumbered executive order — which then
                    outranked the ornament in :func:`classify_page`. Page 106
                    ("This Executive Order shall take effect immediately.") is
                    the same shape, one order earlier.

    pages 31-33     Executive Order No. 70 opens on 31 and runs through 49.
                    Page 32's body reads "...granted in Section 124 (a) of the
                    New York City Charter are hereby delegated to the Office of
                    the Mayor...", and LETTERHEAD detection slid its window
                    across that 154-token paragraph and found both anchors, so
                    the page opened a document on letterhead-plus-date in the
                    middle of the order.

    page 33         "(1) The provisions of Executive Order No. 30 dated
                    November 30, 1970 ... are hereby suspended" — a body
                    CROSS-REFERENCE. These orders amend each other constantly,
                    and treating every mention as a page "about" another
                    instrument cut long orders apart section by section.
"""

from __future__ import annotations

import json
from pathlib import Path

from nyc_executive_orders import volume_split as vs

FIXTURES = Path(__file__).parent / "fixtures" / "pre1974_lindsay"

MIN_YEAR, MAX_YEAR = 1972, 1973


def load_page(number: int) -> dict:
    return json.loads((FIXTURES / f"page_{number:04d}.json").read_text(encoding="utf-8"))


def load_all() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(FIXTURES.glob("page_*.json"))]


def classify(number: int, *, allow_bare_label: bool = False) -> vs.PageInfo:
    return vs.classify_page(load_page(number), min_year=MIN_YEAR, max_year=MAX_YEAR,
                            allow_bare_label=allow_bare_label)


def head_of(number: int) -> list[str]:
    return vs.page_lines(load_page(number))[:vs.HEAD_LINES]


# --------------------------------------------------------------------------- #
# 1: a body sentence naming the instrument is not an instrument label           #
# --------------------------------------------------------------------------- #

def test_body_sentence_naming_the_instrument_does_not_open_a_document():
    """Page 108's body says "...by this Executive Order." — mid-sentence.

    Even with the fallback enabled (the un-numbered volume's setting), a label
    buried in prose must not be read as a label at all.
    """
    assert vs.find_instrument_label(head_of(108), allow_bare_label=True) \
        == (None, None, None, None)


def test_closing_page_of_the_previous_order_does_not_open_a_document():
    """Page 106 reads "This Executive Order shall take effect immediately."."""
    assert vs.find_instrument_label(head_of(106), allow_bare_label=True) \
        == (None, None, None, None)


def test_bare_label_must_start_its_line():
    assert vs._bare_label_opens_line("EXECUTIVE MEMORANDA") is not None
    assert vs._bare_label_opens_line("## EXECUTIVE ORDER") is not None
    # A title line may carry a short subject after the label.
    assert vs._bare_label_opens_line("EXECUTIVE ORDER - LABOR RELATIONS") is not None
    # ...but a sentence may not, whether the label starts it or not.
    assert vs._bare_label_opens_line(
        "This Executive Order shall take effect immediately."
    ) is None
    assert vs._bare_label_opens_line(
        "Executive Order No. 40 is hereby revoked and the following substituted "
        "in its place, to take effect immediately."
    ) is None


def test_page_ornament_outranks_an_unnumbered_label():
    """"-2-" at the top of page 108 is decisive: a first page is never "-2-"."""
    assert classify(108, allow_bare_label=True).role == vs.ROLE_CONTINUATION


def test_a_numbered_volume_never_uses_the_bare_label_fallback():
    """The fallback is for the one volume that prints no numbers at all."""
    split = vs.split_volume(load_all(), min_year=MIN_YEAR, max_year=MAX_YEAR)
    assert all(d.number or d.printed_label is None for d in split.documents)


# --------------------------------------------------------------------------- #
# 2: letterhead is a line, not a phrase found somewhere in a paragraph          #
# --------------------------------------------------------------------------- #

def test_letterhead_anchors_inside_a_body_paragraph_do_not_count():
    """Page 32 contains both anchors — inside one 154-token sentence."""
    assert vs._count_letterhead_anchors(head_of(32)) < vs.MIN_LETTERHEAD_ANCHORS
    assert not vs._is_letterhead_line(
        "§5. All of the powers of budget modification, as granted in Section "
        "124 (a) of the New York City Charter are hereby delegated to the "
        "Office of the Mayor for the duration of this order."
    )


def test_real_letterhead_still_counts():
    """Page 107 prints the block properly, one anchor per line."""
    assert vs._count_letterhead_anchors(head_of(107)) >= vs.MIN_LETTERHEAD_ANCHORS
    assert vs._is_letterhead_line("THE CITY OF NEW YORK")
    assert vs._is_letterhead_line("OFFICE OF THE MAYOR")
    # ...and still counts when the model folds the whole block onto one line.
    assert vs._is_letterhead_line("CITY OF NEW YORK OFFICE OF THE MAYOR")


# --------------------------------------------------------------------------- #
# 3: an order that amends another order is not a page "about" that order        #
# --------------------------------------------------------------------------- #

def test_body_cross_reference_is_not_a_referential_page():
    """Page 33 cites Executive Order No. 30 in the middle of a numbered section."""
    assert not vs.has_referential_instrument_mention(head_of(33))
    assert classify(33).role == vs.ROLE_CONTINUATION


def test_an_explicit_re_line_is_still_referential():
    """The narrowing must not lose the cover-note case the check exists for."""
    assert vs.has_referential_instrument_mention(
        ["January 25, 1946", "Re: Memorandum #2 - 1/10/46 (attached)"]
    )
    assert vs.has_referential_instrument_mention(
        ["In answer to your Memorandum No.20 we wish to inform you..."]
    )


# --------------------------------------------------------------------------- #
# The assembled documents                                                       #
# --------------------------------------------------------------------------- #

def test_page_roles():
    assert classify(31).role == vs.ROLE_START
    assert classify(31).number == "70"
    assert classify(32).role == vs.ROLE_CONTINUATION
    assert classify(33).role == vs.ROLE_CONTINUATION
    assert classify(106).role == vs.ROLE_CONTINUATION
    assert classify(107).role == vs.ROLE_START
    assert classify(107).number == "84"
    for page in (108, 109, 110):
        assert classify(page).role == vs.ROLE_CONTINUATION, page
    assert classify(111).role == vs.ROLE_START
    assert classify(111).number == "85"


def test_order_84_spans_all_four_of_its_pages():
    split = vs.split_volume(load_all(), min_year=MIN_YEAR, max_year=MAX_YEAR)
    by_number = {d.number: d for d in split.documents if d.number}
    assert by_number["84"].pages == [107, 108, 109, 110]
    assert by_number["84"].date_on_page == "1973-08-02"
    # Its last page's sign-off is inside the record, not in a phantom next door.
    assert by_number["84"].signed_by == "JOHN V. LINDSAY"
    assert "Section 1. There shall be created" in by_number["84"].body
    assert "This Order shall take effect immediately." in by_number["84"].body


def test_order_70_keeps_its_continuation_pages():
    """32 and 33 attach to 70.

    Only a prefix is asserted: the fixture set is deliberately non-contiguous
    (31-33 and 106-111 out of a 129-page volume), so page 106 — the last page
    of Executive Order 83, whose own opening page is not a fixture here — also
    lands on 70. In the full volume it attaches to 83, as ``test_page_roles``
    pins by classifying it CONTINUATION.
    """
    split = vs.split_volume(load_all(), min_year=MIN_YEAR, max_year=MAX_YEAR)
    by_number = {d.number: d for d in split.documents if d.number}
    assert by_number["70"].pages[:3] == [31, 32, 33]


def test_no_phantom_documents_between_the_numbered_orders():
    """Nothing unnumbered may sit between two consecutive numbered orders."""
    split = vs.split_volume(load_all(), min_year=MIN_YEAR, max_year=MAX_YEAR)
    numbers = [d.number for d in split.documents]
    assert numbers == ["70", "84", "85"]
