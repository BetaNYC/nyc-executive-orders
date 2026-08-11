"""Regression tests for two segmentation bugs found while building the
O'Dwyer-Impellitteri volume (1946-01-07 .. 1950-10-04).

Every fixture under ``tests/fixtures/pre1974_odwyer/`` is a REAL page record
copied verbatim from that volume's committed OCR (same convention as
``tests/fixtures/pre1974/``), chosen to reproduce two false "duplicate-number"
reconciliation flags that turned out to be segmentation bugs, not genuine
reissues:

    page 34/36    "MEMORANDUM NO. 10" repeats as a running header on its own
                  continuation page ("-page 2-"); page 36 was misread as a
                  new document instead of memorandum 10's second page.
    page 185/187  same bug, the "-2-" ornament shape.
    page 11       a cover note that reads "Re: Memorandum #2 - 1/10/46
                  (attached)" was misread as memorandum 2 itself; the real
                  memorandum 2 is pages 13/15.
    page 69/70    a reply letter ("In answer to your Memorandum No.20 we wish
                  to inform...") was misread as memorandum 20 itself.
    page 80       guard against a fix regression: "MEMORANDUM NO. 25" here is
                  a GENUINE opening, but the model tagged it Section-header,
                  so :func:`element_text` prefixes it "## MEMORANDUM NO. 25" —
                  the position check in :func:`find_instrument_label` must
                  still recognize it as starting the line.
"""

from __future__ import annotations

import json
from pathlib import Path

from nyc_executive_orders import volume_split as vs

FIXTURES = Path(__file__).parent / "fixtures" / "pre1974_odwyer"

MIN_YEAR, MAX_YEAR = 1946, 1950


def load_page(number: int) -> dict:
    return json.loads((FIXTURES / f"page_{number:04d}.json").read_text(encoding="utf-8"))


def classify(number: int) -> vs.PageInfo:
    return vs.classify_page(load_page(number), min_year=MIN_YEAR, max_year=MAX_YEAR)


# --------------------------------------------------------------------------- #
# Running-header continuations (page-number ornament follows a repeated label) #
# --------------------------------------------------------------------------- #

def test_running_header_with_page_ornament_is_a_continuation():
    """Page 36 reads "MEMORANDUM NO. 10 / -page 2- / 8/26/46 / ...quote
    continues mid-sentence..." — it must attach to memorandum 10, not reopen it.
    """
    opener = classify(34)
    assert opener.role == vs.ROLE_START
    assert opener.number == "10"

    continuation = classify(36)
    assert continuation.role == vs.ROLE_CONTINUATION


def test_running_header_with_dash_ornament_is_a_continuation():
    """Same bug, the "-2-" ornament shape: page 187 continues memorandum 51."""
    opener = classify(185)
    assert opener.role == vs.ROLE_START
    assert opener.number == "51"

    continuation = classify(187)
    assert continuation.role == vs.ROLE_CONTINUATION


# --------------------------------------------------------------------------- #
# Reference bleed (a page that talks ABOUT another instrument)                  #
# --------------------------------------------------------------------------- #

def test_re_line_does_not_mint_the_referenced_number():
    """Page 11's "Re: Memorandum #2 - 1/10/46 (attached)" must not make this
    page memorandum 2 — the real memorandum 2 is pages 13/15.
    """
    info = classify(11)
    assert info.role == vs.ROLE_START
    assert info.number is None
    assert "referential-label-ignored" in info.flags

    real_opener = classify(13)
    assert real_opener.role == vs.ROLE_START
    assert real_opener.number == "2"


def test_reply_letter_does_not_mint_the_referenced_number():
    """Page 70's "In answer to your Memorandum No.20 we wish to inform..." is
    a reply, not memorandum 20 itself.
    """
    assert classify(69).role == vs.ROLE_BLANK

    info = classify(70)
    assert info.role == vs.ROLE_START
    assert info.number is None
    assert "referential-label-ignored" in info.flags


# --------------------------------------------------------------------------- #
# Regression guard: a heading-prefixed genuine label must still be found       #
# --------------------------------------------------------------------------- #

def test_section_header_prefixed_label_is_still_found():
    """The model tagged "MEMORANDUM NO. 25" a Section-header, so
    :func:`element_text` renders it "## MEMORANDUM NO. 25". The position check
    that rejects mid-sentence mentions must strip that heading mark first.
    """
    info = classify(80)
    assert info.role == vs.ROLE_START
    assert info.number == "25"
