"""Phase A orchestration: enumerate -> resolve PDF URL -> (optional) download
-> write index + manifest + gaps.

`run_harvest` takes an already-built `fetcher` (dependency injection). That is
what keeps the whole pipeline offline-testable: tests pass a fake fetcher and no
network is touched. The CLI (cli.py) and the supervised live script build a real
fetcher and pass it in.

Dry-run (the default) enumerates and resolves PDF URLs but issues ZERO
downloads. Downloading requires `download=True` (the CLI's `--download` flag).
Note: even a dry-run makes LIVE calls when given a real fetcher (enumeration +
article pages) — that is why the CLI/live entry points are gated, not the tests.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .download import download_pdf
from .enumerate import EOEntry, enumerate_years
from .fetch import Fetcher
from .identity import mint_eo_id
from .index import IndexRow, write_index
from .manifest import ManifestRow, write_manifest, write_gaps
from .resolve_pdf import resolve_pdf_url

logger = logging.getLogger("nyc_executive_orders.harvest")


@dataclass
class HarvestResult:
    index_rows: list[IndexRow] = field(default_factory=list)
    manifest_rows: list[ManifestRow] = field(default_factory=list)
    enumerated: int = 0
    resolved: int = 0
    downloaded: int = 0
    cached: int = 0
    errors: int = 0
    deduped: int = 0
    conflicts: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    dry_run: bool = True
    output_paths: dict = field(default_factory=dict)


def dedupe_rows(
    index_rows: list[IndexRow],
    manifest_rows: list[ManifestRow],
) -> tuple[list[IndexRow], list[ManifestRow], int, list[tuple[str, str | None, str | None]]]:
    """Collapse exact-duplicate feed records to a single record.

    The nyc.gov feed sometimes lists the *same* order twice — identical title,
    date, and PDF URL (verified in the 2022->present dry-run: 13 of 1062 rows
    were exact doubles, e.g. `2025-EO-051` "Executive Order 51" 2025-05-13
    `eo-51.pdf` listed twice). Those are noise, not distinct orders.

    Exact duplicate := same `eo_id` AND same `source_pdf_url` (equivalently the
    same title+date+pdf, which is what a doubled feed entry produces). The first
    occurrence is kept; every later exact duplicate is dropped.

    Records that share an `eo_id` but DIFFER in `source_pdf_url` are a real
    conflict, not a duplicate — two different PDFs claiming one identity. They are
    KEPT (never silently merged or dropped) and returned so the caller can surface
    them loudly. None are expected in current data; this is a guard.

    `index_rows` and `manifest_rows` are paired 1:1 by position (harvest builds
    one of each per enumerated entry), so they are deduplicated in lockstep to
    stay aligned. Returns (kept_index, kept_manifest, dropped_count, conflicts),
    where each conflict is (eo_id, first_source_pdf_url, other_source_pdf_url).
    """
    if len(index_rows) != len(manifest_rows):
        raise ValueError(
            f"index/manifest row counts diverge ({len(index_rows)} vs "
            f"{len(manifest_rows)}) — cannot dedupe in lockstep"
        )

    seen_keys: set[tuple[str, str | None]] = set()
    first_url: dict[str, str | None] = {}
    kept_index: list[IndexRow] = []
    kept_manifest: list[ManifestRow] = []
    dropped = 0
    conflicts: list[tuple[str, str | None, str | None]] = []

    for ir, mr in zip(index_rows, manifest_rows):
        key = (mr.eo_id, mr.source_pdf_url)
        if key in seen_keys:
            dropped += 1
            logger.debug(
                "index.dedup drop exact-duplicate eo_id=%s source_pdf_url=%s",
                mr.eo_id,
                mr.source_pdf_url,
            )
            continue
        if mr.eo_id in first_url and first_url[mr.eo_id] != mr.source_pdf_url:
            conflicts.append((mr.eo_id, first_url[mr.eo_id], mr.source_pdf_url))
            logger.warning(
                "index.conflict eo_id=%s has divergent source_pdf_url "
                "(first=%s other=%s) — keeping BOTH, not merging; review required",
                mr.eo_id,
                first_url[mr.eo_id],
                mr.source_pdf_url,
            )
        seen_keys.add(key)
        first_url.setdefault(mr.eo_id, mr.source_pdf_url)
        kept_index.append(ir)
        kept_manifest.append(mr)

    logger.info("index.deduped removed=%d conflicts=%d", dropped, len(conflicts))
    return kept_index, kept_manifest, dropped, conflicts


def _delayer(delay: float):
    """Return a no-arg callable that sleeps `delay` seconds (no-op if <= 0)."""
    if delay and delay > 0:
        return lambda: time.sleep(delay)
    return lambda: None


def _process_entry(
    fetcher: Fetcher,
    entry: EOEntry,
    *,
    download: bool,
    pdf_dir: Path,
    on_fetch,
    result: HarvestResult,
) -> None:
    eo_id = mint_eo_id(entry.year, entry.number, entry.is_emergency)

    pdf_url = resolve_pdf_url(fetcher, entry.article_url)
    on_fetch()
    pdf_resolved = pdf_url is not None
    if pdf_resolved:
        result.resolved += 1

    pdf_path: str | None = None
    download_status = "skipped"  # dry-run default
    download_error: str | None = None

    if download and pdf_resolved:
        outcome = download_pdf(
            fetcher, eo_id, entry.year, pdf_url, pdf_dir=pdf_dir, on_fetch=on_fetch
        )
        download_status = outcome.status
        pdf_path = outcome.pdf_path
        download_error = outcome.error
        if outcome.status == "downloaded":
            result.downloaded += 1
        elif outcome.status == "cached":
            result.cached += 1
        elif outcome.status == "error":
            result.errors += 1
    elif download and not pdf_resolved:
        download_status = "error"
        download_error = "pdf URL not resolved"
        result.errors += 1

    result.index_rows.append(
        IndexRow(
            eo_id=eo_id,
            number=entry.number,
            year=entry.year,
            is_emergency=entry.is_emergency,
            date_signed=entry.date_signed,
            title=entry.title,
            source_pdf_url=pdf_url,
            pdf_path=pdf_path,
            source=config.SOURCE_LIVE,
        )
    )
    result.manifest_rows.append(
        ManifestRow(
            eo_id=eo_id,
            number=entry.number,
            year=entry.year,
            is_emergency=entry.is_emergency,
            date_signed=entry.date_signed,
            title=entry.title,
            article_url=entry.article_url,
            source_pdf_url=pdf_url,
            pdf_path=pdf_path,
            pdf_resolved=pdf_resolved,
            download_status=download_status,
            download_error=download_error,
        )
    )


def run_harvest(
    fetcher: Fetcher,
    from_year: int,
    to_year: int,
    *,
    download: bool = False,
    delay: float = 0.0,
    page_size: int = config.DEFAULT_PAGE_SIZE,
    out_dir: str | Path = config.DEFAULT_OUT_DIR,
    pdf_dir: str | Path = config.DEFAULT_PDF_DIR,
    index_dir: str | Path = config.DEFAULT_INDEX_DIR,
    write_outputs: bool = True,
) -> HarvestResult:
    """Run the Phase A pipeline over an inclusive year range.

    With `download=False` (default) no PDF is fetched — a dry run that still
    produces a full index/manifest of what *would* be downloaded.
    """
    on_fetch = _delayer(delay)
    result = HarvestResult(dry_run=not download)

    entries = enumerate_years(
        fetcher, from_year, to_year, page_size=page_size, on_fetch=on_fetch
    )
    result.enumerated = len(entries)

    pdf_dir_path = Path(pdf_dir)
    for entry in entries:
        _process_entry(
            fetcher,
            entry,
            download=download,
            pdf_dir=pdf_dir_path,
            on_fetch=on_fetch,
            result=result,
        )

    # Collapse exact-duplicate feed entries (same eo_id AND same source_pdf_url)
    # before anything is written, so the index/manifest carry one row per real
    # order. Same-id-different-pdf conflicts are surfaced, not merged.
    (
        result.index_rows,
        result.manifest_rows,
        result.deduped,
        result.conflicts,
    ) = dedupe_rows(result.index_rows, result.manifest_rows)

    if write_outputs:
        index_paths = write_index(result.index_rows, index_dir)
        manifest_path = write_manifest(result.manifest_rows, out_dir)
        gaps_path = write_gaps(result.manifest_rows, out_dir)
        result.output_paths = {
            "index_json": index_paths["json"],
            "index_csv": index_paths["csv"],
            "manifest": manifest_path,
            "gaps": gaps_path,
        }

    logger.info(
        "harvest.done enumerated=%d resolved=%d downloaded=%d cached=%d "
        "errors=%d deduped=%d conflicts=%d dry_run=%s",
        result.enumerated,
        result.resolved,
        result.downloaded,
        result.cached,
        result.errors,
        result.deduped,
        len(result.conflicts),
        result.dry_run,
    )
    return result
