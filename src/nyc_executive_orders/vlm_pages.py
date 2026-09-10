"""Read committed dots.ocr page records into corpus body text — era-neutral.

The VLM OCR stage (:mod:`vlm_ocr`) writes one ``page_XXXX.json`` per page, for
the pre-1974 bound volumes and (Phase F) the post-1974 scans alike. This module
turns those records into the text a corpus record publishes, and into the QA
flags that force a record to ``needs-review``.

It exists because two callers need the same functions and only one of them is
about volumes. :func:`element_text`, :func:`page_lines` and :func:`page_flags`
were written for :mod:`volume_split`; that module now re-exports them from here,
so the pre-1974 path is unchanged, byte for byte.

:func:`assemble_body` joined them for the same reason. Gluing a document's blocks
into its final text was the last step of BOTH era pipelines, written twice and so
diverging twice: neither separated two blocks by more than a single newline, which
Markdown reads as one continuing paragraph, and only the post-1974 copy rejoined a
word broken across a line. Both eras now call the one function.

    THE CORPUS RENDERER, NOT THE VIEWER RENDERER.
    :func:`vlm_ocr.records_to_markdown` and :func:`vlm_ocr.element_to_markdown`
    render a faithful *review* document: they inject ``<!-- page N -->`` markers,
    ``---`` rules and ``*[Picture - bbox ...]*`` placeholders. None of that is
    order text, and all of it would be published into ``full_text``. Corpus
    bodies come from here.

Nothing in this module paraphrases, summarizes or invents text. Every transform
is a layout decision over strings the model already emitted: drop a Picture's
bbox placeholder, collapse the model's own heading marks, re-render a table's
HTML as lines. The verbatim model output stays in the committed page record.

No network. No model. Pure local reads.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .extract import clean_text

# Ink-coverage below which a page is treated as having lost content, even though
# its JSON parsed. Matches the viewer's uncovered-ink QA flag.
MIN_COVERED_FRACTION = 0.98

# Categories dropped from the corpus body entirely. A Picture is the city seal or
# the mayor's signature; the model emits no text for it, and its bbox coordinates
# are not order text.
DROPPED_CATEGORIES = frozenset({"Picture"})

# Categories the model marks up as HTML rather than plain text.
TABLE_CATEGORIES = frozenset({"Table"})

# Categories rendered as headings when heading_marks is on.
_HEADING_PREFIX = {"Title": "#", "Section-header": "##"}

_LEADING_HASHES_RE = re.compile(r"^#{1,6}\s*")

# A whole-line Markdown thematic break: 3+ of -, * or _, optionally spaced, and
# nothing else. dots.ocr emits these for a printed horizontal rule -- typically
# the line under a letterhead, and often folded INTO the Title element's text.
_RULE_LINE_RE = re.compile(r"^[ \t]*(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")

# A word broken across the boundary between two blocks: the left block ends in a
# hyphen mid-word, the right one opens with the rest of the word. The pair mirrors
# ``extract._DEHYPHEN_RE`` (``(\w+)[-\u00ad]\n([a-z]\w*)``) exactly, because the
# whole point of detecting it is to leave those two fragments a SINGLE newline
# apart, which is the only thing that regex will repair. U+00AD soft hyphen counts
# as the hyphen there, so it counts here.
_WRAP_TAIL_RE = re.compile(r"\w[-\u00ad]\Z")
_WRAP_HEAD_RE = re.compile(r"\A[a-z]")


def _drop_rule_lines(text: str) -> str:
    """Remove whole-line Markdown thematic breaks from an element's text.

    Markup, not order text: the line carries no words, so dropping it adds,
    removes, reorders and paraphrases nothing. The verbatim string stays in the
    committed page record.

    It has to go from a PUBLISHED body for two concrete reasons, both seen on
    the real 1974-EO-001:

    * ``OFFICE OF THE MAYOR`` followed by ``---`` is a setext heading in
      Markdown, so a printed rule silently promotes the line above it to an H2;
    * corpus records are YAML frontmatter between ``---`` fences, and a body that
      also opens a line with ``---`` is needlessly ambiguous to anything that
      splits on them.
    """
    return "\n".join(ln for ln in text.split("\n") if not _RULE_LINE_RE.match(ln))


# --------------------------------------------------------------------------- #
# Table flattening                                                              #
# --------------------------------------------------------------------------- #

class _TableFlattener(HTMLParser):
    """Collect a table's cells row by row.

    Deliberately tolerant: the model's HTML is usually well-formed but is not
    guaranteed to be, and a malformed table must degrade to its text rather than
    raise. Unclosed rows still flush at close time.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "tr":
            self._flush_row()
            self._row = []
        elif tag in ("td", "th"):
            if self._row is None:
                self._row = []
            self._cell = []
        elif tag == "br" and self._cell is not None:
            # One cell is one field, and a row is one line, so an intra-cell
            # break becomes a space rather than a newline that would break the
            # row-per-line contract.
            self._cell.append(" ")

    def handle_startendtag(self, tag: str, attrs) -> None:
        if tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._flush_cell()
        elif tag == "tr":
            self._flush_row()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def _flush_cell(self) -> None:
        if self._cell is None:
            return
        text = " ".join("".join(self._cell).split())
        if self._row is None:
            self._row = []
        self._row.append(text)
        self._cell = None

    def _flush_row(self) -> None:
        self._flush_cell()
        if self._row:
            self.rows.append(self._row)
        self._row = None

    def close(self) -> None:  # noqa: D102 - HTMLParser hook
        super().close()
        self._flush_row()


