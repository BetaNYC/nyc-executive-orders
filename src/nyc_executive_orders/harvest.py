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
    dry_run: bool = True
    output_paths: dict = field(default_factory=dict)


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
        "harvest.done enumerated=%d resolved=%d downloaded=%d cached=%d errors=%d dry_run=%s",
        result.enumerated,
        result.resolved,
        result.downloaded,
        result.cached,
        result.errors,
        result.dry_run,
    )
    return result
