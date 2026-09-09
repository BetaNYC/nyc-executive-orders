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

from . import clean, textlayer
from .clean import clean_record
from .enrich import enrich_record
from .extract import TEXT_SOURCE_BORN_DIGITAL, extract_pdf_text
from .ocr import (
    TEXT_SOURCE_OCR,
    TEXT_SOURCE_OCR_FAILED,
    OcrConfig,
    ocr_and_extract,
)
from .vlm_corpus import DEFAULT_VLM_OCR_ROOT, load_vlm_document
from .vlm_ocr import TEXT_SOURCE_OCR_VLM, TEXT_SOURCE_OCR_VLM_FAILED

logger = logging.getLogger("nyc_executive_orders.build_corpus")

# Which engine transcribes a SCANNED PDF. Born-digital orders are unaffected by
# every value here: a real text layer goes to extract() regardless.
#
#   auto       VLM where records exist, Tesseract where they do not. THE ROLLOUT
#              SETTING: it lets the corpus be rebuilt at any point during a long
#              OCR run and still come out complete, with manifest.csv showing
#              exactly how far the migration has got.
#   vlm        VLM where records exist, ocr-skipped where they do not. Never
#              silently falls back. Reproducible, and needs no Tesseract at all.
#   tesseract  Ignore the VLM records entirely. This is the rollback path.
#
# Under `auto` the build is a function of what is on disk, which is exactly why
# stage 1 commits its page records rather than leaving them in scratch.
OCR_ENGINE_AUTO = "auto"
OCR_ENGINE_VLM = "vlm"
OCR_ENGINE_TESSERACT = "tesseract"
OCR_ENGINE_CHOICES = (OCR_ENGINE_AUTO, OCR_ENGINE_VLM, OCR_ENGINE_TESSERACT)

# eo_id-keyed record of how the VLM made each body it made, beside the existing
# gpp_provenance.json / pre1974_provenance.json. It is what lets a later clean
# sweep re-apply the forced-review rule (see _vlm_provenance).
VLM_PROVENANCE_FILENAME = "vlm_provenance.json"

# A VLM page flag in this set forces the record to needs-review no matter what the
# text metrics say. Same rule, same reasoning, as build_pre1974._FORCE_REVIEW_FLAGS:
# clean tiering measures the text that IS there and would happily call a truncated
# body clean. Matched by substring, since some flags carry a value.
_FORCE_REVIEW_FLAGS = (
    "parse-error",
    "truncated",
    "empty-output",
    "low-ink-coverage",
    "all-pages-blank",
    "no-pages-recorded",
    "no-measurable-ink",
)

# text_source values that this module adds beyond the extract/ocr ones.
TEXT_SOURCE_NONE = "none"                # no PDF on disk (the 53 gap EOs)
TEXT_SOURCE_OCR_SKIPPED = "ocr-skipped"  # scanned, but run under --no-ocr
TEXT_SOURCE_UNREADABLE = "unreadable"    # PDF present but could not be opened

# Stub body for orders with no recoverable text.
NO_TEXT_STUB = "_No text available_"


class CorpusShrinkError(RuntimeError):
    """Raised when a build would overwrite the on-disk corpus with fewer docs.

    The on-disk ``index/eo_index.json`` is a gitignored, regenerated artifact; a
    scoped harvest can leave it holding only its own records. A default build from
    that partial index would silently shrink ``corpus/eo.json`` and delete the
    rest. This guard turns that data-loss footgun into a loud, opt-in decision.
    """


