"""Born-digital extraction + text cleaning (dehyphenation, whitespace, paras)."""

from __future__ import annotations

from nyc_executive_orders.extract import (
    TEXT_SOURCE_BORN_DIGITAL,
    clean_text,
    extract_pdf_text,
)


def test_dehyphenation_rejoins_soft_wrap():
    # A word-char + hyphen + newline + lowercase => rejoined.
    assert clean_text("adminis-\ntration") == "administration"


def test_dehyphenation_leaves_capitalized_next_token():
    # Next line starts uppercase => a genuine dash/compound, not a soft wrap.
    assert clean_text("New-\nYork") == "New-\nYork"


def test_whitespace_collapse_and_paragraphs():
    raw = "line   one  \n\n\n\nline    two"
    cleaned = clean_text(raw)
    # Intra-line runs collapse; 3+ blank lines collapse to a single paragraph break.
    assert cleaned == "line one\n\nline two"


def test_clean_text_strips_edges():
    assert clean_text("\n\n  hello  \n\n") == "hello"


def test_extract_born_digital_fixture(born_digital_pdf):
    result = extract_pdf_text(born_digital_pdf)
    assert result.text_source == TEXT_SOURCE_BORN_DIGITAL
    assert result.page_count == 1
    assert result.has_text
    # The hyphenated wrap in the fixture ("adminis-" / "tration") is rejoined.
    assert "administration" in result.text
    assert "adminis-" not in result.text
    # Distinctive token survived extraction.
    assert "OVERSIGHT" in result.text
    assert result.char_count == len(result.text)


# --------------------------------------------------------------------------- #
# The wrap rule: fragments are not words                                        #
# --------------------------------------------------------------------------- #

def test_a_genuine_compound_keeps_its_hyphen():
    """The old rule tested capitalization and glued this into `publicprivate` —
    while its own docstring offered it as the example it protected."""
    assert clean_text("a public-\nprivate deal") == "a public-private deal"


def test_a_multi_hyphen_compound_keeps_its_hyphen():
    """32 corpus records published `person-toperson` (corpus/2022/2022-EEO-255.md
    and 29 others, plus 2 ocr-vlm bodies)."""
    assert clean_text("a person-to-\nperson call") == "a person-to-person call"
    assert clean_text("a not-\nfor-profit entity") == "a not-for-profit entity"


def test_a_word_fragment_still_joins():
    """Neither half is a word, so the hyphen was the typesetter's."""
    assert clean_text("data adminis-\ntration") == "data administration"


# --------------------------------------------------------------------------- #
# Soft hyphens                                                                  #
# --------------------------------------------------------------------------- #

def test_a_soft_hyphen_at_a_wrap_is_a_hyphen():
    """U+00AD prints as a hyphen at a line break, so it means what "-" means and
    gets the same lexicon test. `ready-mixed` is corpus/2022/2022-EO-023.md."""
    assert clean_text("person-to­\nperson") == "person-to-person"
    assert clean_text("ready­\nmixed concrete") == "ready-mixed concrete"


def test_a_soft_hyphen_inside_a_word_still_joins():
    assert clean_text("admin­\nistration") == "administration"


def test_a_stray_soft_hyphen_is_removed():
    """135 records published one verbatim, so a search for the word missed them."""
    assert clean_text("person-to-­person") == "person-to-person"
    assert "­" not in clean_text("a soft­hyphen here")


def test_other_invisible_marks_are_removed():
    assert clean_text("zero​width﻿ space") == "zerowidth space"
