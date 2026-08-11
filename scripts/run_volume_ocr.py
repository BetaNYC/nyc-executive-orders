#!/usr/bin/env python3
"""Phase E stage 1 — OCR the pre-1974 bound volumes with the local dots.ocr model.

Drives `nyc_executive_orders.vlm_ocr` over the 14 volumes listed in
`sources/gpp/volumes.json`, one volume at a time, pointing each run's
`--json-dir` at `sources/gpp/volumes/ocr/<volume-stem>/` — the committed,
diffable page records that stage 2 (`scripts/run_pre1974_build.py`) reads. The
rest of the run (rendered PNGs, bbox overlays, memory_profile.json) is scratch
and stays under `vlm-ocr-runs/` (gitignored), where `qa-ui/` reads it for the
page images.

Page records are WRITTEN THERE, not written to the run directory and copied
afterwards. The copy is what this script used to do, and it meant every page
existed twice: the resume planner had to treat a page as done if either
directory held it, publishing overwrote without pruning (so a stale record could
outlive the run that made it, and be re-published over a good re-run), and the
viewer needed a banner to explain which copy you were looking at. One location
removes all three. The trade is that a killed run leaves partial output in a
committed directory instead of a hidden one — which git shows you as an unstaged
diff, and `run_full_vlm_pipeline.sh --clean --volume X` removes.

NO AUTHORIZATION GATE. The human/operator flags on the other runners exist
because those scripts make live, rate-limited calls to nyc.gov, the Internet
Archive, or DORIS. This one is pure local compute over files already on disk: no
network (beyond the one-time Hugging Face weight download), no third-party
service, nothing to be a bad citizen toward.

It IS slow — roughly 2 minutes per non-blank page, ~2 hours for a 126-page
volume, ~45-50 hours for all 14. So:

  * run one volume at a time (`--volume` takes a filename substring),
  * `--start-page` resumes an interrupted volume in place; page numbering is
    absolute, so the run directory extends rather than renumbering,
  * `--classify-blank-only` renders and scores pages WITHOUT loading the model
    (minutes, not hours). Do this first on any volume you have not OCR'd before:
    the blank/bleed-through thresholds were calibrated on one volume's scans, and
    a false skip silently drops a real order.

Calibrate, then OCR:
    python scripts/run_volume_ocr.py --volume Wagner_Orders --classify-blank-only
    python scripts/run_volume_ocr.py --volume 1962-01-23

List what is done and what is left:
    python scripts/run_volume_ocr.py --status
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders.build_pre1974 import load_volumes  # noqa: E402
from nyc_executive_orders.vlm_ocr import DEFAULT_MODEL, ROTATE_CHOICES  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VOLUMES_JSON = REPO_ROOT / "sources" / "gpp" / "volumes.json"
DEFAULT_OCR_ROOT = REPO_ROOT / "sources" / "gpp" / "volumes" / "ocr"
DEFAULT_RUNS_ROOT = REPO_ROOT / "vlm-ocr-runs"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--volume", default=None,
                   help="Substring of the volume filename to run (default: all).")
    p.add_argument("--status", action="store_true",
                   help="Report per-volume OCR progress and exit.")
    p.add_argument("--classify-blank-only", action="store_true",
                   help="Render + score blank pages without loading the model.")
    p.add_argument("--start-page", type=int, default=1,
                   help="1-based page to resume an interrupted volume from.")
    p.add_argument("--pages", type=int, default=None,
                   help="Cap pages processed (for a quick sample).")
    p.add_argument("--device", choices=["auto", "mlx", "cuda"], default=None,
                   help="Forwarded to vlm_ocr --device (default: let vlm_ocr auto-detect).")
    p.add_argument("--model", default=None,
                   help=f"Forwarded to vlm_ocr --model (default: vlm_ocr's own per-device default, "
                        f"{DEFAULT_MODEL} on mlx).")
    p.add_argument("--quantization", choices=["none", "8bit", "4bit"], default=None,
                   help="Forwarded to vlm_ocr --quantization (cuda backend only).")
    p.add_argument("--attn-implementation", default=None,
                   help="Forwarded to vlm_ocr --attn-implementation (cuda backend only).")
    p.add_argument("--dpi", type=int, default=None)
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--rotate", choices=ROTATE_CHOICES, default=None,
                   help="Forwarded to vlm_ocr --rotate (default: vlm_ocr's own 'auto', which "
                        "turns landscape-shaped pages 90 degrees clockwise so their text is "
                        "upright). Two volumes were scanned sideways; see detect_page_rotation.")
    p.add_argument("--volumes-json", default=str(DEFAULT_VOLUMES_JSON))
    p.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT),
                   help="Where run scratch (PNGs, overlays, memory profile) is written.")
    p.add_argument("--ocr-root", default=str(DEFAULT_OCR_ROOT),
                   help="Where per-page JSON is written, committed, and read from by "
                        "stage 2 -- passed to vlm_ocr as --json-dir.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the commands that would run, then exit.")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    volumes = load_volumes(args.volumes_json)
    if args.volume:
        volumes = [v for v in volumes if args.volume.lower() in v.filename.lower()]
        if not volumes:
            print(f"error: no volume matches {args.volume!r}", file=sys.stderr)
            return 2

    runs_root, ocr_root = Path(args.runs_root), Path(args.ocr_root)

    if args.status:
        print(f"{'volume':64s} {'years':>9s}  ocr'd  classified")
        for v in load_volumes(args.volumes_json):
            vol_dir = ocr_root / v.stem
            done = len(list(vol_dir.glob("page_*.json"))) if vol_dir.is_dir() else 0
            mark = "—" if not done else str(done)
            classified = "yes" if (vol_dir / "classify_report.json").is_file() else "—"
            print(f"{v.filename:64s} {v.min_year}-{v.max_year:>4d}  {mark:>5s}  {classified}")
        return 0

    for volume in volumes:
        pdf = REPO_ROOT / volume.pdf_relpath
        if not pdf.exists():
            print(f"SKIP {volume.filename}: PDF not on disk", file=sys.stderr)
            continue
        out_dir = runs_root / volume.stem
        json_dir = ocr_root / volume.stem

        cmd = [
            sys.executable, "-m", "nyc_executive_orders.vlm_ocr", str(pdf),
            "--output-dir", str(out_dir), "--json-dir", str(json_dir),
            "--start-page", str(args.start_page), "--profile-memory",
        ]
        if args.device is not None:
            cmd += ["--device", args.device]
        if args.model is not None:
            cmd += ["--model", args.model]
        if args.quantization is not None:
            cmd += ["--quantization", args.quantization]
        if args.attn_implementation is not None:
            cmd += ["--attn-implementation", args.attn_implementation]
        if args.pages is not None:
            cmd += ["--pages", str(args.pages)]
        if args.dpi is not None:
            cmd += ["--dpi", str(args.dpi)]
        if args.max_tokens is not None:
            cmd += ["--max-tokens", str(args.max_tokens)]
        if args.rotate is not None:
            cmd += ["--rotate", args.rotate]
        if args.classify_blank_only:
            cmd.append("--classify-blank-only")

        print(f"\n=== {volume.filename} ({volume.min_year}-{volume.max_year}) ===")
        print("  " + " ".join(cmd))
        if args.dry_run:
            continue

        env_src = str(REPO_ROOT / "src")
        import os
        env = dict(os.environ)
        env["PYTHONPATH"] = env_src + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(cmd, env=env, check=False)  # returncode handled below
        if proc.returncode != 0:
            print(f"FAILED {volume.filename} (exit {proc.returncode})", file=sys.stderr)
            return proc.returncode

        # Nothing to publish: --json-dir already wrote the records where stage 2,
        # the picture clipper and the viewer read them. classify_report.json lands
        # there too, so a calibration pass is auditable before -- or without --
        # the expensive full run.
        n = len(list(json_dir.glob("page_*.json")))
        print(f"  {n} page record(s) in {json_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
