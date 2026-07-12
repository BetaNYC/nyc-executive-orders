"""Light metadata index — one record per EO, from the API + filename only.

NO OCR, NO full-text (Phase A). The field set is LOCKED (project STATUS.md):

    eo_id, number, year, is_emergency, date_signed, title,
    source_pdf_url, pdf_path, source

Written in two formats from the same rows: eo_index.json (a JSON array, for
tooling) and eo_index.csv (spreadsheet-friendly). Writes overwrite in place, so
re-running is idempotent.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import config

# Locked column order (also the JSON key order).
INDEX_FIELDS = [
    "eo_id",
    "number",
    "year",
    "is_emergency",
    "date_signed",
    "title",
    "source_pdf_url",
    "pdf_path",
    "source",
]


@dataclass
class IndexRow:
    """One EO's light metadata record (locked field set)."""

    eo_id: str
    number: int | None
    year: int
    is_emergency: bool
    date_signed: str | None
    title: str
    source_pdf_url: str | None
    pdf_path: str | None
    source: str = config.SOURCE_LIVE

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in INDEX_FIELDS}


def write_index(rows: Iterable[IndexRow], index_dir: str | Path) -> dict:
    """Write eo_index.json and eo_index.csv. Returns the two paths."""
    out = Path(index_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = list(rows)

    json_path = out / "eo_index.json"
    json_path.write_text(
        json.dumps([r.as_dict() for r in rows], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    csv_path = out / "eo_index.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        for row in rows:
            data = row.as_dict()
            # Render booleans/None consistently for CSV consumers.
            writer.writerow(
                {
                    k: (
                        ""
                        if data[k] is None
                        else str(data[k]).lower()
                        if isinstance(data[k], bool)
                        else data[k]
                    )
                    for k in INDEX_FIELDS
                }
            )

    return {"json": json_path, "csv": csv_path}
