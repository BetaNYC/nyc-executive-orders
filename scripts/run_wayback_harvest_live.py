#!/usr/bin/env python3
"""SUPERVISED LIVE WAYBACK HARVEST (Phase B) — DO NOT RUN AUTOMATICALLY.

============================================================================
  !!  THIS SCRIPT MAKES REAL NETWORK CALLS TO THE INTERNET ARCHIVE  !!
============================================================================

The human-run entry point for the historical (1974 -> ~2022) executive-order
backfill: it enumerates the old `nyc.gov/html/records/pdf/executive_orders/`
PDFs on the Wayback Machine (removed from live nyc.gov by the 2026 redesign),
downloads each archived PDF go-slow, and merges the result into the current-era
corpus (preferring the live-nycgov rows on any eo_id collision).

Wayback access runs entirely through BetaNYC's `ny-gov-web-archiver` (EDGI
`wayback` engine). Its client is throttled BELOW Internet Archive's shared
~30 req/min budget and honors Retry-After / 429 backoff. This script never
touches the live archive from CI, an agent's test run, or any automation — the
offline suite mocks the archiver client.

Go-slow (mandatory — Internet Archive is a nonprofit on constrained infra):
  * The archiver's client paces memento downloads internally (~1 / 3s).
  * `--delay` adds a further sleep between downloads (default 2.5s), matching the
    Phase A live runner. Erring slower is correct for a one-time historical sweep.

Two equally-strong authorization gates (same posture as run_harvest_live.py):
  * --i-am-a-human-running-this-supervised : a human, interactively.
  * --operator-authorized : an agent, under explicit in-session authorization
    from operator noel (logs a truthful agent-executed provenance line; does NOT
    claim a human is supervising).
Refuses to run (exit 2) if NEITHER gate flag is present.

Setup (once):
    uv pip install -e .          # pulls ny-gov-web-archiver + wayback
    git lfs install              # PDFs land under pdfs/ (git-LFS)

Live dry-run first (enumerate + parse + merge, NO downloads):
    python scripts/run_wayback_harvest_live.py --from-year 1974 --to-year 2022 \
        --dry-run --i-am-a-human-running-this-supervised

Real download (go-slow) of the historical set:
    python scripts/run_wayback_harvest_live.py --from-year 1974 --to-year 2022 \
        --i-am-a-human-running-this-supervised

Exit codes: 0 clean; 1 completed with fetch errors; 2 no authorization gate.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the src/ package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders import config  # noqa: E402
from nyc_executive_orders.gather_wayback_eo import run_wayback_harvest  # noqa: E402

logger = logging.getLogger("nyc_executive_orders.run_wayback_harvest_live")


def _build_client():
    """Build the archiver's go-slow Wayback client (real network path only)."""
    from ny_gov_web_archiver.wayback_client import build_client

    return build_client()


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--from-year", type=int, default=config.HISTORICAL_FLOOR_YEAR)
    p.add_argument("--to-year", type=int, default=config.COVERAGE_FLOOR_YEAR)
    p.add_argument("--dry-run", action="store_true", help="Enumerate + parse + merge; no downloads.")
    p.add_argument(
        "--delay",
        type=float,
        default=config.DEFAULT_DELAY_SECONDS,
        help="Extra sleep (s) between downloads, ON TOP of the archiver's own "
        "throttle. Default conservative (2.5s). Go slow.",
    )
    p.add_argument("--limit", type=int, default=None, help="Max CDX rows (default: archiver default).")
    p.add_argument(
        "--i-am-a-human-running-this-supervised",
        action="store_true",
        help="Required acknowledgement that this makes LIVE Internet Archive calls.",
    )
    p.add_argument(
        "--operator-authorized",
        action="store_true",
        help=(
            "Non-human authorization path: an agent runs this under explicit "
            "in-session authorization from operator noel. Equally strong to the "
            "human-supervised flag; supply one or the other."
        ),
    )
    args = p.parse_args()

    # Two equally-strong authorization gates. Proceed if EITHER is present; refuse
    # if NEITHER.
    human = args.i_am_a_human_running_this_supervised
    operator = args.operator_authorized
    if not (human or operator):
        print(
            "REFUSING TO RUN: this script makes live Internet Archive calls.\n"
            "Re-run with --i-am-a-human-running-this-supervised (a human acting "
            "under BetaNYC's go-slow authorization) OR --operator-authorized (an "
            "agent running under explicit in-session operator authorization).",
            file=sys.stderr,
        )
        return 2

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Truthful provenance line: only emitted on the agent path (operator flag, no
    # human flag). It must never claim a human is running the harvest.
    if operator and not human:
        logger.info(
            "LIVE run under explicit operator authorization (operator=noel, "
            "in-session); agent-executed — not a human-supervised run."
        )

    download = not args.dry_run
    print(
        f"LIVE {'DOWNLOAD' if download else 'DRY-RUN'} Wayback harvest (Phase B): "
        f"{args.from_year}-{args.to_year} delay={args.delay}s (+ archiver throttle)\n"
    )

    client = _build_client()
    with client:
        result = run_wayback_harvest(
            client,
            from_year=args.from_year,
            to_year=args.to_year,
            download=download,
            delay=args.delay,
            limit=args.limit,
        )

    print(
        f"\nDone: enumerated={result.enumerated} unique_urls={result.unique_urls} "
        f"wayback_rows={result.wayback_rows} kept={result.wayback_kept} "
        f"dropped_dup={len(result.dropped_wayback_ids)} flagged={len(result.flagged)} "
        f"downloaded={result.downloaded} cached={result.cached} errors={result.errors}"
    )
    if result.conflicts:
        print(f"  CONFLICTS (same eo_id, different pdf) needing review: {len(result.conflicts)}")
    for label, path in result.output_paths.items():
        print(f"  {label}: {path}")

    # Exit-code contract (parity with run_harvest_live.py): 0 clean; 1 completed
    # with fetch errors; 2 missing authorization gate (handled above).
    if result.errors > 0:
        print(f"\nCompleted with {result.errors} error(s) — exiting non-zero.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
