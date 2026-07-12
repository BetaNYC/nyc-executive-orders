"""Download an EO's source PDF into pdfs/YYYY/ (git-LFS-tracked), idempotently.

Idempotency (engineering-standards §6): the destination filename is derived from
the stable `eo_id`, and an existing file is treated as already-downloaded and
skipped. Re-running a harvest never re-fetches or duplicates a PDF.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from . import config
from .fetch import Fetcher

logger = logging.getLogger("nyc_executive_orders.download")


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a single PDF download attempt."""

    eo_id: str
    pdf_url: str
    pdf_path: str | None  # repo-relative path, or None on failure
    status: str  # "downloaded" | "cached" | "error"
    error: str | None = None


def pdf_dest(eo_id: str, year: int, pdf_dir: Path) -> Path:
    """Absolute destination path for an EO PDF: <pdf_dir>/YYYY/<eo_id>.pdf."""
    return Path(pdf_dir) / str(year) / f"{eo_id}.pdf"


def _rel_to_repo(path: Path) -> str:
    """Repo-relative path string for the index/manifest (falls back to name)."""
    try:
        return str(path.relative_to(config.DEFAULT_OUT_DIR))
    except ValueError:
        return str(path)


def download_pdf(
    fetcher: Fetcher,
    eo_id: str,
    year: int,
    pdf_url: str,
    *,
    pdf_dir: Path = config.DEFAULT_PDF_DIR,
    on_fetch=None,
) -> DownloadResult:
    """Download one PDF (skip if already present). `on_fetch` applies go-slow.

    A network call happens ONLY when the file is not already cached; `on_fetch`
    is invoked only in that case, so cached re-runs are instantaneous and silent.
    """
    dest = pdf_dest(eo_id, year, pdf_dir)
    if dest.exists():
        logger.info("download.cached eo_id=%s path=%s", eo_id, dest)
        return DownloadResult(eo_id, pdf_url, _rel_to_repo(dest), "cached")

    try:
        logger.info("download.start eo_id=%s url=%s", eo_id, pdf_url)
        content = fetcher.get_bytes(pdf_url)
        if on_fetch:
            on_fetch()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        logger.info("download.done eo_id=%s path=%s bytes=%d", eo_id, dest, len(content))
        return DownloadResult(eo_id, pdf_url, _rel_to_repo(dest), "downloaded")
    except Exception as exc:  # noqa: BLE001 - record every failure, never abort
        logger.warning("download.error eo_id=%s url=%s error=%s", eo_id, pdf_url, exc)
        return DownloadResult(
            eo_id, pdf_url, None, "error", error=f"{type(exc).__name__}: {exc}"
        )
