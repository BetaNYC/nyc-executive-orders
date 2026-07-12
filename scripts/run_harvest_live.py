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
  * Run under BetaNYC's go-slow authorization for nyc.gov access, via ONE of two
    equally-strong gates:
      - --i-am-a-human-running-this-supervised : a human, interactively.
      - --operator-authorized : an agent, under explicit in-session authorization
        from operator noel (logs a truthful agent-executed provenance line; does
        NOT claim a human is supervising).
  * Refuses to run (exit 2) if NEITHER gate flag is present.
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

logger = logging.getLogger("nyc_executive_orders.run_harvest_live")


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
    # if NEITHER. The operator-authorized path exists so an agent can run the
    # download under explicit operator authorization WITHOUT falsely claiming a
    # human is supervising it.
    human = args.i_am_a_human_running_this_supervised
    operator = args.operator_authorized
    if not (human or operator):
        print(
            "REFUSING TO RUN: this script makes live nyc.gov calls.\n"
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

    # Truthful provenance line: only emitted on the agent path (operator flag,
    # no human flag). It must never claim a human is running the harvest.
    if operator and not human:
        logger.info(
            "LIVE run under explicit operator authorization (operator=noel, "
            "in-session); agent-executed — not a human-supervised run."
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

    # Exit-code contract (so `&&` chaining and the pipeline wrapper can gate on a
    # clean finish): 0 only on a fully clean run; 1 if the run completed but hit
    # any errors; 2 (above) if the human-gate flag was missing.
    if result.errors > 0:
        print(
            f"\nCompleted with {result.errors} error(s) — exiting non-zero.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
