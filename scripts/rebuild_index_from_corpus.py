#!/usr/bin/env python3
"""Reconstruct the FULL ``index/eo_index.json`` (+ .csv) from ``corpus/eo.json``.

``index/`` is a gitignored, regenerated artifact. A scoped harvest overwrites it
with only its own records, which then can't rebuild the real corpus and arms the
``build_corpus`` shrink guard. ``corpus/eo.json`` is committed and holds every
record's metadata as a superset of the locked index field set, so the full index
is a deterministic one-pass projection of it — no network, no re-harvest.

    uv run python scripts/rebuild_index_from_corpus.py

Idempotent: re-running yields byte-identical output (same write path as the
harvest's own indexer, so the regenerated index is format-compatible).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders import config  # noqa: E402
from nyc_executive_orders.index import INDEX_FIELDS, IndexRow, write_index  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = REPO_ROOT / "corpus" / "eo.json"


def rows_from_corpus(records: list[dict]) -> list[IndexRow]:
    """Project each corpus record down to the locked light-index field set."""
    return [
        IndexRow(
            eo_id=r["eo_id"],
            number=r.get("number"),
            year=int(r["year"]),
            is_emergency=bool(r["is_emergency"]),
            date_signed=r.get("date_signed"),
            title=r.get("title") or "",
            source_pdf_url=r.get("source_pdf_url"),
            pdf_path=r.get("pdf_path"),
            source=r.get("source") or config.SOURCE_LIVE,
        )
        for r in records
    ]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corpus", default=str(DEFAULT_CORPUS), help="Path to corpus/eo.json.")
    p.add_argument("--index-dir", default=str(config.DEFAULT_INDEX_DIR))
    args = p.parse_args(argv)

    records = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    rows = rows_from_corpus(records)
    paths = write_index(rows, args.index_dir)

    print(f"Rebuilt index from {args.corpus}: {len(rows)} records "
          f"({len(INDEX_FIELDS)} fields)")
    print(f"  json: {paths['json']}")
    print(f"  csv:  {paths['csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
