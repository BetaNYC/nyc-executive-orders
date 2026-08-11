#!/usr/bin/env python3
"""Phase E stage 2 — build the pre-1974 corpus from committed volume OCR.

Reads `sources/gpp/volumes/ocr/<volume-stem>/page_*.json`, segments each volume
into per-instrument documents, and emits:

    corpus/YYYY/<eo_id>.md         the locked YAML frontmatter + full text
    corpus/eo_pre1974.json         bulk records (NOT eo.json — see build_pre1974)
    corpus/manifest_pre1974.csv    per-record parse ledger
    corpus/pre1974_provenance.json the sidecar (page spans, QA flags, lineage)
    pre1974_report.md              found-vs-expected, per volume

Offline, no model, no network, no gate — the expensive OCR already happened in
stage 1 (`scripts/run_volume_ocr.py`) and its output is committed. Re-running is
cheap and idempotent, which is the point: the segmentation and metadata rules can
be iterated without re-reading 2,936 pages.

    python scripts/run_pre1974_build.py --dry-run
    python scripts/run_pre1974_build.py
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders.build_pre1974 import (  # noqa: E402
    build_pre1974,
    load_volumes,
    render_report,
)
from nyc_executive_orders.vlm_ocr import DEFAULT_DPI, DEFAULT_MODEL  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VOLUMES_JSON = REPO_ROOT / "sources" / "gpp" / "volumes.json"
DEFAULT_OCR_ROOT = REPO_ROOT / "sources" / "gpp" / "volumes" / "ocr"
DEFAULT_CORPUS_DIR = REPO_ROOT / "corpus"
DEFAULT_REPORT = REPO_ROOT / "pre1974_report.md"


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--dry-run", action="store_true",
                   help="Build into a temp dir and report; write nothing under corpus/.")
    p.add_argument("--volume", default=None,
                   help="Substring of a volume filename, to build just that one.")
    p.add_argument("--volumes-json", default=str(DEFAULT_VOLUMES_JSON))
    p.add_argument("--ocr-root", default=str(DEFAULT_OCR_ROOT))
    p.add_argument("--corpus-dir", default=str(DEFAULT_CORPUS_DIR))
    p.add_argument("--report-path", default=str(DEFAULT_REPORT))
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    volumes = load_volumes(args.volumes_json)
    if args.volume:
        volumes = [v for v in volumes if args.volume.lower() in v.filename.lower()]
        if not volumes:
            print(f"error: no volume matches {args.volume!r}", file=sys.stderr)
            return 2

    # The model/DPI that produced the committed page JSON, recorded on every
    # record so a reader can tell what read it — and so a later re-OCR under a
    # different quantization is detectable rather than silent.
    ocr_meta = {"model": DEFAULT_MODEL, "dpi": DEFAULT_DPI, "temperature": 0.0}

    with tempfile.TemporaryDirectory(prefix="pre1974-dry-") as td:
        corpus_dir = Path(td) / "corpus" if args.dry_run else Path(args.corpus_dir)
        result = build_pre1974(
            volumes,
            repo_root=REPO_ROOT,
            corpus_dir=corpus_dir,
            ocr_root=Path(args.ocr_root),
            ocr_meta=ocr_meta,
        )
        report = render_report(result)
        if not args.dry_run:
            Path(args.report_path).write_text(report + "\n", encoding="utf-8")

    print(report)
    print("\n" + ("DRY-RUN — nothing written" if args.dry_run else "WROTE:"))
    if not args.dry_run:
        for label, path in result.output_paths.items():
            print(f"  {label}: {path}")
        print(f"  report: {args.report_path}")

    ocred = [v for v in result.volumes if v.get("status") == "ok"]
    if not ocred:
        print("\nNo volume has OCR on disk yet — run scripts/run_volume_ocr.py first.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
