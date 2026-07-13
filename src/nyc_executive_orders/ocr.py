"""Local OCR for scanned PDFs — LOCAL ONLY, hard cloud gate (standards §7).

Scanned EO PDFs (classified by :mod:`textlayer`) have no recoverable text layer.
This module adds one *locally* with ``ocrmypdf`` (Tesseract), then extracts the
text through the same cleaning path as born-digital orders (:mod:`extract`), so
the two bodies read consistently.

    HARD RULE (engineering-standards §7 — data egress, two-forces gate):
    THIS MODULE MAKES NO NETWORK CALLS AND HAS NO CLOUD FALLBACK. EVER.
    ``ocrmypdf``/Tesseract/Ghostscript run entirely on-machine. If local OCR
    fails on a document we record it as ``ocr-failed`` and flag it — we NEVER
    auto-escalate to Google Document AI, AWS Textract, or any external service.
    Cloud processing of these public records is *allowable on demonstrated need*
    (Force 1/2 don't bind public civic PDFs), but only as a deliberate,
    per-use, operator-authorized action — never a silent fallback from here.

``ocrmypdf`` is an OPTIONAL dependency (the ``ocr`` extra), lazily imported so
the born-digital-only pipeline (``run_parse --no-ocr``) installs and runs without
it — mirroring how Playwright is optional for the WAF fetch path.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .extract import ExtractResult, extract_pdf_text

logger = logging.getLogger("nyc_executive_orders.ocr")

# Provenance tags stamped on the emitted record's `text_source`.
TEXT_SOURCE_OCR = "ocr"                # local OCR succeeded
TEXT_SOURCE_OCR_FAILED = "ocr-failed"  # local OCR failed; flagged, NOT escalated


@dataclass(frozen=True)
class OcrConfig:
    """Explicit OCR quality knobs (no hidden defaults buried in a call site).

    Defaults suit scanned government letterhead: rotate + deskew help crooked
    scans; ``force_ocr`` rasterizes and OCRs every page (the docs are ~image-only
    already, and it sidesteps ``PriorOcrFoundError`` on pages carrying a scrap of
    stray text). ``clean`` (unpaper) is OFF by default — it needs the extra
    ``unpaper`` binary and rarely helps clean municipal scans.
    """

    language: str = "eng"
    rotate_pages: bool = True
    deskew: bool = True
    clean: bool = False
    force_ocr: bool = True
    optimize: int = 0


def _run_ocrmypdf(input_pdf: Path, output_pdf: Path, config: OcrConfig) -> None:
    """Invoke ocrmypdf locally. Lazily imported so it stays an optional dep.

    Built against the documented ``ocrmypdf.ocr(input_file, output_file, **kw)``
    API (verified against the installed 17.8 signature: ``input_file_or_options,
    output_file, language, ... rotate_pages, deskew, clean, force_ocr,
    optimize, progress_bar``). No guessed parameters (standards §0).
    """
    import ocrmypdf  # noqa: PLC0415 - lazy import keeps ocrmypdf optional

    ocrmypdf.ocr(
        input_pdf,
        output_pdf,
        language=config.language,
        rotate_pages=config.rotate_pages,
        deskew=config.deskew,
        clean=config.clean,
        force_ocr=config.force_ocr,
        optimize=config.optimize,
        progress_bar=False,  # never draw a TUI bar in a batch/agent run
    )


def ocr_and_extract(
    pdf_path: str | Path,
    *,
    config: OcrConfig | None = None,
) -> ExtractResult:
    """OCR a scanned PDF locally, then extract + clean its new text layer.

    On success returns an :class:`~.extract.ExtractResult` with
    ``text_source="ocr"``. On ANY local OCR failure it returns a result with
    ``text_source="ocr-failed"`` and empty text — the item is flagged for review,
    NEVER auto-escalated to a cloud service (see module docstring / §7).
    """
    config = config or OcrConfig()
    path = Path(pdf_path)

    with tempfile.TemporaryDirectory(prefix="eo-ocr-") as td:
        out_pdf = Path(td) / (path.stem + ".ocr.pdf")
        try:
            _run_ocrmypdf(path, out_pdf, config)
        except Exception as exc:  # local failure ONLY — do not escalate
            logger.warning(
                "OCR failed locally for %s: %s — flagging ocr-failed (NOT "
                "escalating to any cloud service, per standards §7)",
                path, exc,
            )
            return ExtractResult(
                text="",
                page_count=0,
                char_count=0,
                text_source=TEXT_SOURCE_OCR_FAILED,
            )

        result = extract_pdf_text(out_pdf)
        # Re-stamp provenance: the bytes came from local OCR, not a native layer.
        return ExtractResult(
            text=result.text,
            page_count=result.page_count,
            char_count=result.char_count,
            text_source=TEXT_SOURCE_OCR,
        )