def _strip_markup(markup: str) -> str:
    """Every word in the markup, tags removed, entities resolved."""
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", markup)).split())


def flatten_table_html(markup: str) -> str:
    """Re-render one ``<table>`` as plain lines: one row per line, cells by " | ".

    Cell order and cell text are preserved exactly; only the markup around them
    changes. ``<br>`` inside a cell becomes a space, because a row is one line.
    This is a layout re-rendering, not a rewrite: no word is added, removed,
    reordered or paraphrased, and the verbatim HTML stays in the committed page
    record.

    It is required rather than cosmetic. ``<`` and ``>`` are not in
    :func:`clean._junk_ratio`'s allowed character set, so a verbatim table pushes
    a body past ``REVIEW_MAX_JUNK_RATIO`` and forces ``needs-review`` on every
    order that contains one. Relaxing that ratio instead would re-tier the whole
    existing corpus.

    NOTHING IS EVER DROPPED. The parsed rows are accepted only if their words are
    exactly the words the markup contains; any shortfall (text outside a cell,
    a half-open tag, markup that is not a table at all) falls back to the tags
    stripped out of the raw string. Losing a word from an archived order would be
    far worse than losing its layout.
    """
    parser = _TableFlattener()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # pragma: no cover - HTMLParser is very tolerant
        parser.rows = []

    fallback = _strip_markup(markup)
    if not parser.rows:
        return fallback

    flattened = "\n".join(" | ".join(cells) for cells in parser.rows)
    if flattened.replace("|", " ").split() != fallback.split():
        return fallback
    return flattened


# --------------------------------------------------------------------------- #
# Element / page rendering                                                      #
# --------------------------------------------------------------------------- #

