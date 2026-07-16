#!/usr/bin/env python3
"""SUPERVISED LIVE de BLASIO EO BACKFILL (Phase B.4) — DO NOT RUN AUTOMATICALLY.

============================================================================
  !!  THIS SCRIPT MAKES REAL NETWORK CALLS TO THE INTERNET ARCHIVE  !!
============================================================================

Backfills the executive orders signed 2014-2021 (all of Mayor de Blasio's) that
fell into a harvest gap: the live source (Phase A, articlesearch.json) reaches
back only to ~2022, and the historical Wayback path (`/html/records/...`) stops
at 2013, so NEITHER side ever queried 2014-2021. A 2026-07-16 CDX discovery found
these orders on the Wayback Machine under the pre-redesign `/assets/home/...`
path (year in the directory, `{eo|eeo}[-_]{n}.pdf` filename). A live
articlesearch.json probe of those years returned zero — Wayback is the only
source.

Wayback access runs entirely through BetaNYC's `ny-gov-web-archiver` (EDGI
`wayback` engine), throttled BELOW Internet Archive's shared ~30 req/min budget,
honoring Retry-After / 429. The offline suite mocks the client; this script is
the only live entry point. Reuses the Phase B merge machinery (prefer-live on any
eo_id collision — de Blasio ids are all net-new, so nothing is dropped).

Go-slow (mandatory — Internet Archive is a nonprofit on constrained infra):
  * the archiver client paces memento downloads internally (~1 / 3s);
  * `--delay` adds a further sleep between downloads (default 2.5s).

Two equally-strong authorization gates (same posture as run_wayback_harvest_live):
  * --i-am-a-human-running-this-supervised : a human, interactively.
  * --operator-authorized : an agent, under explicit in-session authorization
    from operator noel (logs a truthful agent-executed provenance line).
Refuses to run (exit 2) if NEITHER gate flag is present.

Live dry-run first (enumerate + parse + merge, NO downloads):
    python scripts/run_deblasio_harvest_live.py --dry-run \
        --i-am-a-human-running-this-supervised

Real download (go-slow) of the de Blasio set:
    python scripts/run_deblasio_harvest_live.py \
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
from nyc_executive_orders.gather_wayback_eo import run_deblasio_harvest  # noqa: E402

logger = logging.getLogger("nyc_executive_orders.run_deblasio_harvest_live")


def _build_client():
    """Build the archiver's go-slow Wayback client (real network path only)."""
    from ny_gov_web_archiver.wayback_client import build_client

    return build_client()


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--year-lo", type=int, default=config.DEBLASIO_FLOOR_YEAR)
    p.add_argument("--year-hi", type=int, default=config.DEBLASIO_CEIL_YEAR)
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

    if operator and not human:
        logger.info(
            "LIVE run under explicit operator authorization (operator=noel, "
            "in-session); agent-executed — not a human-supervised run."
        )

    download = not args.dry_run
    print(
        f"LIVE {'DOWNLOAD' if download else 'DRY-RUN'} de Blasio backfill (Phase B.4): "
        f"{args.year_lo}-{args.year_hi} delay={args.delay}s (+ archiver throttle)\n"
    )

    client = _build_client()
    with client:
        result = run_deblasio_harvest(
            client,
            year_lo=args.year_lo,
            year_hi=args.year_hi,
            download=download,
            delay=args.delay,
            limit=args.limit,
        )

    print(
        f"\nDone: enumerated={result.enumerated} unique_ids={result.unique_urls} "
        f"wayback_rows={result.wayback_rows} kept={result.wayback_kept} "
        f"dropped_dup={len(result.dropped_wayback_ids)} flagged={len(result.flagged)} "
        f"downloaded={result.downloaded} cached={result.cached} errors={result.errors}"
    )
    if result.conflicts:
        print(f"  CONFLICTS (same eo_id, different pdf) needing review: {len(result.conflicts)}")
    for label, path in result.output_paths.items():
        print(f"  {label}: {path}")

    if result.errors > 0:
        print(f"\nCompleted with {result.errors} error(s) — exiting non-zero.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
