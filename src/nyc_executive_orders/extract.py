"""Full-text extraction of a PDF's text layer via PyMuPDF, plus text cleanup.

Extracts the text layer of a PDF and cleans it into a readable Markdown body:

  * **Dehyphenation** — a word split across a line break (``adminis-`` /
    ``tration``) is rejoined into ``administration``. The two halves of a wrap
    are word FRAGMENTS, and fragments are not words: the rule joins only when
    :func:`lexicon.recognize` rejects at least one side. So ``adminis`` +
    ``tration`` joins, while ``public-``/``private``, ``not-``/``for`` and
    ``to-``/``person`` keep their hyphen, because both halves are real words and
    the hyphen is the compound's own.

    The previous rule looked at capitalization instead, and it was wrong in both
    directions: it glued ``person-to-\nperson`` into ``person-toperson`` in 32
    corpus records, and its own docstring's example (``public-\nprivate``) came
    out as ``publicprivate``. Measured over all 1,205 born-digital PDFs, the
    lexicon rule keeps the hyphen at 98 of the 126 wrap sites and joins the other
    28, and every decision is correct except two the old rule also got wrong
    (``ex-officio``, ``197-d``).

  * **Soft hyphens** — U+00AD is an invisible "break here if you must" mark. It
    is not ``-``, so the rule above never saw it, and 135 records published it
    verbatim, where a search for ``person-to-person`` cannot match. It is
    removed, and a wrap on one is rejoined.

  * **Paragraph breaks** — PyMuPDF returns one line per printed line and says
    nothing about paragraphs, so a body whose source PDF puts no extra leading
    between blocks arrives with no blank line anywhere and Markdown renders it as
    one wall of text. :func:`_page_paragraphs` reads the line geometry and
    inserts a break where the vertical gap jumps. See its docstring.

  * **Whitespace normalization** — runs of intra-line spaces/tabs collapse to a
    single space; trailing spaces are stripped; 3+ consecutive blank lines
    collapse to a single blank line (one paragraph break).

:func:`clean_text` is reused for OCR'd output (:mod:`ocr`, :mod:`vlm_pages`) so
every body reads consistently; :func:`_page_paragraphs` is not, because it needs
page geometry that only this module has.

NOTE: this module is NOT the born-digital path alone. A PDF classified
:data:`textlayer.CLASS_OCR_LAYER` — a scan carrying somebody else's OCR — also
comes through here, and its text is second-hand OCR of unknown quality rather
than the document. The caller stamps the provenance; do not read a body from
here as faithful without checking it.

No network. Pure local PDF read.
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF

from . import lexicon

logger = logging.getLogger("nyc_executive_orders.extract")

# Provenance tags stamped on the emitted record (frontmatter `text_source`).
TEXT_SOURCE_BORN_DIGITAL = "born-digital"

# A line-wrap hyphen site: the word fragment before the hyphen, the newline, and
# the fragment after it. Whether to JOIN is decided per match by `_join_wrap` —
# the regex only finds candidates.
#
# U+00AD SOFT HYPHEN counts as the hyphen. It is invisible mid-line and prints as
# a hyphen at a break, so at a line end it means exactly what "-" means and gets
# exactly the same treatment: "person-to\u00ad\nperson" is the compound
# "person-to-person" and must keep its hyphen, while "admin\u00ad\nistration" is
# one word and must not.
_DEHYPHEN_RE = re.compile(r"(\w+)[-\u00ad]\n([a-z]\w*)")

# Invisible marks left ANYWHERE else in the body. A soft hyphen that is not at a
# line end never prints, so publishing it verbatim only breaks search: 135
# records carried one, and a search for "person-to-person" could not match them.
# Zero-width space and a stray byte-order mark ride along for the same reason.
_INVISIBLE_MARKS_RE = re.compile(r"[\u00ad\u200b\ufeff]")

# A line gap this many times the page's median line gap is a paragraph break.
# Measured on the real corpus: within a paragraph the leading is 13.8pt and
# between paragraphs it is 27.6pt (corpus/2022/2022-EO-023.md), a ratio of 2.0.
# 1.35 sits well inside that gap while staying above the jitter of a page that
# mixes 11pt body text with a 16pt heading.
PARAGRAPH_GAP_RATIO = 1.35

# Below this many line gaps a page has no reliable median to compare against --- a
# title page, a signature block, a one-clause order. Such a page falls back to
# plain text extraction rather than guessing.
_MIN_GAPS_FOR_PARAGRAPHS = 6

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


def _join_wrap(match: re.Match) -> str:
    """Join a line-wrap hyphen, unless both halves are words in their own right.

    ``adminis`` and ``tration`` are fragments — neither is a word — so the hyphen
    was inserted by the typesetter and must go. ``public`` and ``private`` are
    both words, so the hyphen belongs to the compound and must stay. This is the
    only signal that separates the two cases; capitalization is not one, since
    ``person-to-person`` and ``not-for-profit`` wrap in lowercase.
    """
    left, right = match.group(1), match.group(2)
    if lexicon.recognize(left) and lexicon.recognize(right):
        return f"{left}-{right}"      # a real compound: keep the hyphen, drop the wrap
    return f"{left}{right}"


def clean_text(raw: str) -> str:
    """Dehyphenate line-wraps, drop invisible marks, normalize whitespace."""
    # 1) Rejoin wraps BEFORE collapsing whitespace, while the newline that
    #    signals the wrap is still present.
    text = _DEHYPHEN_RE.sub(_join_wrap, raw)
    # 2) Any invisible mark that was not a line wrap is simply noise in the body.
    text = _INVISIBLE_MARKS_RE.sub("", text)
    # 3) Collapse intra-line whitespace and strip trailing spaces per line.
    text = "\n".join(_INTRALINE_WS_RE.sub(" ", line).rstrip() for line in text.split("\n"))
    # 4) Collapse 3+ newlines to a single paragraph break.
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    # 5) Trim leading/trailing blank space overall.
    return text.strip()


def _page_paragraphs(page) -> str:
    """One page's text, with a blank line wherever the printed leading jumps.

    ``page.get_text("text")`` returns one line per printed line and nothing about
    paragraphs. Where the source PDF separates blocks with extra leading rather
    than a blank line, every paragraph break is lost, and the Markdown body then
    renders the whole order as a single wall of text. 187 of the 537 genuinely
    born-digital bodies had no blank line anywhere.

    The leading itself is the signal, and ``"dict"`` mode carries it: the gap
    between consecutive line tops is steady inside a paragraph and jumps between
    them (13.8pt against 27.6pt in corpus/2022/2022-EO-023.md). Comparing each
    gap against the page's OWN median makes the rule independent of font size,
    so an 11pt body and a 16pt heading on one page are judged on the same scale.

    This adds structure only: it inserts newlines and never alters, reorders or
    drops a character. Verified against all 537 genuine born-digital PDFs ---
    stripping whitespace from this output and from ``get_text("text")`` gives
    identical strings for every one.

    Falls back to plain extraction on a page with too few gaps to have a
    meaningful median, and on a page whose blocks PyMuPDF gives no geometry for.
    """
    data = page.get_text("dict")
    lines: list[tuple[float, str]] = []
    for block in data.get("blocks", ()):
        if block.get("type") != 0:      # 1 = image block: no text, no geometry
            continue
        for line in block.get("lines", ()):
            text = "".join(span.get("text", "") for span in line.get("spans", ()))
            lines.append((line["bbox"][1], text))

    gaps = [b - a for (a, _), (b, _) in zip(lines, lines[1:]) if b > a]
    if len(gaps) < _MIN_GAPS_FOR_PARAGRAPHS:
        return page.get_text("text")

    median = statistics.median(gaps)
    if median <= 0:                      # pragma: no cover - degenerate geometry
        return page.get_text("text")

    threshold = median * PARAGRAPH_GAP_RATIO
    out = [lines[0][1]]
    for (prev_top, _), (top, text) in zip(lines, lines[1:]):
        out.append("\n" + text if top - prev_top > threshold else text)
    return "\n".join(out) + "\n"


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
        pages = [_page_paragraphs(page) for page in doc]
    finally:
        doc.close()

    text = clean_text("\n\n".join(pages))
    return ExtractResult(
        text=text,
        page_count=page_count,
        char_count=len(text),
        text_source=TEXT_SOURCE_BORN_DIGITAL,
    )
