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


# --------------------------------------------------------------------------- #
# Paragraph geometry                                                            #
# --------------------------------------------------------------------------- #

def test_wide_leading_becomes_a_paragraph_break(paragraphs_pdf):
    """PyMuPDF returns one line per printed line and nothing about paragraphs, so
    a PDF that separates blocks with leading rather than a blank line arrived as
    one wall of text. 664 of the born-digital bodies had no blank line anywhere."""
    body = extract_pdf_text(paragraphs_pdf).text
    assert body.count("\n\n") == 4
    assert "printed lines and\nmust therefore stay" in body, "tight leading holds"
    assert "emitted body; and\n\nWHEREAS, the second" in body, "wide leading breaks"


def test_paragraph_breaks_add_no_content(paragraphs_pdf):
    """The rule inserts newlines and must never alter, reorder or drop a
    character. Verified against all 1,205 corpus PDFs with a text layer; this
    pins the property in the suite."""
    import re

    import fitz

    doc = fitz.open(paragraphs_pdf)
    plain = "".join(page.get_text("text") for page in doc)
    doc.close()
    body = extract_pdf_text(paragraphs_pdf).text
    assert re.sub(r"\s+", "", body) == re.sub(r"\s+", "", plain)


def test_a_short_page_is_left_alone(born_digital_pdf):
    """Too few line gaps for a median to mean anything — a title page, a
    signature block, a one-clause order. Guessing there would be worse than
    leaving the page as PyMuPDF returned it."""
    import fitz

    from nyc_executive_orders.extract import _page_paragraphs

    doc = fitz.open(born_digital_pdf)
    page = doc[0]
    assert _page_paragraphs(page) == page.get_text("text")
    doc.close()


def test_an_image_only_page_contributes_nothing_and_does_not_crash(mixed_pages_pdf):
    """Page 2 has an image block and no text block, so there is no geometry to
    read. It must fall back rather than raise."""
    result = extract_pdf_text(mixed_pages_pdf)
    assert result.page_count == 2
    assert result.has_text
    assert "OVERSIGHT" in result.text
