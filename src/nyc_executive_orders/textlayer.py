"""Text-layer probe/gate — classify a PDF as born-digital ``text`` vs ``scanned``.

This is the decision point of the parse pipeline: born-digital PDFs go straight
to :mod:`extract` (fast, faithful), scanned PDFs must go through local
:mod:`ocr`. Getting the split right is what keeps the ~1,000 scanned orders from
being emitted as empty bodies, and keeps the ~880 born-digital ones from being
needlessly (and lossily) re-OCR'd.

Heuristic: PyMuPDF character density. We sum the extracted-text characters
across all pages and divide by the page count. Above
:data:`TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD` the PDF has a real text layer
(``text``); at or below it there is effectively no recoverable text (``scanned``).
The threshold formalizes the >100 chars/page rule measured across the corpus on
2026-07-12 (881 text / 916 scanned / 53 no-pdf). It is intentionally generous:
a born-digital order is thousands of chars/page, a scanned one is 0–a-few, so
100 sits in a wide empty valley between the two populations.

No network, no OCR here — pure local inspection of committed bytes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import fitz  # PyMuPDF

logger = logging.getLogger("nyc_executive_orders.textlayer")

# Above this many extracted characters per page, a PDF is treated as having a
# real (born-digital) text layer. Formalizes the corpus-measured >100 rule.
TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD = 100

# Classification labels (also the values stamped into the report + downstream
# provenance decisions).
CLASS_TEXT = "text"        # born-digital: has an extractable text layer
CLASS_SCANNED = "scanned"  # image-only / near-empty: needs OCR
CLASS_ERROR = "error"      # unreadable/corrupt PDF, or zero pages


@dataclass(frozen=True)
class TextLayerResult:
    """One PDF's text-layer classification."""

    pdf_path: str
    classification: str          # CLASS_TEXT | CLASS_SCANNED | CLASS_ERROR
    page_count: int
    total_chars: int
    chars_per_page: float
    error: str | None = None

    @property
    def needs_ocr(self) -> bool:
        return self.classification == CLASS_SCANNED

    def as_dict(self) -> dict:
        return {
            "pdf_path": self.pdf_path,
            "classification": self.classification,
            "page_count": self.page_count,
            "total_chars": self.total_chars,
            "chars_per_page": round(self.chars_per_page, 2),
            "error": self.error,
        }


def classify_pdf(
    pdf_path: str | Path,
    *,
    threshold: int = TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD,
) -> TextLayerResult:
    """Classify a single PDF by PyMuPDF character density.

    Opens the PDF locally, sums ``page.get_text("text")`` characters across all
    pages, and compares the per-page mean against ``threshold``. A missing,
    empty, or unreadable file returns :data:`CLASS_ERROR` (never crashes the
    batch — the caller decides how to surface it).
    """
    path = Path(pdf_path)
    if not path.exists():
        return TextLayerResult(str(pdf_path), CLASS_ERROR, 0, 0, 0.0,
                               error="file not found")
    try:
        doc = fitz.open(path)
    except Exception as exc:  # corrupt / unsupported / encrypted
        logger.warning("textlayer: cannot open %s: %s", path, exc)
        return TextLayerResult(str(pdf_path), CLASS_ERROR, 0, 0, 0.0, error=str(exc))

    try:
        page_count = doc.page_count
        if page_count == 0:
            return TextLayerResult(str(pdf_path), CLASS_ERROR, 0, 0, 0.0,
                                   error="zero pages")
        total_chars = sum(len(page.get_text("text")) for page in doc)
    except Exception as exc:  # pragma: no cover - defensive, mid-read failure
        logger.warning("textlayer: read error on %s: %s", path, exc)
        return TextLayerResult(str(pdf_path), CLASS_ERROR, 0, 0, 0.0, error=str(exc))
    finally:
        doc.close()

    chars_per_page = total_chars / page_count
    classification = CLASS_TEXT if chars_per_page > threshold else CLASS_SCANNED
    return TextLayerResult(
        pdf_path=str(pdf_path),
        classification=classification,
        page_count=page_count,
        total_chars=total_chars,
        chars_per_page=chars_per_page,
    )


def classify_many(
    pdf_paths: Iterable[str | Path],
    *,
    threshold: int = TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD,
) -> list[TextLayerResult]:
    """Classify a batch of PDFs, in order."""
    return [classify_pdf(p, threshold=threshold) for p in pdf_paths]


def summarize(results: Iterable[TextLayerResult]) -> dict[str, int]:
    """Count results by classification, e.g. ``{'text': 881, 'scanned': 916}``."""
    counts: dict[str, int] = {CLASS_TEXT: 0, CLASS_SCANNED: 0, CLASS_ERROR: 0}
    for r in results:
        counts[r.classification] = counts.get(r.classification, 0) + 1
    return counts


def write_textlayer_report(
    results: Iterable[TextLayerResult],
    out_path: str | Path,
) -> Path:
    """Write the reproducible probe report (JSON) — gitignored regenerated output.

    Shape: ``{"threshold": N, "summary": {text, scanned, error}, "records": [...]}``.
    Idempotent: overwrites in place, so re-running the probe yields the same file.
    """
    results = list(results)
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "threshold_chars_per_page": TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD,
        "summary": summarize(results),
        "records": [r.as_dict() for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path