def _existing_corpus_count(corpus_dir: Path) -> int | None:
    """Number of records in ``corpus_dir/eo.json``, or None if there is none yet.

    A missing/empty/unreadable eo.json is treated as "nothing to protect" (None)
    so a first build or a build into a fresh scratch dir is never blocked.
    """
    eo_json = corpus_dir / "eo.json"
    if not eo_json.exists():
        return None
    try:
        existing = json.loads(eo_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return len(existing) if isinstance(existing, list) else None

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
    # How the VLM made this body, when it did: pages, QA flags, forced_review.
    # Collected into corpus/vlm_provenance.json — see _vlm_provenance().
    vlm_provenance: dict | None = None

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
    ocr_engine: str = OCR_ENGINE_AUTO,
    vlm_ocr_root: str | Path | None = None,
) -> ParsedEO:
    """Run probe -> extract/ocr/vlm -> enrich for one index record; build its output.

    ``textlayer_results`` (if given) accumulates the per-PDF probe results for the
    reproducible report.

    ``ocr_engine`` picks what transcribes a SCANNED PDF (see OCR_ENGINE_CHOICES).
    ``vlm_ocr_root`` is where stage 1's committed page records live; None means
    the default, and the VLM branch is skipped entirely under
    ``ocr_engine="tesseract"``. Born-digital PDFs ignore both.
    """
    vlm_ocr_root = Path(vlm_ocr_root) if vlm_ocr_root is not None else (
        repo_root / DEFAULT_VLM_OCR_ROOT
    )
    force_review = False
    vlm_provenance: dict | None = None
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
            vlm = None
            if ocr_engine in (OCR_ENGINE_AUTO, OCR_ENGINE_VLM):
                vlm = load_vlm_document(vlm_ocr_root, year, eo_id)

            if vlm is not None and vlm.has_text:
                body = vlm.text
                char_count = len(body)
                page_count = vlm.page_count or page_count
                text_source = TEXT_SOURCE_OCR_VLM
                force_review = _forced_review(vlm.flags)
                vlm_provenance = _vlm_provenance(vlm, force_review, repo_root)
            elif vlm is not None and vlm.records_present:
                # Records on disk that yielded nothing usable is a REAL, recorded
                # failure — not the same thing as "stage 1 has not got here yet",
                # and it must never read as an ordinary empty order.
                text_source = TEXT_SOURCE_OCR_VLM_FAILED
                force_review = True
                vlm_provenance = _vlm_provenance(vlm, force_review, repo_root)
            elif ocr_engine == OCR_ENGINE_VLM:
                # Asked for the VLM and it has no records here. Say so; never fall
                # back to Tesseract behind the operator's back.
                text_source = TEXT_SOURCE_OCR_SKIPPED
            elif do_ocr:
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
    clean = _run_clean_stage(record, body, text_source=text_source, year=year,
                             force_review=force_review)
    body = clean["body"]
    raw_body = clean["raw_body"]
    char_count = len(body)

    frontmatter = build_frontmatter(
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
        vlm_provenance=vlm_provenance,
    )


def _vlm_provenance(vlm, force_review: bool, repo_root: Path) -> dict:
    """How this record's text was made, for ``corpus/vlm_provenance.json``.

    Exists because :func:`clean_existing_corpus` re-cleans from ``full_text_raw``
    and cannot see the page records: without this sidecar a clean sweep would
    silently promote a truncated or low-coverage record back to ``clean``. Same
    role, and the same shape of file, as ``corpus/gpp_provenance.json`` and
    ``corpus/pre1974_provenance.json`` — so no locked frontmatter field moves.
    """
    try:
        ocr_dir = str(vlm.ocr_dir.relative_to(repo_root))
    except ValueError:
        # An --vlm-ocr-root outside the repo (a scratch run). Record it as given
        # rather than inventing a relative path.
        ocr_dir = str(vlm.ocr_dir)
    return {
        "eo_id": vlm.eo_id,
        "ocr_dir": ocr_dir,
        "pages": vlm.page_count,
        "pages_with_text": vlm.doc.pages_with_text,
        "pages_skipped": vlm.doc.pages_skipped,
        "flags": list(vlm.flags),
        "forced_review": force_review,
        "tables": vlm.doc.tables,
        "pictures": vlm.doc.pictures,
        "blank_override": vlm.doc.blank_override,
        "element_counts": dict(vlm.doc.element_counts),
    }


def _forced_review(flags: Iterable[str]) -> bool:
    """Did any page of this document fail loudly enough to force needs-review?

    Matched by substring because some flags carry a value
    (``low-ink-coverage:0.912``). Same rule as :func:`build_pre1974._forced_review`.
    """
    return any(any(f in flag for f in _FORCE_REVIEW_FLAGS) for flag in flags)


