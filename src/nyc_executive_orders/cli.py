"""Command-line entry point.

    python -m nyc_executive_orders harvest --from-year 2022 --to-year 2026 [--dry-run]
    python -m nyc_executive_orders harvest --from-year 2022 --to-year 2026 --download

Dry-run (enumerate + resolve PDF URLs, NO download) is the DEFAULT. Downloading
requires the explicit `--download` flag.

WARNING: with a real fetcher this makes LIVE calls to nyc.gov. For a large,
supervised go-slow download, prefer scripts/run_harvest_live.py (which gates on
an explicit human acknowledgement). This CLI builds the default fetcher
(requests -> Playwright WAF fallback) only when actually invoked.
"""

from __future__ import annotations

import argparse
import logging
from typing import Sequence

from . import config
from .fetch import build_fetcher
from .harvest import run_harvest


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nyc_executive_orders",
        description="NYC executive orders — Phase A harvester (current-era, live nyc.gov).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    h = sub.add_parser("harvest", help="Enumerate + resolve PDF URLs (+ optionally download).")
    h.add_argument("--from-year", type=int, required=True, help="Earliest signing year (inclusive).")
    h.add_argument("--to-year", type=int, required=True, help="Latest signing year (inclusive).")
    dl = h.add_mutually_exclusive_group()
    dl.add_argument(
        "--download",
        action="store_true",
        help="Actually download the PDFs. Off by default (dry-run).",
    )
    dl.add_argument(
        "--dry-run",
        action="store_true",
        help="Enumerate + resolve PDF URLs only; never download (the default).",
    )
    h.add_argument(
        "--delay",
        type=float,
        default=config.DEFAULT_DELAY_SECONDS,
        help=f"Go-slow delay (s) between live calls (default {config.DEFAULT_DELAY_SECONDS}).",
    )
    h.add_argument("--page-size", type=int, default=config.DEFAULT_PAGE_SIZE)
    h.add_argument("--backend", default="default", choices=["default", "requests", "playwright"])
    h.add_argument("--out-dir", default=str(config.DEFAULT_OUT_DIR))
    h.add_argument("--pdf-dir", default=str(config.DEFAULT_PDF_DIR))
    h.add_argument("--index-dir", default=str(config.DEFAULT_INDEX_DIR))
    h.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    download = bool(args.download)  # dry-run is the default; --download opts in

    fetcher = build_fetcher(args.backend)
    result = run_harvest(
        fetcher,
        args.from_year,
        args.to_year,
        download=download,
        delay=args.delay,
        page_size=args.page_size,
        out_dir=args.out_dir,
        pdf_dir=args.pdf_dir,
        index_dir=args.index_dir,
    )

    mode = "DOWNLOAD" if download else "DRY-RUN"
    print(
        f"{mode} harvest complete: enumerated={result.enumerated} "
        f"pdf_resolved={result.resolved} downloaded={result.downloaded} "
        f"cached={result.cached} errors={result.errors}"
    )
    for label, path in result.output_paths.items():
        print(f"  {label}: {path}")

    # Exit non-zero on a completed-but-errored run so `&&` chaining and CI gate on
    # "finished clean"; 0 only when there were zero errors.
    return 1 if result.errors else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
