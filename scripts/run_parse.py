#!/usr/bin/env python3
"""Parse the indexed EOs into the publishable corpus (Markdown + bulk eo.json).

Chains the parse pipeline over ``index/eo_index.json``:

    textlayer.classify -> (born-digital) extract | (scanned) ocr -> enrich -> emit

Two run modes:

  * ``--no-ocr`` — born-digital ONLY. Scanned orders are emitted as an
    ``ocr-skipped`` stub. Fast, no heavy deps, no gate — use this to iterate.
  * default (OCR on) — scanned orders are OCR'd LOCALLY (ocrmypdf/Tesseract). This
    is the expensive path, so it is GATED exactly like the live harvest runner:
    it refuses to run without ``--operator-authorized`` (agent, under explicit
    in-session operator authorization) or ``--i-am-a-human-running-this-supervised``
    (a human). The gate is NOT about network — OCR is fully local, no cloud, no
    fallback (engineering-standards §7) — it makes the long full-corpus OCR run a
    deliberate, authorized action, consistent with the project's runner posture.

Fast smoke-test slice (born-digital, one year, no gate needed). Point it at a
scratch --corpus-dir so the partial output doesn't trip the shrink guard against
the real corpus/:
    uv run python scripts/run_parse.py --no-ocr --year 2003 --corpus-dir /tmp/eo-smoke

Full authorized run incl. local OCR (agent path):
    uv run python scripts/run_parse.py --operator-authorized
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Make the src/ package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders import config  # noqa: E402
from nyc_executive_orders.build_corpus import CorpusShrinkError, build_corpus  # noqa: E402
from nyc_executive_orders.ocr import OcrConfig  # noqa: E402

logger = logging.getLogger("nyc_executive_orders.run_parse")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INDEX = REPO_ROOT / "index" / "eo_index.json"
DEFAULT_CORPUS_DIR = REPO_ROOT / "corpus"


def load_records(index_path: Path) -> list[dict]:
    return json.loads(index_path.read_text(encoding="utf-8"))


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--no-ocr",
        action="store_true",
        help="Born-digital only; scanned PDFs emit an ocr-skipped stub (no gate).",
    )
    p.add_argument("--year", type=int, default=None, help="Restrict to one signing year.")
    p.add_argument("--limit", type=int, default=None, help="Cap number of records.")
    p.add_argument("--index", default=str(DEFAULT_INDEX), help="Path to eo_index.json.")
    p.add_argument("--corpus-dir", default=str(DEFAULT_CORPUS_DIR))
    p.add_argument("--index-dir", default=str(config.DEFAULT_INDEX_DIR))
    p.add_argument(
        "--allow-shrink",
        action="store_true",
        help="Permit emitting fewer docs than the corpus already on disk "
        "(overrides the shrink guard; use only when the smaller build is "
        "intended, e.g. a scoped --year run into a scratch --corpus-dir).",
    )
    p.add_argument("--language", default="eng", help="Tesseract language (OCR path).")
    p.add_argument(
        "--i-am-a-human-running-this-supervised",
        action="store_true",
        help="Human authorization for the (long) local OCR run.",
    )
    p.add_argument(
        "--operator-authorized",
        action="store_true",
        help="Agent authorization for the local OCR run (explicit in-session operator go-ahead).",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    do_ocr = not args.no_ocr

    # Gate ONLY the OCR path. --no-ocr needs no authorization (born-digital, fast).
    if do_ocr:
        human = args.i_am_a_human_running_this_supervised
        operator = args.operator_authorized
        if not (human or operator):
            print(
                "REFUSING TO RUN: the default (OCR) path runs local OCR across the "
                "scanned corpus — a long, deliberate operation.\n"
                "Re-run with --operator-authorized (agent, explicit in-session "
                "operator authorization) OR --i-am-a-human-running-this-supervised "
                "(a human). To iterate WITHOUT OCR, pass --no-ocr (no gate).",
                file=sys.stderr,
            )
            return 2
        if operator and not human:
            logger.info(
                "Local-OCR corpus build under explicit operator authorization "
                "(operator=noel, in-session); agent-executed. LOCAL ONLY — no "
                "cloud OCR, no network (engineering-standards §7)."
            )

    records = load_records(Path(args.index))
    ocr_config = OcrConfig(language=args.language)

    print(
        f"PARSE {'(no-ocr, born-digital only)' if args.no_ocr else '(OCR on, local)'}: "
        f"records={len(records)} year={args.year} limit={args.limit}\n"
    )

    try:
        result = build_corpus(
            records,
            repo_root=REPO_ROOT,
            corpus_dir=Path(args.corpus_dir),
            index_dir=Path(args.index_dir),
            do_ocr=do_ocr,
            ocr_config=ocr_config,
            year=args.year,
            limit=args.limit,
            allow_shrink=args.allow_shrink,
        )
    except CorpusShrinkError as exc:
        print(f"REFUSING TO RUN: {exc}", file=sys.stderr)
        return 2

    print(f"Done: parsed={result.total}")
    for src, n in sorted(result.by_text_source.items()):
        print(f"  text_source={src}: {n}")
    for label, path in result.output_paths.items():
        print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
