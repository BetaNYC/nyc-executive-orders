"""Emit the publishable corpus — one Markdown file per EO + a bulk ``eo.json``.

Chains the parse pipeline for every indexed order:

    textlayer.classify -> (born-digital) extract  |  (scanned) ocr  -> enrich -> emit

Outputs (all under ``corpus/``, a gitignored regenerated artifact like ``index/``):
  * ``corpus/YYYY/<eo_id>.md`` — YAML frontmatter (locked metadata field set) +
    body = the extracted/OCR'd full text, or a ``_No text available_`` stub for
    the 53 no-PDF gap EOs and any ``ocr-failed`` order.
  * ``corpus/eo.json`` — one object per order, all metadata + ``full_text``.
  * ``corpus/manifest.csv`` — per-order parse ledger (text_source, counts, path).
  * ``index/textlayer_report.json`` — the probe report (side output).

Writes overwrite in place, so the whole build is idempotent (safe to re-run).

The ``manifest.csv`` refreshed here is the CORPUS manifest at ``corpus/manifest.csv``
— it does NOT overwrite the harvest's ``manifest.csv`` at the repo root (that is
owned by the harvest step and records download state, a different concern).
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import yaml

from . import textlayer
from .clean import clean_record
from .enrich import enrich_record
from .extract import TEXT_SOURCE_BORN_DIGITAL, extract_pdf_text
from .ocr import (
    TEXT_SOURCE_OCR,
    TEXT_SOURCE_OCR_FAILED,
    OcrConfig,
    ocr_and_extract,
)

logger = logging.getLogger("nyc_executive_orders.build_corpus")

# text_source values that this module adds beyond the extract/ocr ones.
TEXT_SOURCE_NONE = "none"                # no PDF on disk (the 53 gap EOs)
TEXT_SOURCE_OCR_SKIPPED = "ocr-skipped"  # scanned, but run under --no-ocr
TEXT_SOURCE_UNREADABLE = "unreadable"    # PDF present but could not be opened

# Stub body for orders with no recoverable text.
NO_TEXT_STUB = "_No text available_"

# text_quality value for records with no recoverable text (stubs) — the clean
# stage is not run on them (nothing to clean).
TEXT_QUALITY_NO_TEXT = "no-text"

# Locked corpus frontmatter field order. Superset of the light index fields,
# plus the derived + Phase-C + provenance fields.
FRONTMATTER_FIELDS = [
    "eo_id",
    "number",
    "year",
    "is_emergency",
    "date_signed",
    "mayor",
    "administration",
    "admin_note",
    "title",
    "source",
    "source_pdf_url",
    "pdf_path",
    "supersedes",
    "superseded_by",
    "establishes_entity",
    "in_effect",
    "text_source",
    "page_count",
    "text_quality",
    "dropped_header",
    "dropped_marks",
]

MANIFEST_FIELDS = [
    "eo_id",
    "year",
    "text_source",
    "classification",
    "char_count",
    "page_count",
    "md_path",
]


@dataclass
class ParsedEO:
    """One order fully parsed: frontmatter, body text, and bookkeeping."""

    frontmatter: dict
    body: str                        # CLEANED full text (the .md body)
    classification: str | None       # textlayer class, or None if no PDF
    char_count: int
    md_relpath: str
    raw_body: str = ""               # verbatim pre-clean text (-> eo.json full_text_raw)

    @property
    def text_source(self) -> str:
        return self.frontmatter["text_source"]


@dataclass
class BuildResult:
    """Aggregate outcome of a corpus build."""

    total: int = 0
    by_text_source: dict[str, int] = field(default_factory=dict)
    output_paths: dict[str, str] = field(default_factory=dict)

    def bump(self, text_source: str) -> None:
        self.by_text_source[text_source] = self.by_text_source.get(text_source, 0) + 1


def _resolve_pdf_path(record: dict, repo_root: Path) -> Path | None:
    """Absolute path to a record's PDF, or None if it has no ``pdf_path``.

    ``pdf_path`` is stored relative to the repo root (e.g. ``pdfs/2022/x.pdf``).
    """
    rel = record.get("pdf_path")
    if not rel:
        return None
    p = Path(rel)
    return p if p.is_absolute() else (repo_root / p)


def parse_record(
    record: dict,
    *,
    repo_root: Path,
    do_ocr: bool,
    ocr_config: OcrConfig | None,
    textlayer_results: list | None = None,
) -> ParsedEO:
    """Run probe -> extract/ocr -> enrich for one index record; build its output.

    ``textlayer_results`` (if given) accumulates the per-PDF probe results for the
    reproducible report.
    """
    year = int(record["year"])
    eo_id = record["eo_id"]

    body = NO_TEXT_STUB
    char_count = 0
    page_count: int | None = None
    text_source = TEXT_SOURCE_NONE
    classification: str | None = None

    pdf_path = _resolve_pdf_path(record, repo_root)
    if pdf_path is not None and pdf_path.exists():
        probe = textlayer.classify_pdf(pdf_path)
        if textlayer_results is not None:
            textlayer_results.append(probe)
        classification = probe.classification
        page_count = probe.page_count

        if classification == textlayer.CLASS_TEXT:
            extracted = extract_pdf_text(pdf_path)
            if extracted.has_text:
                body = extracted.text
                char_count = extracted.char_count
                page_count = extracted.page_count
                text_source = TEXT_SOURCE_BORN_DIGITAL
            else:
                # Classified text but nothing extractable — flag, don't fabricate.
                text_source = TEXT_SOURCE_UNREADABLE
        elif classification == textlayer.CLASS_SCANNED:
            if do_ocr:
                extracted = ocr_and_extract(pdf_path, config=ocr_config)
                if extracted.text_source == TEXT_SOURCE_OCR and extracted.has_text:
                    body = extracted.text
                    char_count = extracted.char_count
                    page_count = extracted.page_count or page_count
                    text_source = TEXT_SOURCE_OCR
                else:
                    text_source = TEXT_SOURCE_OCR_FAILED
            else:
                text_source = TEXT_SOURCE_OCR_SKIPPED
        else:  # CLASS_ERROR
            text_source = TEXT_SOURCE_UNREADABLE
    elif pdf_path is not None:
        # pdf_path recorded but the file isn't on disk — same as no text.
        logger.warning("%s: pdf_path %s not found on disk", eo_id, record["pdf_path"])
        text_source = TEXT_SOURCE_NONE

    # --- Clean stage: post-process the extracted/OCR'd text ----------------- #
    # OCR docs get the full clean (header trim, file-marks, title/date, tier).
    # Born-digital docs pass through byte-for-byte (apply_body_edits=False) — only
    # a genuinely-empty title/date is gap-filled. No-text stubs are not cleaned.
    raw_body = body
    clean = _run_clean_stage(record, body, text_source=text_source, year=year)
    body = clean["body"]
    raw_body = clean["raw_body"]
    char_count = len(body)

    frontmatter = _build_frontmatter(
        record, text_source=text_source, page_count=page_count,
        title=clean["title"], date_signed=clean["date_signed"],
        text_quality=clean["text_quality"], dropped_header=clean["dropped_header"],
        dropped_marks=clean["dropped_marks"],
    )
    md_relpath = f"{year}/{eo_id}.md"
    return ParsedEO(
        frontmatter=frontmatter,
        body=body,
        classification=classification,
        char_count=char_count,
        md_relpath=md_relpath,
        raw_body=raw_body,
    )


def _run_clean_stage(record: dict, body: str, *, text_source: str,
                     year: int) -> dict:
    """Apply the clean stage per ``text_source``; return the fields the corpus needs.

    * OCR -> full clean (body may change; header/marks relocated; title/date gate).
    * born-digital -> pass-through body (byte-identical); title/date gap-fill only.
    * anything else (no-text stub, ocr-skipped/failed, unreadable) -> not cleaned.
    """
    if text_source in (TEXT_SOURCE_BORN_DIGITAL, TEXT_SOURCE_OCR):
        result = clean_record(
            body,
            year=year,
            existing_title=record.get("title"),
            existing_date_signed=record.get("date_signed"),
            text_source=text_source,
            apply_body_edits=(text_source == TEXT_SOURCE_OCR),
        )
        return {
            "body": result.full_text,
            "raw_body": result.full_text_raw,
            "title": result.title,
            "date_signed": result.date_signed,
            "text_quality": result.text_quality,
            "dropped_header": result.dropped_header,
            "dropped_marks": result.dropped_marks,
        }
    # No recoverable text — leave everything as-is.
    return {
        "body": body,
        "raw_body": body,
        "title": record.get("title"),
        "date_signed": record.get("date_signed"),
        "text_quality": TEXT_QUALITY_NO_TEXT,
        "dropped_header": "",
        "dropped_marks": [],
    }


def _build_frontmatter(record: dict, *, text_source: str, page_count: int | None,
                       title, date_signed, text_quality: str,
                       dropped_header: str, dropped_marks: list) -> dict:
    """Assemble the locked frontmatter dict for one order.

    ``title`` / ``date_signed`` are the post-clean values (a gate-accepted
    extraction fills a previously-empty field; existing values pass through). The
    clean-stage provenance (``text_quality``/``dropped_header``/``dropped_marks``)
    is carried so consumers can see what was relocated and how much to trust it.
    """
    derived = enrich_record(record)
    merged = {
        "eo_id": record["eo_id"],
        "number": record.get("number"),
        "year": int(record["year"]),
        "is_emergency": bool(record["is_emergency"]),
        "date_signed": date_signed,
        "mayor": derived["mayor"],
        "administration": derived["administration"],
        "admin_note": derived["admin_note"],
        "title": title,
        "source": record.get("source"),
        "source_pdf_url": record.get("source_pdf_url"),
        "pdf_path": record.get("pdf_path"),
        "supersedes": derived["supersedes"],
        "superseded_by": derived["superseded_by"],
        "establishes_entity": derived["establishes_entity"],
        "in_effect": derived["in_effect"],
        "text_source": text_source,
        "page_count": page_count,
        "text_quality": text_quality,
        "dropped_header": dropped_header,
        "dropped_marks": dropped_marks,
    }
    # Emit in the locked order.
    return {k: merged[k] for k in FRONTMATTER_FIELDS}


def render_markdown(parsed: ParsedEO) -> str:
    """Render one order's ``.md``: YAML frontmatter block + body."""
    front = yaml.safe_dump(
        parsed.frontmatter,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return f"---\n{front}---\n\n{parsed.body}\n"


def build_corpus(
    records: Iterable[dict],
    *,
    repo_root: str | Path,
    corpus_dir: str | Path,
    index_dir: str | Path,
    do_ocr: bool = True,
    ocr_config: OcrConfig | None = None,
    year: int | None = None,
    limit: int | None = None,
) -> BuildResult:
    """Parse every record and emit the corpus. Returns a :class:`BuildResult`.

    ``year`` restricts to one signing year; ``limit`` caps the number of records
    (both are for fast/small runs — full-corpus OCR is a deliberate gated run).
    """
    repo_root = Path(repo_root)
    corpus_dir = Path(corpus_dir)
    index_dir = Path(index_dir)

    selected = [r for r in records if year is None or int(r["year"]) == year]
    if limit is not None:
        selected = selected[:limit]

    result = BuildResult(total=len(selected))
    textlayer_results: list = []
    bulk: list[dict] = []
    manifest_rows: list[dict] = []

    for record in selected:
        parsed = parse_record(
            record,
            repo_root=repo_root,
            do_ocr=do_ocr,
            ocr_config=ocr_config,
            textlayer_results=textlayer_results,
        )
        result.bump(parsed.text_source)

        # Write the per-EO markdown.
        md_path = corpus_dir / parsed.md_relpath
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(parsed), encoding="utf-8")

        # Accumulate the bulk record (metadata + cleaned full text + verbatim raw).
        bulk.append({**parsed.frontmatter,
                     "full_text": parsed.body,
                     "full_text_raw": parsed.raw_body})
        manifest_rows.append({
            "eo_id": parsed.frontmatter["eo_id"],
            "year": parsed.frontmatter["year"],
            "text_source": parsed.text_source,
            "classification": parsed.classification or "",
            "char_count": parsed.char_count,
            "page_count": "" if parsed.frontmatter["page_count"] is None
                          else parsed.frontmatter["page_count"],
            "md_path": f"corpus/{parsed.md_relpath}",
        })
        logger.info("parsed %s [%s]", parsed.frontmatter["eo_id"], parsed.text_source)

    # Bulk JSON.
    corpus_dir.mkdir(parents=True, exist_ok=True)
    eo_json = corpus_dir / "eo.json"
    eo_json.write_text(json.dumps(bulk, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")

    # Corpus manifest.
    manifest_path = corpus_dir / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(manifest_rows)

    # Probe report (side output; index/ is gitignored regenerated too).
    report_path = textlayer.write_textlayer_report(
        textlayer_results, index_dir / "textlayer_report.json"
    )

    result.output_paths = {
        "eo_json": str(eo_json),
        "manifest": str(manifest_path),
        "textlayer_report": str(report_path),
        "corpus_dir": str(corpus_dir),
    }
    return result
