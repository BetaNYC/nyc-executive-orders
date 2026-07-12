"""Light metadata index: locked fields, JSON + CSV output."""

from __future__ import annotations

import csv
import json

from nyc_executive_orders.index import INDEX_FIELDS, IndexRow, write_index


def _row():
    return IndexRow(
        eo_id="2024-EEO-718",
        number=718,
        year=2024,
        is_emergency=True,
        date_signed="2024-12-29",
        title="Emergency Executive Order 718",
        source_pdf_url="https://www.nyc.gov/content/dam/.../EEO-718-of-2024.pdf",
        pdf_path="pdfs/2024/2024-EEO-718.pdf",
    )


def test_locked_field_set():
    assert INDEX_FIELDS == [
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


def test_index_row_default_source_is_live():
    assert _row().source == "live-nycgov"


def test_write_index_json(tmp_path):
    paths = write_index([_row()], tmp_path)
    data = json.loads(paths["json"].read_text(encoding="utf-8"))
    assert len(data) == 1
    assert list(data[0].keys()) == INDEX_FIELDS
    assert data[0]["eo_id"] == "2024-EEO-718"
    assert data[0]["is_emergency"] is True
    assert data[0]["source"] == "live-nycgov"


def test_write_index_csv(tmp_path):
    paths = write_index([_row()], tmp_path)
    with paths["csv"].open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["eo_id"] == "2024-EEO-718"
    assert rows[0]["is_emergency"] == "true"
    assert rows[0]["number"] == "718"


def test_write_index_none_pdf_path_is_blank_in_csv(tmp_path):
    row = _row()
    row.pdf_path = None
    paths = write_index([row], tmp_path)
    with paths["csv"].open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["pdf_path"] == ""