def _run_clean_stage(record: dict, body: str, *, text_source: str,
                     year: int, force_review: bool = False) -> dict:
    """Apply the clean stage per ``text_source``; return the fields the corpus needs.

    * OCR / VLM-OCR -> full clean (body may change; header/marks relocated;
      title/date gate).
    * born-digital -> pass-through body (byte-identical); title/date gap-fill only.
    * anything else (no-text stub, ocr-skipped/failed, unreadable) -> not cleaned.

    ``force_review`` demotes the computed tier to ``needs-review``. It carries the
    VLM's page-level QA signals, which the text metrics cannot see: a truncated
    body is perfectly clean prose right up to where it stops.
    """
    if text_source in (TEXT_SOURCE_BORN_DIGITAL, TEXT_SOURCE_OCR, TEXT_SOURCE_OCR_VLM):
        result = clean_record(
            body,
            year=year,
            existing_title=record.get("title"),
            existing_date_signed=record.get("date_signed"),
            text_source=text_source,
            # Born-digital text has no OCR header noise and passes through
            # byte-for-byte; everything else gets the full clean.
            apply_body_edits=(text_source != TEXT_SOURCE_BORN_DIGITAL),
        )
        text_quality = result.text_quality
        if force_review and text_quality != TEXT_QUALITY_NO_TEXT:
            text_quality = clean.TEXT_QUALITY_REVIEW
        return {
            "body": result.full_text,
            "raw_body": result.full_text_raw,
            "title": result.title,
            "date_signed": result.date_signed,
            "text_quality": text_quality,
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


def build_frontmatter(record: dict, *, text_source: str, page_count: int | None,
                      title, date_signed, text_quality: str,
                      dropped_header: str, dropped_marks: list) -> dict:
    """Assemble the locked frontmatter dict for one order.

    Public (it was ``_build_frontmatter``) because Phase E's
    :mod:`build_pre1974` emits through it too. Every corpus writer routes the
    field set through this one function, so ``FRONTMATTER_FIELDS`` stays the
    single definition of the schema and no era can drift its own shape.

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
    ocr_engine: str = OCR_ENGINE_AUTO,
    vlm_ocr_root: str | Path | None = None,
    year: int | None = None,
    limit: int | None = None,
    allow_shrink: bool = False,
) -> BuildResult:
    """Parse every record and emit the corpus. Returns a :class:`BuildResult`.

    ``year`` restricts to one signing year; ``limit`` caps the number of records
    (both are for fast/small runs — full-corpus OCR is a deliberate gated run).

    Because the emit overwrites ``corpus/eo.json`` wholesale, a build with fewer
    records than the corpus already on disk would delete orders. That is refused
    with :class:`CorpusShrinkError` unless ``allow_shrink`` is set — the one guard
    every caller of this shared emit path routes through (year/limit scoped runs
    included). A first build, or a build into a fresh/empty ``corpus_dir``, is
    never blocked.
    """
    repo_root = Path(repo_root)
    corpus_dir = Path(corpus_dir)
    index_dir = Path(index_dir)

    selected = [r for r in records if year is None or int(r["year"]) == year]
    if limit is not None:
        selected = selected[:limit]

    # Shrink guard: never silently overwrite a larger on-disk corpus with fewer
    # docs. Fail loud, name the counts, and point at the usual cause.
    existing = _existing_corpus_count(corpus_dir)
    if existing is not None and len(selected) < existing and not allow_shrink:
        raise CorpusShrinkError(
            f"refusing to shrink {corpus_dir / 'eo.json'} from {existing} to "
            f"{len(selected)} records (would delete {existing - len(selected)} "
            "orders). If this is intentional, pass allow_shrink=True "
            "(--allow-shrink). Common cause: the on-disk index is a partial "
            "harvest artifact — regenerate the full index "
            "(scripts/rebuild_index_from_corpus.py) before parsing."
        )

    result = BuildResult(total=len(selected))
    textlayer_results: list = []
    bulk: list[dict] = []
    manifest_rows: list[dict] = []

    vlm_provenance: dict[str, dict] = {}
    for record in selected:
        parsed = parse_record(
            record,
            repo_root=repo_root,
            do_ocr=do_ocr,
            ocr_config=ocr_config,
            textlayer_results=textlayer_results,
            ocr_engine=ocr_engine,
            vlm_ocr_root=vlm_ocr_root,
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
        if parsed.vlm_provenance is not None:
            vlm_provenance[parsed.frontmatter["eo_id"]] = parsed.vlm_provenance
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

    # VLM provenance sidecar. Only written when this build actually read page
    # records, so a Tesseract-only or born-digital-only build leaves any existing
    # file alone rather than truncating it to nothing.
    if vlm_provenance:
        sidecar = corpus_dir / VLM_PROVENANCE_FILENAME
        sidecar.write_text(
            json.dumps(vlm_provenance, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result.output_paths["vlm_provenance"] = str(sidecar)
    return result


# text_source -> textlayer classification, for the sweep's manifest (no re-probe).
_CLASS_FOR_SOURCE = {
    TEXT_SOURCE_BORN_DIGITAL: textlayer.CLASS_TEXT,
    TEXT_SOURCE_OCR: textlayer.CLASS_SCANNED,
    TEXT_SOURCE_OCR_FAILED: textlayer.CLASS_SCANNED,
    TEXT_SOURCE_OCR_SKIPPED: textlayer.CLASS_SCANNED,
    TEXT_SOURCE_OCR_VLM: textlayer.CLASS_SCANNED,
    TEXT_SOURCE_OCR_VLM_FAILED: textlayer.CLASS_SCANNED,
}


def clean_existing_corpus(
    records: Iterable[dict],
    *,
    corpus_dir: str | Path,
    vlm_provenance: dict | None = None,
) -> BuildResult:
    """Apply ONLY the clean stage to an already-parsed corpus and re-emit it.

    This is the full-sweep post-process: it takes the existing ``eo.json`` records
    (whose ``full_text`` is the verbatim OCR/extraction — the OCR layer is NOT
    re-run) and rewrites ``corpus/YYYY/<eo_id>.md`` + ``eo.json`` + ``manifest.csv``
    with the cleaned bodies, filled metadata, and clean provenance.

    Non-destructive + idempotent: the raw input is taken from ``full_text_raw``
    when present (a prior sweep preserved it), else from ``full_text`` (the
    verbatim pre-clean corpus). So a re-run reads the same verbatim source and
    yields identical output, and ``full_text_raw`` always holds the true original.

    ``vlm_provenance`` is the parsed ``corpus/vlm_provenance.json`` sidecar,
    keyed by ``eo_id``. It carries the page-level QA verdict that the text
    metrics cannot see, and re-applying it here is the whole reason
    :func:`_vlm_provenance` writes the file (see its docstring). Without it a
    sweep silently promotes every truncated / low-ink-coverage record back to
    ``clean``: measured at 184 records on the 2026-09 corpus. It is read rather
    than discovered because ``corpus_dir`` may be a scratch directory (the
    ``run_clean_sweep --dry-run`` path), which holds no sidecar.
    """
    provenance = vlm_provenance or {}
    corpus_dir = Path(corpus_dir)
    records = list(records)
    result = BuildResult(total=len(records))
    bulk: list[dict] = []
    manifest_rows: list[dict] = []

    for record in records:
        year = int(record["year"])
        eo_id = record["eo_id"]
        text_source = record.get("text_source") or TEXT_SOURCE_NONE
        page_count = record.get("page_count")
        # Verbatim source: prefer a preserved raw (idempotent re-runs), else the
        # current full_text (still verbatim on the first sweep).
        raw_input = record.get("full_text_raw") or record.get("full_text", "")

        force_review = bool(provenance.get(eo_id, {}).get("forced_review"))
        clean = _run_clean_stage(record, raw_input, text_source=text_source,
                                 year=year, force_review=force_review)
        frontmatter = build_frontmatter(
            record, text_source=text_source, page_count=page_count,
            title=clean["title"], date_signed=clean["date_signed"],
            text_quality=clean["text_quality"], dropped_header=clean["dropped_header"],
            dropped_marks=clean["dropped_marks"],
        )
        parsed = ParsedEO(
            frontmatter=frontmatter, body=clean["body"],
            classification=_CLASS_FOR_SOURCE.get(text_source),
            char_count=len(clean["body"]), md_relpath=f"{year}/{eo_id}.md",
            raw_body=clean["raw_body"],
        )
        result.bump(text_source)

        md_path = corpus_dir / parsed.md_relpath
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(parsed), encoding="utf-8")

        bulk.append({**frontmatter,
                     "full_text": parsed.body,
                     "full_text_raw": parsed.raw_body})
        manifest_rows.append({
            "eo_id": eo_id,
            "year": year,
            "text_source": text_source,
            "classification": parsed.classification or "",
            "char_count": parsed.char_count,
            "page_count": "" if page_count is None else page_count,
            "md_path": f"corpus/{parsed.md_relpath}",
        })

    corpus_dir.mkdir(parents=True, exist_ok=True)
    eo_json = corpus_dir / "eo.json"
    eo_json.write_text(json.dumps(bulk, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    manifest_path = corpus_dir / "manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(manifest_rows)

    result.output_paths = {"eo_json": str(eo_json), "manifest": str(manifest_path),
                           "corpus_dir": str(corpus_dir)}
    return result
