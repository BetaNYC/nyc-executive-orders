"""Emit the pre-1974 corpus from split volume OCR — Phase E, stage 2's tail.

Chains, per bound volume::

    page JSON -> volume_split -> [date ladder] -> clean -> enrich -> emit

Outputs:
  * ``corpus/YYYY/<eo_id>.md`` — the SAME locked YAML frontmatter every other
    era uses (:data:`build_corpus.FRONTMATTER_FIELDS`), so a 1951 order reads
    exactly like a 2024 one.
  * ``corpus/eo_pre1974.json`` — the bulk record set. Deliberately NOT
    ``corpus/eo.json``: that file is the NYC Admin Code § 3-113.1 deliverable,
    which the statute itself scopes to orders issued on or after 1974-01-01.
    These are older, second-source, and OCR'd from bound compilations rather
    than from an original filing, so they ship alongside rather than inside it.
    Keeping them separate also leaves ``eo.json``'s record count, the corpus
    shrink guard, and the real-data regression tests untouched.
  * ``corpus/manifest_pre1974.csv`` — per-record parse ledger.
  * ``corpus/pre1974_provenance.json`` — the sidecar (see :func:`_provenance`).
  * ``pre1974_report.md`` — the run report, beside ``gpp_integration_report.md``.

Fully offline and idempotent: it reads committed page JSON, never the PDFs and
never the model, so re-running is cheap and yields byte-identical output.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import clean, volume_split
from .build_corpus import (
    MANIFEST_FIELDS,
    NO_TEXT_STUB,
    TEXT_QUALITY_NO_TEXT,
    build_frontmatter,
    render_markdown,
)
from .identity import mint_pre1974_id
from .vlm_ocr import TEXT_SOURCE_OCR_VLM, TEXT_SOURCE_OCR_VLM_FAILED

logger = logging.getLogger("nyc_executive_orders.build_pre1974")

# Provenance tag for records minted from a pre-1974 bound compilation. Distinct
# from SOURCE_GPP (the per-order GPP deposits that closed 1974+ gaps): these come
# from a *volume*, so their pdf_path points at a 100-400 page book, not at the
# order alone.
SOURCE_GPP_VOLUME = "gpp-volume"

# A record touched by any page-level OCR failure is forced here regardless of
# what the text metrics say. A truncated or unparseable page means the body is
# incomplete; clean tiering measures the text that IS there and would happily
# call the surviving fragment "clean".
_FORCE_REVIEW_FLAGS = ("parse-error", "truncated", "empty-output", "low-ink-coverage")

# Volume filenames encode their coverage: <start>_<end>_<Mayors>_<Type>.pdf.
_VOLUME_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})_")


@dataclass
class Volume:
    """One bound compilation, from ``sources/gpp/volumes.json``."""

    gpp_id: str
    fileset_id: str
    pdf_relpath: str
    description: str
    download_url: str
    start_date: str
    end_date: str

    @property
    def filename(self) -> str:
        return Path(self.pdf_relpath).name

    @property
    def stem(self) -> str:
        return Path(self.pdf_relpath).stem

    @property
    def min_year(self) -> int:
        return int(self.start_date[:4])

    @property
    def max_year(self) -> int:
        return int(self.end_date[:4])


@dataclass
class BuildPre1974Result:
    """Aggregate outcome of a pre-1974 build."""

    volumes: list[dict] = field(default_factory=list)
    total: int = 0
    by_quality: dict[str, int] = field(default_factory=dict)
    output_paths: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def bump(self, quality: str) -> None:
        self.by_quality[quality] = self.by_quality.get(quality, 0) + 1


def load_volumes(volumes_json: str | Path) -> list[Volume]:
    """Read ``sources/gpp/volumes.json`` into :class:`Volume` specs.

    Coverage dates come from the FILENAME, which the GPP integration built from
    each volume's own description prose ("between January 7, 1946 and October 4,
    1950") — not from ``date_published``, which is the compilation's print date
    and runs years after the orders it contains.
    """
    data = json.loads(Path(volumes_json).read_text(encoding="utf-8"))
    volumes: list[Volume] = []
    for entry in data.get("volumes", []):
        paths = entry.get("local_paths") or []
        filesets = entry.get("fileset_ids") or []
        urls = entry.get("download_urls") or []
        if not paths:
            continue
        name = Path(paths[0]).name
        m = _VOLUME_NAME_RE.match(name)
        if not m:
            logger.warning("volume %s: filename %s carries no date range; skipped",
                           entry.get("gpp_id"), name)
            continue
        volumes.append(Volume(
            gpp_id=entry.get("gpp_id", ""),
            fileset_id=filesets[0] if filesets else "",
            pdf_relpath=paths[0],
            description=entry.get("description", ""),
            download_url=urls[0] if urls else "",
            start_date=m.group(1),
            end_date=m.group(2),
        ))
    return volumes


def load_page_records(ocr_dir: str | Path) -> list[dict]:
    """Every ``page_XXXX.json`` in a volume's committed OCR directory."""
    directory = Path(ocr_dir)
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("page_*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("%s: unreadable page record (%s); skipped", path, exc)
    return records


# --------------------------------------------------------------------------- #
# Date ladder                                                                   #
# --------------------------------------------------------------------------- #

def resolve_dates(documents: list, volume: Volume) -> None:
    """Fill any date still missing by chronological bracketing, in place.

    The third rung of the ladder. Rungs 1 (the instrument's own printed date)
    and 2 (the volume's subject index) already ran inside
    :func:`volume_split.reconcile_with_index`. A volume is bound in order, so an
    undated instrument sitting between two neighbours dated in the SAME year can
    take that year — and only the year. The day is never invented: the record
    gets ``date_signed: null`` and a year, which is exactly the honest answer.
    """
    for i, doc in enumerate(documents):
        if doc.date_on_page:
            continue
        before = next((d.date_on_page for d in reversed(documents[:i]) if d.date_on_page), None)
        after = next((d.date_on_page for d in documents[i + 1:] if d.date_on_page), None)
        if before and after and before[:4] == after[:4]:
            doc.flags.append(
                f"year-from-position: undated; bracketed by {before} and {after}, "
                f"so the year is {before[:4]}. Day deliberately not inferred."
            )
            doc.date_source = "position"
            doc._bracketed_year = int(before[:4])  # type: ignore[attr-defined]
        else:
            doc.flags.append(
                "no-date: no date on the instrument, none in the index, and "
                "neighbouring instruments do not agree on a year"
            )


def record_year(doc, volume: Volume) -> int:
    """The signing year for a document — the corpus directory it lands in."""
    if doc.date_on_page:
        return int(doc.date_on_page[:4])
    bracketed = getattr(doc, "_bracketed_year", None)
    if bracketed:
        return bracketed
    # Last resort: the volume's own start year. Flagged already by resolve_dates.
    return volume.min_year


# --------------------------------------------------------------------------- #
# Emit                                                                          #
# --------------------------------------------------------------------------- #

def _mint_ids(documents: list, volume: Volume, used: dict[str, int] | None = None) -> list[str]:
    """One id per document, guaranteed unique across the whole BUILD.

    Two documents can still collide after index reconciliation — an unrecovered
    duplicate number, or two unnumbered instruments sharing a date — so the
    occurrence suffix is applied at mint time rather than trusted not to be
    needed. An overwritten record is silent data loss; a suffixed id is visible.

    ``used`` is the caller's counter, shared across volumes. It has to be: an
    unnumbered record's id is minted from its date alone, and two volumes DO
    cover the same dates — the Lindsay Memoranda and Lindsay Orders compilations
    both run through 1970-71, and both yielded an unnumbered instrument dated
    1970-07-01, i.e. the same ``1970-EO-D0701``. With a per-volume counter each
    was "unique" in its own volume and the second silently overwrote the first's
    file, leaving one more record in the bulk JSON than on disk. Defaults to a
    fresh dict so a single-volume call still behaves.
    """
    ids: list[str] = []
    used = {} if used is None else used
    for doc in documents:
        year = record_year(doc, volume)
        month_day = (
            doc.date_on_page[5:7] + doc.date_on_page[8:10] if doc.date_on_page else None
        )
        base = mint_pre1974_id(year, doc.number, doc.series, month_day=month_day)
        occurrence = used.get(base, 0)
        used[base] = occurrence + 1
        eo_id = mint_pre1974_id(
            year, doc.number, doc.series, month_day=month_day, occurrence=occurrence
        )
        if occurrence:
            doc.flags.append(
                f"id-collision: {base} was already minted in this build; this "
                f"record is {eo_id}. Two documents claim the same identity — "
                "check the segmentation before publishing."
            )
        ids.append(eo_id)
    return ids


def _provenance(doc, eo_id: str, volume: Volume, split, ocr_meta: dict) -> dict:
    """The sidecar entry for one record.

    Everything the locked 21-field frontmatter has no room for lives here, on the
    ``corpus/gpp_provenance.json`` precedent: how the text was made, which pages
    it came from, what the volume's own index says about it, and every QA flag
    raised along the way. This is what makes a record auditable back to a page of
    a scan without widening the schema every era.
    """
    entry = split.index_entries.get(doc.number) if doc.number else None
    return {
        "eo_id": eo_id,
        "volume": {
            "gpp_id": volume.gpp_id,
            "fileset_id": volume.fileset_id,
            "filename": volume.filename,
            "pdf_path": volume.pdf_relpath,
            "download_url": volume.download_url,
            "covers": f"{volume.start_date}..{volume.end_date}",
        },
        "page_span": list(doc.page_span),
        "pages": doc.pages,
        "instrument": {
            "series": doc.series,
            "number": doc.number,
            "number_source": doc.number_source,
            "printed_label": doc.printed_label,
            "signed_by": doc.signed_by,
        },
        "date": {
            "value": doc.date_on_page,
            "source": doc.date_source,
            "index_date": entry.date if entry else None,
        },
        "index_subjects": entry.subjects if entry else [],
        "ocr": ocr_meta,
        "flags": doc.flags,
    }


def _forced_review(doc) -> bool:
    return any(
        any(flag in f for flag in _FORCE_REVIEW_FLAGS) for f in doc.flags
    )


def build_pre1974(
    volumes: list[Volume],
    *,
    repo_root: str | Path,
    corpus_dir: str | Path,
    ocr_root: str | Path,
    ocr_meta: dict | None = None,
) -> BuildPre1974Result:
    """Split every volume that has OCR on disk and emit the pre-1974 corpus."""
    repo_root = Path(repo_root)
    corpus_dir = Path(corpus_dir)
    ocr_root = Path(ocr_root)
    ocr_meta = ocr_meta or {}

    result = BuildPre1974Result()
    bulk: list[dict] = []
    manifest_rows: list[dict] = []
    provenance: dict[str, dict] = {}
    # Shared across every volume — see _mint_ids. Two compilations overlap in
    # time, so a date-derived id minted in one can collide with the next.
    minted: dict[str, int] = {}

    for volume in volumes:
        records = load_page_records(ocr_root / volume.stem)
        if not records:
            result.volumes.append({
                "filename": volume.filename, "status": "not-ocred",
                "pages": 0, "documents": 0,
            })
            continue

        split = volume_split.split_volume(
            records, min_year=volume.min_year, max_year=volume.max_year,
            description=volume.description,
        )
        resolve_dates(split.documents, volume)
        ids = _mint_ids(split.documents, volume, minted)

        # strict: ids is minted one-per-document, so a length mismatch is a bug
        # that would silently drop records off the end of the shorter list.
        for doc, eo_id in zip(split.documents, ids, strict=True):
            year = record_year(doc, volume)
            body = doc.body.strip()
            has_text = bool(body)

            index_record = {
                "eo_id": eo_id,
                "number": doc.number,
                "year": year,
                "is_emergency": False,   # no emergency series exists pre-1974
                "date_signed": doc.date_on_page,
                "title": None,
                "source": SOURCE_GPP_VOLUME,
                "source_pdf_url": volume.download_url,
                "pdf_path": volume.pdf_relpath,
            }

            if has_text:
                cleaned = clean.clean_record(
                    body,
                    year=year,
                    # The document's own stated SUBJECT:/RE: line, or None. The
                    # generic caps-block title extractor is switched OFF for this
                    # era — see clean_record's `extract_title` docs.
                    existing_title=doc.subject,
                    existing_date_signed=doc.date_on_page,
                    text_source=TEXT_SOURCE_OCR_VLM,
                    apply_body_edits=True,
                    extract_title=False,
                )
                out_body, raw_body = cleaned.full_text, cleaned.full_text_raw
                title, quality = cleaned.title, cleaned.text_quality
                dropped_header, dropped_marks = cleaned.dropped_header, cleaned.dropped_marks
                # clean_record only fills an EMPTY date, and never invents a day;
                # a date we already resolved wins, so this only ever gap-fills.
                date_signed = doc.date_on_page or cleaned.date_signed
            else:
                out_body = raw_body = NO_TEXT_STUB
                title, quality = None, TEXT_QUALITY_NO_TEXT
                dropped_header, dropped_marks = "", []
                date_signed = doc.date_on_page

            if _forced_review(doc) and quality != TEXT_QUALITY_NO_TEXT:
                quality = clean.TEXT_QUALITY_REVIEW

            index_record["date_signed"] = date_signed
            frontmatter = build_frontmatter(
                index_record,
                text_source=TEXT_SOURCE_OCR_VLM if has_text else TEXT_SOURCE_OCR_VLM_FAILED,
                page_count=len(doc.pages),
                title=title,
                date_signed=date_signed,
                text_quality=quality,
                dropped_header=dropped_header,
                dropped_marks=dropped_marks,
            )

            md_relpath = f"{year}/{eo_id}.md"
            md_path = corpus_dir / md_relpath
            md_path.parent.mkdir(parents=True, exist_ok=True)
            md_path.write_text(
                render_markdown(_Parsed(frontmatter, out_body)), encoding="utf-8"
            )

            bulk.append({**frontmatter, "full_text": out_body, "full_text_raw": raw_body})
            manifest_rows.append({
                "eo_id": eo_id,
                "year": year,
                "text_source": frontmatter["text_source"],
                "classification": "scanned",
                "char_count": len(out_body),
                "page_count": len(doc.pages),
                "md_path": f"corpus/{md_relpath}",
            })
            provenance[eo_id] = _provenance(doc, eo_id, volume, split, ocr_meta)
            result.bump(quality)
            result.total += 1

        recon = split.reconciliation()
        result.volumes.append({
            "filename": volume.filename,
            "status": "ok",
            "pages": len(records),
            "series": split.series,
            "documents": recon["n_documents"],
            "expected_from_index": recon["n_expected"],
            "found": recon["n_found"],
            "missing": recon["missing"],
            "unindexed": recon["unindexed"],
            "unnumbered": recon["n_unnumbered"],
            "failed_pages": split.failed_pages,
            "flags": split.flags,
        })
        result.flags.extend(f"{volume.filename}: {f}" for f in split.flags)

    corpus_dir.mkdir(parents=True, exist_ok=True)
    eo_json = corpus_dir / "eo_pre1974.json"
    eo_json.write_text(json.dumps(bulk, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")

    manifest_path = corpus_dir / "manifest_pre1974.csv"
    with manifest_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(manifest_rows)

    sidecar_path = corpus_dir / "pre1974_provenance.json"
    sidecar_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")

    result.output_paths = {
        "eo_pre1974_json": str(eo_json),
        "manifest": str(manifest_path),
        "provenance": str(sidecar_path),
        "corpus_dir": str(corpus_dir),
    }
    return result


@dataclass
class _Parsed:
    """Minimal stand-in carrying what :func:`build_corpus.render_markdown` reads.

    render_markdown only touches ``.frontmatter`` and ``.body``; building a full
    ``ParsedEO`` here would mean inventing values for fields this era does not
    have (a textlayer classification, an md_relpath it already knows).
    """

    frontmatter: dict
    body: str


def render_report(result: BuildPre1974Result) -> str:
    """Human-readable run report — the found-vs-expected ledger per volume."""
    L: list[str] = []
    L.append("# Phase E — pre-1974 volume split\n")
    L.append(f"Records emitted: **{result.total}**\n")

    L.append("\n## Text quality\n")
    for quality in ("clean", "minor-noise", "needs-review", "no-text"):
        L.append(f"- {quality}: **{result.by_quality.get(quality, 0)}**")

    L.append("\n## Per volume\n")
    L.append("| Volume | Pages | Series | Docs | Index says | Found | Missing | Unindexed |")
    L.append("|---|---:|---|---:|---:|---:|---|---|")
    for v in result.volumes:
        if v.get("status") == "not-ocred":
            L.append(f"| `{v['filename']}` | — | — | — | — | — | *not yet OCR'd* | |")
            continue
        L.append(
            f"| `{v['filename']}` | {v['pages']} | {v['series']} | {v['documents']} "
            f"| {v['expected_from_index']} | {v['found']} "
            f"| {', '.join(v['missing']) or '—'} "
            f"| {', '.join(v['unindexed']) or '—'} |"
        )

    failed = [(v["filename"], v["failed_pages"]) for v in result.volumes
              if v.get("failed_pages")]
    if failed:
        L.append("\n## Pages the OCR could not read\n")
        L.append("These pages produced no usable output. Every record covering one "
                 "is forced to `needs-review`.\n")
        for filename, pages in failed:
            L.append(f"- `{filename}`: {', '.join(str(p) for p in pages)}")

    if result.flags:
        L.append("\n## Volume-level flags\n")
        for flag in result.flags:
            L.append(f"- {flag}")

    L.append("\n---\n")
    L.append("Reconciliation compares against each volume's OWN subject index — "
             "the compilers' contemporaneous list of what the volume contains. "
             "`Missing` means the index names an instrument the segmenter did not "
             "produce; `Unindexed` means the reverse. Both are findings to chase, "
             "not errors to suppress.\n")
    return "\n".join(L)
