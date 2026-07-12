#!/usr/bin/env python3
"""SUPERVISED DRY-RUN PIPELINE — chains gated dry-runs, then HALTS before any download.

============================================================================
  !!  THIS WRAPPER NEVER DOWNLOADS. IT CHAINS DRY-RUNS AND STOPS.  !!
============================================================================

A human-gated wrapper around ``scripts/run_harvest_live.py``. It runs an ordered
sequence of DRY-RUN harvest steps (enumerate + resolve PDF URLs, ZERO downloads),
advancing to the next step ONLY if the previous one finished clean (exit 0 AND
errors == 0). If any step fails it STOPS immediately and reports which one.

After every dry-run step passes it prints a consolidated inventory summary and
then HALTS, printing the exact manual command a human must run to actually
download. It does NOT download anything itself — auto-pulling live PDFs is out of
scope and unsafe. Downloading stays a separate, deliberate, human-run action.

Like the underlying live runner, this refuses to run without
``--i-am-a-human-running-this-supervised`` (exit 2). The flag is passed through to
each dry-run step. Note: even a dry-run makes LIVE enumeration calls to nyc.gov
when actually executed, which is why the human gate is here too. The offline test
suite drives this wrapper with the step invocation mocked — no subprocess, no
network.

Run (human, supervised) — chain the default dry-run sequence, then halt:
    python scripts/run_pipeline.py --i-am-a-human-running-this-supervised
"""

from __future__ import annotations

import argparse
import logging
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("nyc_executive_orders.pipeline")

HUMAN_FLAG = "--i-am-a-human-running-this-supervised"

# The gated live runner this wrapper drives (a sibling script). Each step is a
# dry-run invocation of it as a subprocess, reusing its exit-code contract
# (0 clean / 1 completed-with-errors / 2 missing human flag).
HARVEST_SCRIPT = Path(__file__).resolve().parent / "run_harvest_live.py"


@dataclass(frozen=True)
class PipelineStep:
    """One ordered dry-run step over an inclusive year range."""

    label: str
    from_year: int
    to_year: int


# --- Default sequence -------------------------------------------------------
# Ordered list of dry-run steps. Edit here to change what the pipeline validates.
# Runs cheapest/most-diagnostic first, then the full inventory.
DEFAULT_STEPS: list[PipelineStep] = [
    PipelineStep(
        label="2024 (Adams-era integer scheme, high-volume validation)",
        from_year=2024,
        to_year=2024,
    ),
    PipelineStep(
        label="2022-2026 (full current-era inventory)",
        from_year=2022,
        to_year=2026,
    ),
]


@dataclass
class StepResult:
    """Outcome of one dry-run step: its exit code plus parsed inventory counts."""

    step: PipelineStep
    exit_code: int
    enumerated: int = 0
    resolved: int = 0
    errors: int = 0
    stdout: str = ""
    stderr: str = ""

    @property
    def clean(self) -> bool:
        """A step advances the pipeline only if it exited 0 AND hit zero errors."""
        return self.exit_code == 0 and self.errors == 0


_SUMMARY_RE = re.compile(
    r"enumerated=(?P<enumerated>\d+).*?"
    r"pdf_resolved=(?P<resolved>\d+).*?"
    r"errors=(?P<errors>\d+)",
    re.DOTALL,
)


def _parse_summary(stdout: str) -> tuple[int, int, int]:
    """Extract (enumerated, resolved, errors) from a run_harvest_live summary line.

    The live runner prints: ``Done: enumerated=N pdf_resolved=N ... errors=N``.
    Returns zeros if the line is absent (e.g. the step crashed before printing).
    """
    match = _SUMMARY_RE.search(stdout or "")
    if not match:
        return (0, 0, 0)
    return (
        int(match["enumerated"]),
        int(match["resolved"]),
        int(match["errors"]),
    )