def element_text(
    element: dict,
    *,
    heading_marks: bool = True,
    flatten_tables: bool = False,
    strip_rules: bool = False,
) -> str:
    """One layout element as corpus body text.

    Deliberately NOT :func:`vlm_ocr.element_to_markdown`, which exists to render
    a faithful *review* document for the viewer. Two differences matter here:

    * ``Picture`` elements (the city seal, the mayor's signature) render there as
      ``*[Picture - bbox ...]*`` placeholders. Bbox coordinates are not order
      text, so they are dropped from the corpus body and counted instead.
    * The model sometimes emits its own markdown inside ``text``, so the viewer's
      unconditional ``##`` prefix can yield ``## ## PRINCIPLES``. Existing
      leading hashes are stripped before one level is applied.

    ``heading_marks`` defaults True, which is the pre-1974 behavior this function
    was written for. Post-1974 passes False: a single-order document publishes
    beside 1,205 born-digital records that carry no markdown, and a
    ``# THE CITY OF NEW YORK`` would also land in the ``dropped_header``
    provenance field. Either way the model's own hashes are collapsed first, so
    False means plain text rather than the model's unpredictable markup.

    ``strip_rules`` defaults False for the same reason as ``flatten_tables``:
    nine pre-1974 elements carry a printed rule as ``---`` / ``- - - - -`` /
    ``**********``, and dropping those would change committed output. Post-1974
    passes True -- see :func:`_drop_rule_lines` for why a published body cannot
    keep them.

    ``flatten_tables`` defaults False for the same reason: pre-1974 passes a
    ``Table`` through as the model's raw HTML, and :mod:`volume_split` reads the
    result. Page 1 of the O'Dwyer volume is a library date-stamp the model emits
    as a ``<table>``, and flattening it changes whether that page classifies as
    furniture. Post-1974 passes True, because a published body must not carry
    HTML (see :func:`flatten_table_html`).
    """
    category = element.get("category", "Text")
    text = (element.get("text") or "").strip()
    if strip_rules and category not in TABLE_CATEGORIES:
        text = _drop_rule_lines(text).strip()
    if category in DROPPED_CATEGORIES or not text:
        return ""
    if category in TABLE_CATEGORIES and flatten_tables:
        return flatten_table_html(text)
    if category in _HEADING_PREFIX:
        # Collapse the model's own heading marks, then apply exactly one level.
        lines = [_LEADING_HASHES_RE.sub("", ln).strip() for ln in text.split("\n")]
        lines = [ln for ln in lines if ln]
        if not lines:
            return ""
        if not heading_marks:
            return "\n".join(lines)
        prefix = _HEADING_PREFIX[category]
        # Only the first line is the heading; the rest (the model folds multi-line
        # letterheads into one element) stay as plain lines beneath it.
        return "\n".join([f"{prefix} {lines[0]}", *lines[1:]])
    return text


def page_blocks(
    record: dict,
    *,
    heading_marks: bool = True,
    flatten_tables: bool = False,
    strip_rules: bool = False,
) -> list[str]:
    """A page's elements as SEPARATE blocks, in reading order, Pictures dropped.

    One block per layout element the model identified, its own internal line
    breaks intact. This is :func:`page_lines` stopped one step earlier: the
    model already decided where this page's paragraphs are, and flattening the
    elements into a single list of lines is what throws that decision away.

    A page of six WHEREAS clauses comes back as six blocks.
    :func:`assemble_body` is what turns them into paragraphs.
    """
    blocks: list[str] = []
    for element in record.get("elements", []):
        rendered = element_text(
            element,
            heading_marks=heading_marks,
            flatten_tables=flatten_tables,
            strip_rules=strip_rules,
        )
        if rendered:
            blocks.append(rendered)
    return blocks


def page_lines(
    record: dict,
    *,
    heading_marks: bool = True,
    flatten_tables: bool = False,
    strip_rules: bool = False,
) -> list[str]:
    """Every line of a page's elements, in reading order, Pictures dropped.

    The block boundaries are FLATTENED AWAY here, and that is the point: this is
    what :mod:`volume_split` classifies a page by, counting lines from the top of
    the page (``lines[:HEAD_LINES]``, ``lines[0]``) to tell a title page from an
    index page from a continuation. Its output is therefore frozen — see
    ``tests/test_vlm_pages.py`` for the check against every committed page
    record. Anything that wants the paragraphs wants :func:`page_blocks`.
    """
    return [
        line
        for block in page_blocks(
            record,
            heading_marks=heading_marks,
            flatten_tables=flatten_tables,
            strip_rules=strip_rules,
        )
        for line in block.split("\n")
    ]


# --------------------------------------------------------------------------- #
# QA flags                                                                      #
# --------------------------------------------------------------------------- #

