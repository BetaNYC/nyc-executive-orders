"""vlm_pages — the corpus renderer over committed dots.ocr page records.

Offline: every input is a dict in the shape vlm_ocr writes, or a committed
fixture record. No model, no PDF, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyc_executive_orders import vlm_pages
from nyc_executive_orders.vlm_pages import (
    DocumentText,
    document_text,
    element_text,
    flatten_table_html,
    page_flags,
    page_lines,
)

FIXTURES = Path(__file__).parent / "fixtures"


def page(elements, **extra):
    """A minimal page record in vlm_ocr's shape."""
    return {"page": extra.pop("page", 1), "elements": elements, **extra}


def el(category, text, **extra):
    return {"bbox": [0, 0, 10, 10], "category": category, "text": text, **extra}


# --------------------------------------------------------------------------- #
# element_text                                                                  #
# --------------------------------------------------------------------------- #

def test_picture_is_dropped_not_placeholdered():
    """vlm_ocr renders Pictures as '*[Picture - bbox ...]*'; a corpus body must not."""
    assert element_text(el("Picture", "")) == ""
    assert element_text({"bbox": [0, 0, 1, 1], "category": "Picture"}) == ""


def test_page_header_is_kept_verbatim():
    """The letterhead IS clean._tier's anchor. Dropping it as furniture would flip
    ~1,000 post-1974 records to needs-review and empty their dropped_header."""
    header = "THE CITY OF NEW YORK\nOFFICE OF THE MAYOR"
    assert element_text(el("Page-header", header)) == header
    assert element_text(el("Page-footer", "-2-")) == "-2-"


def test_heading_marks_on_is_the_pre1974_behavior():
    assert element_text(el("Title", "EXECUTIVE ORDER NO. 5")) == "# EXECUTIVE ORDER NO. 5"
    assert element_text(el("Section-header", "PRINCIPLES")) == "## PRINCIPLES"


def test_heading_marks_off_emits_plain_text():
    assert element_text(el("Title", "EXECUTIVE ORDER NO. 5"), heading_marks=False) == (
        "EXECUTIVE ORDER NO. 5"
    )
    assert element_text(el("Section-header", "PRINCIPLES"), heading_marks=False) == "PRINCIPLES"


def test_models_own_hashes_are_collapsed_either_way():
    """The model emits its own markdown inside `text`, which would otherwise
    yield '## ## PRINCIPLES'."""
    assert element_text(el("Section-header", "## PRINCIPLES")) == "## PRINCIPLES"
    assert element_text(el("Section-header", "## PRINCIPLES"), heading_marks=False) == "PRINCIPLES"


def test_only_the_first_line_of_a_multiline_heading_is_the_heading():
    folded = "EXECUTIVE ORDER NO. 5\nJanuary 2, 1974"
    assert element_text(el("Title", folded)) == "# EXECUTIVE ORDER NO. 5\nJanuary 2, 1974"


def test_empty_and_whitespace_elements_render_to_nothing():
    assert element_text(el("Text", "")) == ""
    assert element_text(el("Text", "   \n  ")) == ""
    assert element_text(el("Title", "###")) == ""


def test_unknown_category_passes_its_text_through():
    assert element_text(el("Handwriting", "signed, Abraham D. Beame")) == (
        "signed, Abraham D. Beame"
    )


# --------------------------------------------------------------------------- #
# flatten_table_html                                                            #
# --------------------------------------------------------------------------- #

def test_table_is_raw_html_by_default_and_flat_when_asked():
    """Default False keeps pre-1974 byte-identical: page 1 of the O'Dwyer volume
    is a library date-stamp the model emits as a <table>, and flattening it
    changes whether that page classifies as furniture."""
    markup = "<table><tr><td>A</td><td>B</td></tr></table>"
    assert element_text(el("Table", markup)) == markup
    assert element_text(el("Table", markup), flatten_tables=True) == "A | B"


def test_flatten_real_fixture_table():
    record = json.loads((FIXTURES / "pre1974_odwyer" / "page_0080.json").read_text())
    markup = next(e["text"] for e in record["elements"] if e["category"] == "Table")
    assert flatten_table_html(markup) == (
        "MUNICIPAL REFERENCE LIBRARY RECEIVED\nJUL 14 1947\nMUNICIPAL BUILDING NEW YORK CITY"
    )


def test_flatten_unescapes_entities_and_keeps_cell_order():
    assert flatten_table_html(
        "<table><tr><td>Fees &amp; Charges</td><td>$1,000</td></tr></table>"
    ) == "Fees & Charges | $1,000"


@pytest.mark.parametrize(
    "markup",
    [
        "not a table at all &amp; broken <td>x",
        "stray text <table><tr><td>A</td></tr></table> more stray",
        "<table><tr><td>A</td></tr>",
    ],
)
def test_flatten_never_loses_a_word(markup):
    """Losing a word from an archived order is far worse than losing its layout,
    so malformed markup falls back to its stripped text."""
    flat = flatten_table_html(markup)
    stripped = vlm_pages._strip_markup(markup)
    assert flat.replace("|", " ").split() == stripped.split()


def test_flatten_empty_table_is_empty():
    assert flatten_table_html("<table></table>") == ""


# --------------------------------------------------------------------------- #
# page_lines                                                                    #
# --------------------------------------------------------------------------- #

def test_page_lines_preserves_reading_order_and_splits_lines():
    record = page([
        el("Page-header", "THE CITY OF NEW YORK\nOFFICE OF THE MAYOR"),
        el("Picture", ""),
        el("Text", "WHEREAS, ..."),
    ])
    assert page_lines(record) == [
        "THE CITY OF NEW YORK",
        "OFFICE OF THE MAYOR",
        "WHEREAS, ...",
    ]


