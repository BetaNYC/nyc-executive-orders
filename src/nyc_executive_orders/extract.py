"""Born-digital full-text extraction via PyMuPDF, plus text cleanup.

Extracts the text layer of a PDF and cleans it into a readable Markdown body:

  * **Dehyphenation** — a word split across a line break by a soft hyphen
    (``adminis-`` / ``tration``) is rejoined into ``administration``. The
    heuristic only fires when the char before the hyphen is a word char and the
    first char of the next line is *lowercase*, so genuine hyphenated compounds
    that happen to wrap (``public-\\nprivate``) or a capitalized new token after
    a dash are left intact. It is deliberately conservative and documented as
    imperfect — over-joining a rare compound is preferable to gluing sentences.
  * **Whitespace normalization** — runs of intra-line spaces/tabs collapse to a
    single space; trailing spaces are stripped; 3+ consecutive blank lines
    collapse to a single blank line (one paragraph break). Paragraph structure
    (blank line between blocks) is preserved.

The same cleaning is reused for OCR'd output (see :mod:`ocr`) so born-digital
and OCR bodies read consistently.

No network. Pure local PDF read.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

logger = logging.getLogger("nyc_executive_orders.extract")

# Provenance tags stamped on the emitted record (frontmatter `text_source`).
TEXT_SOURCE_BORN_DIGITAL = "born-digital"

# A word-char, a hyphen, end-of-line, then a lowercase letter: a soft line-wrap
# hyphen to rejoin. Capturing the two word chars lets us drop the hyphen+newline.
_DEHYPHEN_RE = re.compile(r"(\w)-\n([a-z])")

# Runs of spaces/tabs (not newlines) to collapse to a single space.
_INTRALINE_WS_RE = re.compile(r"[ \t]+")

# 3+ newlines (allowing intervening spaces) collapse to exactly two (one blank
# line = one paragraph break).
_MULTI_BLANK_RE = re.compile(r"\n[ \t]*\n[ \t]*(?:\n[ \t]*)+")


@dataclass(frozen=True)
class ExtractResult:
    """Cleaned full text plus provenance for one PDF."""

    text: str
    page_count: int
    char_count: int
    text_source: str  # TEXT_SOURCE_BORN_DIGITAL | ocr.TEXT_SOURCE_OCR | ...

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())


def clean_text(raw: str) -> str:
    """Dehyphenate line-wraps, normalize whitespace, preserve paragraph breaks."""
    # 1) Rejoin soft-hyphenated line wraps BEFORE collapsing whitespace, while
    #    the newline that signals the wrap is still present.
    text = _DEHYPHEN_RE.sub(r"\1\2", raw)
    # 2) Collapse intra-line whitespace and strip trailing spaces per line.
    text = "\n".join(_INTRALINE_WS_RE.sub(" ", line).rstrip() for line in text.split("\n"))
    # 3) Collapse 3+ newlines to a single paragraph break.
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    # 4) Trim leading/trailing blank space overall.
    return text.strip()


def extract_pdf_text(pdf_path: str | Path) -> ExtractResult:
    """Extract + clean the born-digital text layer of a PDF.

    Pages are joined with a blank line (paragraph break) between them. Raises
    nothing on an empty text layer — it returns an ExtractResult whose
    ``has_text`` is False, so the caller can flag it rather than crash. A file
    that cannot be opened DOES raise (a scanned/no-pdf file should never reach
    this path — it is gated out by :mod:`textlayer`).
    """
    path = Path(pdf_path)
    doc = fitz.open(path)
    try:
        page_count = doc.page_count
        pages = [page.get_text("text") for page in doc]
    finally:
        doc.close()

    text = clean_text("\n\n".join(pages))
    return ExtractResult(
        text=text,
        page_count=page_count,
        char_count=len(text),
        text_source=TEXT_SOURCE_BORN_DIGITAL,
    )
