"""Manifest + gaps: missing-number detection and gaps.md content."""

from __future__ import annotations

import csv

from nyc_executive_orders.manifest import (
    ManifestRow,
    find_missing_numbers,
    write_gaps,
    write_manifest,
)


def _row(eo_id, number, is_emergency, *, resolved=True, status="downloaded", year=2024):
    return ManifestRow(
        eo_id=eo_id,
        number=number,
        year=year,
        is_emergency=is_emergency,
        date_signed="2024-01-01",
        title=eo_id,
        article_url=f"https://www.nyc.gov/{eo_id}.html",
        source_pdf_url=None if not resolved else f"https://www.nyc.gov/{eo_id}.pdf",
        pdf_path=None,
        pdf_resolved=resolved,
        download_status=status,
    )


def test_find_missing_numbers_within_series():
    rows = [
        _row("2024-EEO-716", 716, True),
        _row("2024-EEO-718", 718, True),  # 717 missing
        _row("2024-EO-042", 42, False),
    ]
    missing = find_missing_numbers(rows)
    assert missing[(2024, "emergency")] == [717]
    assert (2024, "regular") not in missing  # single value, no gap


def test_find_missing_numbers_skips_unparsed():
    rows = [_row("2024-EO-UNK", None, False)]
    assert find_missing_numbers(rows) == {}


def test_write_manifest_csv(tmp_path):
    rows = [_row("2024-EEO-718", 718, True)]
    path = write_manifest(rows, tmp_path)
    with path.open(encoding="utf-8") as fh:
        out = list(csv.DictReader(fh))
    assert out[0]["eo_id"] == "2024-EEO-718"
    assert out[0]["pdf_resolved"] == "true"
    assert out[0]["download_status"] == "downloaded"


def test_write_gaps_reports_all_sections(tmp_path):
    rows = [
        _row("2024-EEO-716", 716, True),
        _row("2024-EEO-718", 718, True),  # 717 missing
        _row("2024-EO-UNK", None, False),  # unparsed number
        _row("2024-EO-050", 50, False, resolved=False),  # unresolved PDF
        _row("2024-EO-051", 51, False, status="error"),  # download error
    ]
    rows[-1].download_error = "HTTP 500"
    path = write_gaps(rows, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "Missing numbers" in text
    assert "717" in text
    assert "2024-EO-UNK" in text  # unparsed section
    assert "2024-EO-050" in text  # unresolved section
    assert "HTTP 500" in text  # download error section


def test_write_gaps_clean_run(tmp_path):
    rows = [_row("2024-EO-001", 1, False)]
    text = write_gaps(rows, tmp_path).read_text(encoding="utf-8")
    assert "contiguous" in text
