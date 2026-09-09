"""Text-layer probe/gate — classify a PDF as ``text``, ``scanned-ocr-layer`` or ``scanned``.

This is the decision point of the parse pipeline: born-digital PDFs go straight
to :mod:`extract`, scanned PDFs must go through OCR. Getting the split right is
what keeps scanned orders from being emitted as empty bodies, and keeps the
born-digital ones from being needlessly (and lossily) re-OCR'd.

TWO probes, because one is not enough:

1. **Character density.** PyMuPDF characters summed across pages, divided by the
   page count. At or below :data:`TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD` there is
   effectively no recoverable text (``scanned``). The threshold formalizes the
   >100 chars/page rule measured across the corpus on 2026-07-12. It is
   intentionally generous: a real text layer is thousands of chars/page, an
   image-only page is 0-a-few, so 100 sits in a wide empty valley.

2. **Text render mode.** Density alone cannot tell a word processor's output
   from a *scan somebody already ran through OCR*, whose text layer is a
   machine's guess at a photograph stamped invisibly over the page image. PDF
   render mode 3 is that exact signature, and ``page.get_texttrace()`` reports
   it per span. Above :data:`INVISIBLE_CHAR_SHARE_THRESHOLD` the PDF is
   ``scanned-ocr-layer``: it has extractable text, but that text is second-hand
   OCR of unknown vintage, not the document.

   Measured 2026-09-08 over all 1,205 records the density probe alone called
   born-digital: 665 are >=90% invisible, 537 are fully visible, and only 5 sit
   anywhere between 0.1% and 99.9%. The share is bimodal at 0 and 1, so like the
   density threshold this one also sits in an empty valley. Costs 8.2s for the
   whole corpus.

Per-page counts are kept, not just the mean, because a mean hides an image-only
page inside an otherwise-good document: a 3-page PDF with 2,000 chars on page 1
and none on pages 2-3 passes the density gate, and the caller then publishes a
body missing two thirds of the order. :attr:`TextLayerResult.image_only_pages`
is that signal.

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
# real text layer. Formalizes the corpus-measured >100 rule.
TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD = 100

# Above this share of extracted characters drawn in PDF render mode 3
# (invisible), the text layer is an OCR overlay on a page image rather than the
# document itself. Sits in an empty valley exactly like the density threshold:
# of 1,205 corpus PDFs with a text layer, 665 measure >=0.999 and 535 measure
# <=0.001; only 5 fall anywhere between. Any value in (0.01, 0.99) splits the
# same 665/537 -- 0.5 is chosen for saying "mostly invisible" plainly.
INVISIBLE_CHAR_SHARE_THRESHOLD = 0.5

# Classification labels (also the values stamped into the report + downstream
# provenance decisions).
CLASS_TEXT = "text"                     # born-digital: the text layer IS the document
CLASS_OCR_LAYER = "scanned-ocr-layer"   # a scan carrying somebody else's OCR: re-OCR it
CLASS_SCANNED = "scanned"               # image-only / near-empty: needs OCR
CLASS_ERROR = "error"                   # unreadable/corrupt PDF, or zero pages

# PDF text render mode 3 = "neither fill nor stroke": invisible. Modes 0-2 and
# 4-7 all paint something. See PDF 32000-1:2008 section 9.3.6.
_RENDER_MODE_INVISIBLE = 3


@dataclass(frozen=True)
class TextLayerResult:
    """One PDF's text-layer classification, plus the evidence behind it."""

    pdf_path: str
    classification: str          # CLASS_TEXT | CLASS_OCR_LAYER | CLASS_SCANNED | CLASS_ERROR
    page_count: int
    total_chars: int
    chars_per_page: float
    error: str | None = None
    # Per-page extracted-character counts, in page order. The mean above hides
    # an image-only page inside an otherwise-good document; this does not.
    page_chars: tuple[int, ...] = ()
    # Characters by PDF text render mode. `visible_chars` is every mode that
    # paints something, not just mode 0 -- one corpus PDF (2012-EO-170) draws 30
    # chars in mode 1 (stroke-only), and stroked text is on the page as surely
    # as filled text is. `traced_chars` is carried as the explicit denominator
    # for `invisible_share` so the share can never be computed against a
    # part-total if a future mode is broken out separately.
    invisible_chars: int = 0
    visible_chars: int = 0
    traced_chars: int = 0
    # 1-based page numbers whose extracted-character count is at or below the
    # density threshold, inside a document that otherwise passed the gate.
    image_only_pages: tuple[int, ...] = ()

    @property
    def needs_ocr(self) -> bool:
        """Would OCR add text this document does not already have?

        THE one definition of the OCR worklist. :func:`vlm_corpus.probe_record`
        selects on it and :func:`build_corpus.parse_record` prefers OCR output
        for it, so the two cannot disagree about the population.

        Three ways to be true:

        * ``CLASS_SCANNED`` — no text layer at all.
        * ``CLASS_OCR_LAYER`` — a text layer, but it is somebody else's OCR of a
          page image. A scan is a scan whether or not it has been read before.
        * ``CLASS_TEXT`` with :attr:`image_only_pages` — a genuine born-digital
          document that nevertheless holds a scanned page. 8 corpus documents
          are this shape, holding 13 pages between them, and every one of those
          pages carries ink. There is no per-page routing in this pipeline and
          8 documents do not justify building one, so the whole document goes.
        """
        if self.classification in (CLASS_SCANNED, CLASS_OCR_LAYER):
            return True
        return self.classification == CLASS_TEXT and bool(self.image_only_pages)

    @property
    def invisible_share(self) -> float:
        """Fraction of traced characters drawn invisibly. 0.0 when nothing traced."""
        if not self.traced_chars:
            return 0.0
        return self.invisible_chars / self.traced_chars

    def as_dict(self) -> dict:
        return {
            "pdf_path": self.pdf_path,
            "classification": self.classification,
            "page_count": self.page_count,
            "total_chars": self.total_chars,
            "chars_per_page": round(self.chars_per_page, 2),
            "error": self.error,
            "page_chars": list(self.page_chars),
            "invisible_chars": self.invisible_chars,
            "visible_chars": self.visible_chars,
            "traced_chars": self.traced_chars,
            "invisible_share": round(self.invisible_share, 4),
            "image_only_pages": list(self.image_only_pages),
        }