def run_step(
    step: PipelineStep,
    *,
    python_exe: str = sys.executable,
    script_path: Path = HARVEST_SCRIPT,
) -> StepResult:
    """Invoke ``run_harvest_live.py`` for one DRY-RUN step as a subprocess.

    Always passes ``--dry-run`` and the human flag through. Captures stdout so the
    inventory counts can be parsed and surfaced in the consolidated summary.
    """
    cmd = [
        python_exe,
        str(script_path),
        "--from-year",
        str(step.from_year),
        "--to-year",
        str(step.to_year),
        "--dry-run",
        HUMAN_FLAG,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    enumerated, resolved, errors = _parse_summary(proc.stdout)
    return StepResult(
        step=step,
        exit_code=proc.returncode,
        enumerated=enumerated,
        resolved=resolved,
        errors=errors,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _download_command(steps: list[PipelineStep]) -> str:
    """The exact manual command a human runs to actually download.

    Spans the widest range across all steps, with NO --dry-run.
    """
    dl_from = min(s.from_year for s in steps)
    dl_to = max(s.to_year for s in steps)
    return (
        f"uv run python scripts/run_harvest_live.py "
        f"--from-year {dl_from} --to-year {dl_to} {HUMAN_FLAG}"
    )


def run_pipeline(steps: list[PipelineStep], *, runner=None) -> tuple[int, list[StepResult]]:
    """Run the ordered dry-run sequence, halting before any download.

    Advances to step N+1 ONLY if step N finished clean. On a failing step, STOPS
    immediately (later steps are not run) and returns a non-zero code. After all
    steps pass, prints a consolidated inventory + the manual download command and
    returns 0 WITHOUT downloading.

    `runner` is the per-step invocation (defaults to the module-level `run_step`);
    tests inject a fake to return controllable results offline.
    """
    runner = runner or run_step
    results: list[StepResult] = []
    total = len(steps)

    for i, step in enumerate(steps, 1):
        logger.info("[%s] START step %d/%d: %s", _stamp(), i, total, step.label)
        res = runner(step)
        results.append(res)
        logger.info(
            "[%s] FINISH step %d/%d: exit=%d enumerated=%d resolved=%d errors=%d",
            _stamp(),
            i,
            total,
            res.exit_code,
            res.enumerated,
            res.resolved,
            res.errors,
        )

        if not res.clean:
            print(
                f"\nPIPELINE STOPPED at step {i}/{total}: {step.label}\n"
                f"  exit_code={res.exit_code} errors={res.errors} "
                f"enumerated={res.enumerated} resolved={res.resolved}\n"
                f"  Later steps were NOT run. Fix the failure above and re-run.",
                file=sys.stderr,
            )
            if res.stderr.strip():
                print(f"  step stderr tail:\n{res.stderr.strip()}", file=sys.stderr)
            return (res.exit_code if res.exit_code != 0 else 1, results)

    _print_inventory_and_halt(steps, results)
    return (0, results)


def _print_inventory_and_halt(steps: list[PipelineStep], results: list[StepResult]) -> None:
    """Consolidated inventory summary + the halt-before-download message."""
    lines = ["", "=== Dry-run inventory (all steps clean) ==="]
    for i, res in enumerate(results, 1):
        lines.append(
            f"  Step {i} — {res.step.label}: "
            f"enumerated={res.enumerated} resolved={res.resolved} errors={res.errors}"
        )
    lines += [
        "",
        "Written outputs (default paths, relative to repo root):",
        "  index/       — per-EO index (eo_index.json + eo_index.csv)",
        "  manifest.csv — full manifest of every EO that WOULD be downloaded",
        "  gaps.md      — flagged gaps and unresolved PDF URLs",
        "",
        "Dry-runs clean. Review the inventory above, then run the download yourself:",
        f"  {_download_command(steps)}",
        "",
        "(This wrapper never downloads — that step is deliberately a separate, "
        "human-run command.)",
    ]
    print("\n".join(lines))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_pipeline",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        HUMAN_FLAG,
        dest="i_am_a_human_running_this_supervised",
        action="store_true",
        help="Required acknowledgement that steps make LIVE nyc.gov calls.",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not args.i_am_a_human_running_this_supervised:
        print(
            "REFUSING TO RUN: this wrapper chains dry-run harvests that make live "
            "nyc.gov calls.\n"
            f"Re-run with {HUMAN_FLAG} only if you are a human acting under "
            "BetaNYC's go-slow authorization.",
            file=sys.stderr,
        )
        return 2

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(name)s %(levelname)s %(message)s",
    )

    print(f"[{_stamp()}] Starting dry-run pipeline: {len(DEFAULT_STEPS)} step(s).")
    rc, _results = run_pipeline(DEFAULT_STEPS)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
