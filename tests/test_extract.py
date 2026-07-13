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
