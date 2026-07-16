#!/usr/bin/env python3
"""Full-corpus CLEAN sweep — apply the clean stage to the whole corpus in place.

Post-process ONLY: it reads the existing ``corpus/eo.json`` (whose ``full_text``
is the verbatim OCR/extraction) and rewrites ``corpus/YYYY/<eo_id>.md`` +
``corpus/eo.json`` + ``corpus/manifest.csv`` with cleaned bodies, filled metadata,
and clean provenance. **The OCR layer is NOT re-run** — cleaning is a text
post-process, so the sweep is fast and the (slow, gated, binary-dependent) OCR is
untouched.

Non-destructive + idempotent: raw input comes from ``full_text_raw`` when present,
else ``full_text``; ``full_text_raw`` always preserves the verbatim original.

Run:
    uv run --no-project --with pyyaml python scripts/run_clean_sweep.py
    uv run --no-project --with pyyaml python scripts/run_clean_sweep.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders.build_corpus import clean_existing_corpus  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / "corpus"
EO_JSON = CORPUS_DIR / "eo.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute + report the quality distribution without writing.")
    args = ap.parse_args(argv)

    records = json.loads(EO_JSON.read_text(encoding="utf-8"))
    print(f"Loaded {len(records)} records from {EO_JSON}")

    if args.dry_run:
        # Re-run the stage in a temp dir so nothing under corpus/ is touched.
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            result = clean_existing_corpus(records, corpus_dir=Path(td) / "corpus")
            written = json.loads(
                Path(result.output_paths["eo_json"]).read_text(encoding="utf-8"))
    else:
        result = clean_existing_corpus(records, corpus_dir=CORPUS_DIR)
        written = json.loads(EO_JSON.read_text(encoding="utf-8"))
    tiers = Counter(r.get("text_quality") for r in written)
    filled_titles = sum(1 for r in written if (r.get("title") or "").strip())
    filled_dates = sum(1 for r in written if r.get("date_signed"))
    ocr = [r for r in written if r.get("text_source") == "ocr"]

    print(f"\n{'DRY-RUN — nothing written' if args.dry_run else 'WROTE corpus in place'}")
    print(f"records: {len(written)}")
    print("text_quality distribution:")
    for q in ("clean", "minor-noise", "needs-review", "no-text"):
        print(f"  {q}: {tiers.get(q, 0)}")
    print(f"titles non-empty: {filled_titles}")
    print(f"dates non-null: {filled_dates}")
    print(f"OCR docs: {len(ocr)}")
    for label, path in result.output_paths.items():
        print(f"  {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