def page_flags(record: dict, *, flag_no_measurable_ink: bool = False) -> list[str]:
    """QA flags for one page — the same four signals the viewer surfaces.

    Any of these on any page of an instrument forces that record to
    ``needs-review`` downstream: a page that failed loudly must never reach the
    corpus looking clean.

    ``flag_no_measurable_ink`` is OFF by default because turning it on would add
    a flag to already-committed pre-1974 provenance. When on, a page whose ink
    measurement found no dark pixels at all raises ``no-measurable-ink``. Such a
    page gets ``covered_fraction: None`` from :func:`vlm_ocr.ink_coverage`, i.e.
    NO dropped-content QA whatsoever, and nothing else says so. Grey, low-contrast
    scans do exist in the post-1974 set (2026-EEO-3.1 measures dark_fraction 0.0
    at std 14.8), so post-1974 turns it on.
    """
    flags: list[str] = []
    if record.get("parse_error"):
        flags.append("parse-error")
    if record.get("finish_reason") == "length":
        flags.append("truncated")
    coverage = record.get("ink_coverage") or {}
    if coverage.get("uncovered_regions"):
        flags.append("uncovered-ink")
    covered = coverage.get("covered_fraction")
    if covered is not None and covered < MIN_COVERED_FRACTION:
        flags.append(f"low-ink-coverage:{covered:.3f}")
    logprobs = ((record.get("logprobs") or {}).get("content") or {})
    if logprobs.get("n_below_threshold"):
        flags.append(f"low-confidence:{logprobs['n_below_threshold']}")
    if record.get("debug_decode_matches_stream") is False:
        flags.append("decode-mismatch")
    if flag_no_measurable_ink and not record.get("skipped"):
        stats = record.get("page_stats") or {}
        if stats.get("dark_fraction") == 0:
            flags.append("no-measurable-ink")
    return flags


# --------------------------------------------------------------------------- #
# Whole-document assembly                                                       #
# --------------------------------------------------------------------------- #

def assemble_body(
    pages: list[list[str]],
    *,
    dehyphenate: bool = True,
) -> str:
    """One document's body text, from its pages' blocks. THE ONLY PLACE THIS LIVES.

    ``pages`` is one list of blocks per page, in page order, as
    :func:`page_blocks` returns them. Three decisions, and this is the single
    place all three are made:

    * **Two blocks are two paragraphs**, so a blank line goes between them. A
      single newline would NOT: the body is published as Markdown, where a lone
      line break continues the paragraph, so gluing blocks with one newline
      republishes a page the model carefully split into six WHEREAS clauses as
      one unbroken slab.
    * **Two pages are also separated by a blank line**, matching how
      :func:`extract.extract_pdf_text` joins born-digital pages.
    * **A word broken across either boundary is rejoined**, by
      :func:`extract.clean_text`.

    The last two interact, which is why they cannot live apart. ``clean_text``
    repairs ``econo-`` + ``mies`` only while the two halves are one newline
    apart; put the paragraph break in first and the word can never be repaired
    afterwards. So a block that ends mid-word is joined to the next by a single
    newline instead — ``1975-EO-048`` is the real record that does this.

    A blank line between every pair of blocks assumes every block boundary is a
    paragraph boundary, so the exceptions were counted before this was written.
    The committed page records hold 33,416 within-page block boundaries (16,139
    post-1974, 17,277 pre-1974). 147 of them (0.44%) have the shape of a split
    sentence: a body block of 60+ characters ending with no punctuation at all,
    followed by a block opening in lower case. Reading those 147, 90 are list
    items the model labelled slightly wrong (``c. Perform such other functions
    ...``, ``no.59 Awarding of contracts...``) and really are separate
    paragraphs. That leaves **57 boundaries, 0.17%**, where a sentence genuinely
    runs from one block into the next and now gains a paragraph break it should
    not have. No word is lost, reordered or altered at any of them.

    ``dehyphenate`` off skips ``clean_text`` entirely (no rejoin, no whitespace
    normalization); the blank lines still go in.
    """
    parts: list[str] = []
    for blocks in pages:
        for block in blocks:
            if not block:
                continue
            if parts:
                wrapped = _WRAP_TAIL_RE.search(parts[-1]) and _WRAP_HEAD_RE.match(block)
                parts.append("\n" if wrapped else "\n\n")
            parts.append(block)
    text = "".join(parts)
    return clean_text(text) if dehyphenate else text.strip()