def test_page_lines_of_a_page_with_no_elements_is_empty():
    assert page_lines(page([])) == []


# --------------------------------------------------------------------------- #
# page_flags                                                                    #
# --------------------------------------------------------------------------- #

def test_flags_for_a_clean_page_are_empty():
    assert page_flags(page([el("Text", "hi")], finish_reason="stop")) == []


def test_each_failure_signal_raises_its_flag():
    assert "parse-error" in page_flags(page([], parse_error="no JSON found"))
    assert "truncated" in page_flags(page([], finish_reason="length"))
    assert "decode-mismatch" in page_flags(page([], debug_decode_matches_stream=False))
    assert "uncovered-ink" in page_flags(
        page([], ink_coverage={"uncovered_regions": [{"ink_px": 900}]})
    )
    assert "low-ink-coverage:0.900" in page_flags(
        page([], ink_coverage={"covered_fraction": 0.9})
    )
    assert "low-confidence:3" in page_flags(
        page([], logprobs={"content": {"n_below_threshold": 3}})
    )


def test_no_measurable_ink_is_off_by_default():
    """On by default would add a flag to already-committed pre-1974 provenance."""
    record = page([el("Text", "hi")], page_stats={"dark_fraction": 0.0, "std": 14.8})
    assert page_flags(record) == []
    assert page_flags(record, flag_no_measurable_ink=True) == ["no-measurable-ink"]


def test_no_measurable_ink_does_not_fire_on_a_skipped_page():
    """A skipped page is *expected* to have no ink; the flag is about a page that
    was OCR'd and therefore silently got no ink_coverage QA at all."""
    record = page([], skipped="blank_or_bleedthrough", page_stats={"dark_fraction": 0.0})
    assert page_flags(record, flag_no_measurable_ink=True) == []


# --------------------------------------------------------------------------- #
# document_text                                                                 #
# --------------------------------------------------------------------------- #

def test_document_text_joins_pages_in_order_with_a_blank_line():
    doc = document_text([
        page([el("Text", "second")], page=2),
        page([el("Text", "first")], page=1),
    ])
    assert doc.text == "first\n\nsecond"
    assert doc.page_count == 2
    assert doc.pages_with_text == 2
    assert doc.has_text


def test_document_text_defaults_are_the_post1974_shape():
    doc = document_text([
        page([
            el("Title", "## EXECUTIVE ORDER NO. 5"),
            el("Table", "<table><tr><td>A</td><td>B</td></tr></table>"),
        ])
    ])
    # Elements within one page are consecutive lines (pre-1974's behavior); the
    # blank line separates PAGES, not elements.
    assert doc.text == "EXECUTIVE ORDER NO. 5\nA | B"
    assert doc.tables == 1


def test_skipped_page_contributes_no_text_and_no_marker():
    doc = document_text([
        page([el("Text", "real")], page=1),
        page([], page=2, skipped="blank_or_bleedthrough"),
    ])
    assert doc.text == "real"
    assert doc.pages_skipped == 1
    assert "skipped" not in doc.text


def test_parse_error_raw_text_never_reaches_the_body():
    """raw_text is model JSON scaffolding, not a transcription. It stays in the
    committed record for a human and is flagged."""
    doc = document_text([
        page([], page=1, parse_error="unterminated string", raw_text='{"eleme')
    ])
    assert doc.text == ""
    assert "parse-error" in doc.flags
    assert "empty-output" in doc.flags
    assert not doc.has_text


def test_all_pages_blank_is_flagged_distinctly_from_empty_output():
    doc = document_text([page([], page=1, skipped="blank_or_bleedthrough")])
    assert "all-pages-blank" in doc.flags
    assert "empty-output" not in doc.flags


def test_no_pages_recorded_is_flagged():
    doc = document_text([])
    assert doc.flags == ["no-pages-recorded"]
    assert doc.page_count == 0


def test_flags_are_deduplicated_and_order_stable():
    doc = document_text([
        page([el("Text", "a")], page=1, finish_reason="length"),
        page([el("Text", "b")], page=2, finish_reason="length", parse_error="x"),
    ])
    assert doc.flags == ["truncated", "parse-error"]


def test_dehyphenation_rejoins_soft_wraps_and_can_be_turned_off():
    record = page([el("Text", "the adminis-\ntration shall")])
    assert document_text([record]).text == "the administration shall"
    assert document_text([record], dehyphenate=False).text == "the adminis-\ntration shall"


def test_counts_elements_tables_and_pictures():
    doc = document_text([
        page([
            el("Picture", ""),
            el("Text", "body"),
            el("Table", "<table><tr><td>A</td></tr></table>"),
        ])
    ])
    assert doc.pictures == 1
    assert doc.tables == 1
    assert doc.element_counts == {"Picture": 1, "Text": 1, "Table": 1}


def test_blank_override_is_carried_up_from_the_pages():
    assert document_text([page([el("Text", "x")], blank_override=True)]).blank_override
    assert not document_text([page([el("Text", "x")])]).blank_override


def test_document_text_over_a_real_fixture_page():
    record = json.loads((FIXTURES / "pre1974" / "page_0028.json").read_text())
    doc = document_text([record])
    assert doc.has_text
    assert "#" not in doc.text          # heading_marks off by default
    assert "<" not in doc.text          # tables flattened
    assert "[Picture" not in doc.text   # placeholders dropped
    assert isinstance(doc, DocumentText)