def classify_pdf(
    pdf_path: str | Path,
    *,
    threshold: int = TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD,
    invisible_threshold: float = INVISIBLE_CHAR_SHARE_THRESHOLD,
) -> TextLayerResult:
    """Classify a single PDF by character density, then by text render mode.

    Opens the PDF locally and makes ONE pass over its pages, collecting the
    ``page.get_text("text")`` character count per page and the
    ``page.get_texttrace()`` character counts per render mode. The per-page mean
    is compared against ``threshold`` to decide whether a text layer exists at
    all; a document that has one is then split by ``invisible_threshold`` into
    the real thing (:data:`CLASS_TEXT`) and a scan carrying somebody else's OCR
    (:data:`CLASS_OCR_LAYER`).

    A missing, empty, or unreadable file returns :data:`CLASS_ERROR` (never
    crashes the batch — the caller decides how to surface it).
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
        page_chars: list[int] = []
        invisible = visible = traced = 0
        for page in doc:
            page_chars.append(len(page.get_text("text")))
            for span in page.get_texttrace():
                n = len(span.get("chars", ()))
                traced += n
                if span.get("type") == _RENDER_MODE_INVISIBLE:
                    invisible += n
                else:
                    visible += n
        total_chars = sum(page_chars)
    except Exception as exc:  # pragma: no cover - defensive, mid-read failure
        logger.warning("textlayer: read error on %s: %s", path, exc)
        return TextLayerResult(str(pdf_path), CLASS_ERROR, 0, 0, 0.0, error=str(exc))
    finally:
        doc.close()

    chars_per_page = total_chars / page_count
    if chars_per_page <= threshold:
        # No usable text layer at all; the render-mode split is meaningless here.
        classification = CLASS_SCANNED
        image_only_pages: tuple[int, ...] = ()
    else:
        share = invisible / traced if traced else 0.0
        classification = CLASS_OCR_LAYER if share > invisible_threshold else CLASS_TEXT
        # Only meaningful inside a document that passed the density gate: these
        # are the pages the caller would otherwise publish as nothing.
        image_only_pages = tuple(
            i for i, n in enumerate(page_chars, start=1) if n <= threshold
        )

    return TextLayerResult(
        pdf_path=str(pdf_path),
        classification=classification,
        page_count=page_count,
        total_chars=total_chars,
        chars_per_page=chars_per_page,
        page_chars=tuple(page_chars),
        invisible_chars=invisible,
        visible_chars=visible,
        traced_chars=traced,
        image_only_pages=image_only_pages,
    )


def classify_many(
    pdf_paths: Iterable[str | Path],
    *,
    threshold: int = TEXT_LAYER_CHARS_PER_PAGE_THRESHOLD,
    invisible_threshold: float = INVISIBLE_CHAR_SHARE_THRESHOLD,
) -> list[TextLayerResult]:
    """Classify a batch of PDFs, in order."""
    return [classify_pdf(p, threshold=threshold,
                         invisible_threshold=invisible_threshold)
            for p in pdf_paths]


def summarize(results: Iterable[TextLayerResult]) -> dict[str, int]:
    """Count results by classification, e.g. ``{'text': 537, 'scanned': 916}``."""
    counts: dict[str, int] = {CLASS_TEXT: 0, CLASS_OCR_LAYER: 0,
                              CLASS_SCANNED: 0, CLASS_ERROR: 0}
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
        "threshold_invisible_char_share": INVISIBLE_CHAR_SHARE_THRESHOLD,
        "summary": summarize(results),
        "records": [r.as_dict() for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path
