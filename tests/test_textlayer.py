"""Text-layer probe: the three-way split, the per-page signal, plus edges.

The gate has two probes, and the second exists because the first cannot tell a
word processor's output from a scan somebody already ran through OCR. Before it
was added, 665 of 1,205 records carried `text_source: born-digital` while being
scans, and 59 inked pages inside otherwise-passing documents were published as
nothing (born-digital-audit.md, 2026-09-01).
"""

from __future__ import annotations

import json

from nyc_executive_orders.textlayer import (
    CLASS_ERROR,
    CLASS_OCR_LAYER,
    CLASS_SCANNED,
    CLASS_TEXT,
    TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD,
    classify_pdf,
    summarize,
    write_textlayer_report,
)


def test_born_digital_classifies_text(born_digital_pdf):
    result = classify_pdf(born_digital_pdf)
    assert result.classification == CLASS_TEXT
    assert result.page_count == 1
    assert result.chars_per_page > TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD
    assert result.needs_ocr is False


def test_scanned_classifies_scanned(scanned_pdf):
    result = classify_pdf(scanned_pdf)
    assert result.classification == CLASS_SCANNED
    assert result.page_count == 1
    assert result.chars_per_page <= TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD
    assert result.needs_ocr is True


def test_missing_file_is_error():
    result = classify_pdf("/no/such/file.pdf")
    assert result.classification == CLASS_ERROR
    assert result.error == "file not found"


def test_threshold_is_boundary(born_digital_pdf):
    # The born-digital fixture is ~252 chars/page. With the threshold raised above
    # that, it flips to scanned — proving the classification hinges on the constant.
    high = classify_pdf(born_digital_pdf, threshold=10_000)
    assert high.classification == CLASS_SCANNED


def test_summarize_counts(born_digital_pdf, scanned_pdf):
    results = [classify_pdf(born_digital_pdf), classify_pdf(scanned_pdf)]
    counts = summarize(results)
    assert counts[CLASS_TEXT] == 1
    assert counts[CLASS_SCANNED] == 1
    assert counts[CLASS_ERROR] == 0


def test_write_report_is_valid_json(tmp_path, born_digital_pdf, scanned_pdf):
    results = [classify_pdf(born_digital_pdf), classify_pdf(scanned_pdf)]
    out = write_textlayer_report(results, tmp_path / "report.json")
    payload = json.loads(out.read_text())
    assert payload["threshold_chars_per_page"] == TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD
    assert payload["summary"][CLASS_TEXT] == 1
    assert payload["summary"][CLASS_SCANNED] == 1
    assert len(payload["records"]) == 2
    # Report writes are idempotent — same input, same bytes on re-run.
    first_bytes = out.read_text()
    write_textlayer_report(results, tmp_path / "report.json")
    assert out.read_text() == first_bytes


# --------------------------------------------------------------------------- #
# The second probe: a text layer is not proof of a born-digital document        #
# --------------------------------------------------------------------------- #

def test_an_ocr_overlay_is_not_born_digital(ocr_layer_pdf):
    """The whole point of the second probe. This PDF is a page image with the
    recognized text stamped over it invisibly, so it is dense in characters and
    reads exactly like a word processor's output to a density-only gate."""
    result = classify_pdf(ocr_layer_pdf)
    assert result.chars_per_page > TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD
    assert result.classification == CLASS_OCR_LAYER
    assert result.invisible_share == 1.0


def test_an_ocr_overlay_still_needs_ocr(ocr_layer_pdf):
    """A scan is a scan whether or not somebody already ran OCR over it. This is
    the property that puts these documents back on the VLM worklist."""
    assert classify_pdf(ocr_layer_pdf).needs_ocr is True


def test_genuine_born_digital_has_no_invisible_text(born_digital_pdf):
    result = classify_pdf(born_digital_pdf)
    assert result.invisible_share == 0.0
    assert result.invisible_chars == 0
    assert result.visible_chars == result.traced_chars


def test_invisible_share_is_zero_when_nothing_was_traced(scanned_pdf):
    """An image-only page traces no characters; the share must not divide by zero."""
    assert classify_pdf(scanned_pdf).invisible_share == 0.0


def test_the_invisible_threshold_is_what_decides(ocr_layer_pdf):
    """Proves the split hinges on the constant, mirroring the density test above."""
    assert classify_pdf(ocr_layer_pdf,
                        invisible_threshold=1.5).classification == CLASS_TEXT


# --------------------------------------------------------------------------- #
# Per-page: a mean hides an image-only page inside a passing document           #
# --------------------------------------------------------------------------- #

def test_an_image_only_page_inside_a_passing_document_is_reported(mixed_pages_pdf):
    """The 2025-EO-057 shape. The document passes the density gate on its mean,
    so nothing stops it; page 2 then contributes nothing to the emitted body.
    Before page_chars existed there was no code path that said so."""
    result = classify_pdf(mixed_pages_pdf)
    assert result.classification == CLASS_TEXT
    assert result.chars_per_page > TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD
    assert result.page_count == 2
    assert result.page_chars[0] > TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD
    assert result.page_chars[1] == 0
    assert result.image_only_pages == (2,)


def test_page_numbers_are_one_based(mixed_pages_pdf):
    """They are printed for a human to open the PDF and look at the page."""
    assert min(classify_pdf(mixed_pages_pdf).image_only_pages) >= 1


def test_a_whole_document_is_not_reported_as_image_only_pages(scanned_pdf):
    """A document that failed the density gate is `scanned`; listing every one of
    its pages as a lost page would be noise, not a signal."""
    assert classify_pdf(scanned_pdf).image_only_pages == ()


def test_a_clean_single_page_reports_no_lost_pages(born_digital_pdf):
    assert classify_pdf(born_digital_pdf).image_only_pages == ()


# --------------------------------------------------------------------------- #
# The report                                                                    #
# --------------------------------------------------------------------------- #

def test_report_carries_the_evidence_and_both_thresholds(tmp_path, ocr_layer_pdf,
                                                         mixed_pages_pdf):
    """index/textlayer_report.json is where the per-page detail is published; the
    corpus record schema is deliberately locked and does not grow for this."""
    results = [classify_pdf(ocr_layer_pdf), classify_pdf(mixed_pages_pdf)]
    out = write_textlayer_report(results, tmp_path / "textlayer_report.json")
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert "threshold_invisible_char_share" in payload
    assert payload["summary"][CLASS_OCR_LAYER] == 1
    overlay, mixed = payload["records"]
    assert overlay["invisible_share"] == 1.0
    assert mixed["page_chars"] == [252, 0]
    assert mixed["image_only_pages"] == [2]


def test_summarize_counts_the_new_class(born_digital_pdf, ocr_layer_pdf, scanned_pdf):
    counts = summarize([classify_pdf(p) for p in
                        (born_digital_pdf, ocr_layer_pdf, scanned_pdf)])
    assert counts == {CLASS_TEXT: 1, CLASS_OCR_LAYER: 1,
                      CLASS_SCANNED: 1, CLASS_ERROR: 0}
