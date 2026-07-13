"""Local OCR path — real ocrmypdf on the scanned fixture, plus failure handling.

The real-OCR test is marked ``slow`` (it shells out to Tesseract/Ghostscript).
It is skipped where ocrmypdf isn't installed (the `ocr` extra), so the suite
stays green on a born-digital-only install and on a Python where ocrmypdf's
native deps aren't available.
"""

from __future__ import annotations

import pytest

from nyc_executive_orders.ocr import (
    TEXT_SOURCE_OCR,
    TEXT_SOURCE_OCR_FAILED,
    OcrConfig,
    ocr_and_extract,
)


def test_ocr_config_defaults_are_explicit():
    cfg = OcrConfig()
    assert cfg.language == "eng"
    assert cfg.rotate_pages is True
    assert cfg.deskew is True
    assert cfg.clean is False
    assert cfg.force_ocr is True


@pytest.mark.slow
def test_ocr_recovers_text_from_scanned_fixture(scanned_pdf):
    pytest.importorskip("ocrmypdf")
    result = ocr_and_extract(scanned_pdf)
    assert result.text_source == TEXT_SOURCE_OCR
    assert result.has_text
    # OCR should recover the distinctive tokens rendered into the raster.
    assert "EXECUTIVE" in result.text.upper()
    assert "OVERSIGHT" in result.text.upper()
    # The same cleaning pipeline dehyphenates the OCR'd soft wrap.
    assert "administration" in result.text.lower()


def test_ocr_failure_is_flagged_not_escalated(tmp_path):
    """A corrupt input must yield ocr-failed — never a crash, never a cloud call.

    We hand ocr_and_extract a non-PDF file; ocrmypdf raises locally, and the
    result is flagged ``ocr-failed`` with empty text (the §7 posture: halt +
    flag, no auto-escalation).
    """
    pytest.importorskip("ocrmypdf")
    bogus = tmp_path / "not_a.pdf"
    bogus.write_bytes(b"this is not a pdf")
    result = ocr_and_extract(bogus)
    assert result.text_source == TEXT_SOURCE_OCR_FAILED
    assert result.text == ""
    assert result.char_count == 0
