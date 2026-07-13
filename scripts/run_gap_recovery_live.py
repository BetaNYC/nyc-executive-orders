#!/usr/bin/env python3
"""SUPERVISED LIVE GAP RECOVERY (Phase B.2) — DO NOT RUN AUTOMATICALLY.

============================================================================
  !!  THIS SCRIPT MAKES REAL NETWORK CALLS TO THE INTERNET ARCHIVE  !!
============================================================================

The human-run entry point for recovering the current-era executive-order PDFs
that are recorded in the index but MISSING from disk — the orders whose live
nyc.gov PDF 404'd, sat on an internal host, or had no resolvable URL. For each
gap it looks up the public-equivalent URL on the Wayback Machine, downloads the
newest archived PDF go-slow, validates it is really a PDF, and stamps the row
`source: "wayback-gap"`.

Wayback access runs entirely through BetaNYC's `ny-gov-web-archiver` (EDGI
`wayback` engine). Its client is throttled BELOW Internet Archive's shared
~30 req/min budget and honors Retry-After / 429 backoff. This script never
touches the live archive from CI, an agent's test run, or any automation — the
offline suite mocks the archiver client.

Go-slow (mandatory — Internet Archive is a nonprofit on constrained infra):
  * The archiver's client paces memento downloads internally (~1 / 3s).
  * `--delay` adds a further sleep between downloads (default 2.5s), matching the
    Phase A / Phase B live runners.

Two equally-strong authorization gates (same posture as run_wayback_harvest_live.py):
  * --i-am-a-human-running-this-supervised : a human, interactively.
  * --operator-authorized : an agent, under explicit in-session authorization
    from operator noel (logs a truthful agent-executed provenance line; does NOT
    claim a human is supervising).
Refuses to run (exit 2) if NEITHER gate flag is present.

Setup (once):
    uv pip install -e .          # pulls ny-gov-web-archiver + wayback
    git lfs install              # PDFs land under pdfs/ (git-LFS)

Live dry-run first (find Wayback snapshots, NO downloads):
    python scripts/run_gap_recovery_live.py --dry-run \
        --i-am-a-human-running-this-supervised

Real recovery (go-slow) of every gap:
    python scripts/run_gap_recovery_live.py \
        --i-am-a-human-running-this-supervised

Exit codes: 0 clean; 1 completed with fetch/lookup errors; 2 no authorization gate.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Make the src/ package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders import config  # noqa: E402
from nyc_executive_orders.recover_gaps import run_gap_recovery  # noqa: E402

logger = logging.getLogger("nyc_executive_orders.run_gap_recovery_live")


def _build_client():
    """Build the archiver's go-slow Wayback client (real network path only)."""
    from ny_gov_web_archiver.wayback_client import build_client

    return build_client()


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--dry-run", action="store_true", help="Find snapshots; no downloads.")
    p.add_argument(
        "--delay",
        type=float,
        default=config.DEFAULT_DELAY_SECONDS,
        help="Extra sleep (s) between downloads, ON TOP of the archiver's own "
        "throttle. Default conservative (2.5s). Go slow.",
    )
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
    # human flag). It must never claim a human is running the recovery.
    if operator and not human:
        logger.info(
            "LIVE run under explicit operator authorization (operator=noel, "
            "in-session); agent-executed — not a human-supervised run."
        )

    download = not args.dry_run
    print(
        f"LIVE {'DOWNLOAD' if download else 'DRY-RUN'} gap recovery (Phase B.2): "
        f"delay={args.delay}s (+ archiver throttle)\n"
    )

    client = _build_client()
    with client:
        result = run_gap_recovery(
            client,
            download=download,
            delay=args.delay,
        )

    print(
        f"\nDone: gaps={result.gaps_total} recovered={result.recovered} "
        f"cached={result.cached} would_recover={result.would_recover} "
        f"unrecoverable={result.unrecoverable} errors={result.errors}"
    )
    for label, path in result.output_paths.items():
        print(f"  {label}: {path}")

    # Exit-code contract (parity with the other live runners): 0 clean; 1
    # completed with lookup/fetch errors; 2 missing authorization gate (above).
    if result.errors > 0:
        print(f"\nCompleted with {result.errors} error(s) — exiting non-zero.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
