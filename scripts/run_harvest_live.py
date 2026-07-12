#!/usr/bin/env python3
"""SUPERVISED LIVE HARVEST — DO NOT RUN AUTOMATICALLY (not for CI or agents).

============================================================================
  !!  THIS SCRIPT MAKES REAL NETWORK CALLS TO www.nyc.gov  !!
============================================================================

This is the human-run entry point that actually downloads every current-era EO
PDF, go-slow. It is NEVER run by CI, by an agent, by a test, or by any
automation. The offline test suite exercises the pipeline with a mocked fetcher
(no network); this script is the one place the project touches the live site.

Conditions (same posture as ny-gov-web-archiver's smoke_test_live.py):
  * Run BY A HUMAN, interactively, under BetaNYC's go-slow authorization for
    nyc.gov access.
  * Refuses to run without --i-am-a-human-running-this-supervised.
  * Downloads on a conservative delay (default 2.5s between live calls) and is
    idempotent — already-downloaded PDFs are skipped, so it is safe to resume.

Setup (once):
    pip install 'nyc-executive-orders[live]'
    python -m playwright install chromium   # only needed if the WAF forces the
                                            # Playwright fallback

Run (human, supervised) — download everything 2022 -> present:
    python scripts/run_harvest_live.py --from-year 2022 --to-year 2026 \
        --i-am-a-human-running-this-supervised

Do a live dry-run first (enumerate + resolve PDF URLs, NO downloads):
    python scripts/run_harvest_live.py --from-year 2022 --to-year 2026 --dry-run \
        --i-am-a-human-running-this-supervised
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the src/ package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders import config  # noqa: E402
from nyc_executive_orders.fetch import build_fetcher  # noqa: E402
from nyc_executive_orders.harvest import run_harvest  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from-year", type=int, required=True)
    p.add_argument("--to-year", type=int, required=True)
    p.add_argument("--dry-run", action="store_true", help="Enumerate + resolve only; no downloads.")
    p.add_argument("--delay", type=float, default=config.DEFAULT_DELAY_SECONDS)
    p.add_argument("--backend", default="default", choices=["default", "requests", "playwright"])
    p.add_argument(
        "--i-am-a-human-running-this-supervised",
        action="store_true",
        help="Required acknowledgement that this makes LIVE nyc.gov calls.",
    )
    args = p.parse_args()

    if not args.i_am_a_human_running_this_supervised:
        print(
            "REFUSING TO RUN: this script makes live nyc.gov calls.\n"
            "Re-run with --i-am-a-human-running-this-supervised only if you are a "
            "human acting under BetaNYC's go-slow authorization.",
            file=sys.stderr,
        )
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    download = not args.dry_run
    print(
        f"LIVE {'DOWNLOAD' if download else 'DRY-RUN'} harvest: "
        f"{args.from_year}-{args.to_year} delay={args.delay}s backend={args.backend}\n"
    )

    fetcher = build_fetcher(args.backend)
    result = run_harvest(
        fetcher,
        args.from_year,
        args.to_year,
        download=download,
        delay=args.delay,
    )

    print(
        f"\nDone: enumerated={result.enumerated} pdf_resolved={result.resolved} "
        f"downloaded={result.downloaded} cached={result.cached} errors={result.errors}"
    )
    for label, path in result.output_paths.items():
        print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
