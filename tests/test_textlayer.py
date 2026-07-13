"""Text-layer probe: born-digital -> text, image-only -> scanned, plus edges."""

from __future__ import annotations

import json

from nyc_executive_orders.textlayer import (
    CLASS_ERROR,
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