@dataclass(frozen=True)
class DocumentText:
    """One document's body, assembled from its page records, plus what it cost."""

    text: str
    page_count: int
    pages_with_text: int
    pages_skipped: int
    flags: list[str] = field(default_factory=list)
    element_counts: dict[str, int] = field(default_factory=dict)
    tables: int = 0
    pictures: int = 0
    blank_override: bool = False

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())


def document_text(
    records: list[dict],
    *,
    heading_marks: bool = False,
    flatten_tables: bool = True,
    strip_rules: bool = True,
    dehyphenate: bool = True,
    flag_no_measurable_ink: bool = True,
) -> DocumentText:
    """Assemble one document's corpus body from its page records, in page order.

    :func:`assemble_body` makes every layout decision — blocks and pages both
    separated by a blank line, words broken across either boundary rejoined.
    This function is the bookkeeping around it: page order, QA flags, and the
    element/table/picture counts a record's provenance carries.

    Three kinds of page contribute no text, all deliberately:

    * a ``skipped`` (blank / bleed-through) page — no marker is emitted, because
      ``*[page skipped]*`` is viewer furniture, not order text;
    * a ``parse_error`` page — its ``raw_text`` is model JSON scaffolding rather
      than a transcription, so it stays in the committed record for a human and
      never reaches ``full_text``. The page still raises ``parse-error``;
    * a page whose elements are all Pictures or empty.

    ``dehyphenate`` runs :func:`extract.clean_text`, the same rejoin-and-normalize
    pass every born-digital and Tesseract body already went through, so a migrated
    body is shaped like its siblings in the same ``eo.json``. Turning it off keeps
    the blank lines and skips the repair.

    Flags are the union over pages, order-stable and de-duplicated, plus
    document-level ``no-pages-recorded`` / ``all-pages-blank``.
    """
    ordered = sorted(records, key=lambda r: r.get("page", 0))

    page_block_lists: list[list[str]] = []
    flags: list[str] = []
    seen_flags: set[str] = set()
    element_counts: dict[str, int] = {}
    pages_with_text = pages_skipped = tables = pictures = 0
    blank_override = False

    for record in ordered:
        for flag in page_flags(record, flag_no_measurable_ink=flag_no_measurable_ink):
            if flag not in seen_flags:
                seen_flags.add(flag)
                flags.append(flag)
        if record.get("blank_override"):
            blank_override = True
        if record.get("skipped"):
            pages_skipped += 1
            continue
        for element in record.get("elements", []):
            category = element.get("category", "Text")
            element_counts[category] = element_counts.get(category, 0) + 1
            if category in DROPPED_CATEGORIES:
                pictures += 1
            elif category in TABLE_CATEGORIES:
                tables += 1
        blocks = page_blocks(
            record,
            heading_marks=heading_marks,
            flatten_tables=flatten_tables,
            strip_rules=strip_rules,
        )
        if blocks:
            pages_with_text += 1
            page_block_lists.append(blocks)

    text = assemble_body(page_block_lists, dehyphenate=dehyphenate)

    if not ordered:
        flags.append("no-pages-recorded")
    elif pages_skipped == len(ordered):
        flags.append("all-pages-blank")
    elif not text.strip():
        flags.append("empty-output")

    return DocumentText(
        text=text,
        page_count=len(ordered),
        pages_with_text=pages_with_text,
        pages_skipped=pages_skipped,
        flags=flags,
        element_counts=element_counts,
        tables=tables,
        pictures=pictures,
        blank_override=blank_override,
    )
