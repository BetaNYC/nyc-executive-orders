"""Exact-duplicate collapse + same-id-different-pdf conflict guard.

The nyc.gov feed lists some orders twice verbatim (same eo_id, title, date, and
PDF URL). `dedupe_rows` collapses those to one row across both the index and the
manifest, while a record that shares an eo_id but points at a DIFFERENT PDF is a
real conflict — kept and surfaced, never silently merged.
"""

from __future__ import annotations

import logging

from nyc_executive_orders.harvest import dedupe_rows
from nyc_executive_orders.index import IndexRow
from nyc_executive_orders.manifest import ManifestRow


def _pair(eo_id, number, pdf_url, *, year=2025):
    """One (IndexRow, ManifestRow) pair as harvest builds them per feed entry."""
    ir = IndexRow(
        eo_id=eo_id,
        number=number,
        year=year,
        is_emergency=False,
        date_signed="2025-05-13",
        title=f"Executive Order {number}",
        source_pdf_url=pdf_url,
        pdf_path=None,
    )
    mr = ManifestRow(
        eo_id=eo_id,
        number=number,
        year=year,
        is_emergency=False,
        date_signed="2025-05-13",
        title=f"Executive Order {number}",
        article_url=f"https://www.nyc.gov/{eo_id}.html",
        source_pdf_url=pdf_url,
        pdf_path=None,
        pdf_resolved=pdf_url is not None,
        download_status="skipped",
    )
    return ir, mr


def _split(pairs):
    index_rows = [ir for ir, _ in pairs]
    manifest_rows = [mr for _, mr in pairs]
    return index_rows, manifest_rows


def test_exact_duplicate_collapsed_and_conflict_surfaced(caplog):
    url_51 = "https://www.nyc.gov/.../eo-51.pdf"
    url_52a = "https://www.nyc.gov/.../eo-52.pdf"
    url_52b = "https://www.nyc.gov/.../eo-52-REVISED.pdf"

    pairs = [
        _pair("2025-EO-051", "51", url_51),  # first occurrence — kept
        _pair("2025-EO-051", "51", url_51),  # EXACT duplicate — dropped
        _pair("2025-EO-052", "52", url_52a),  # first occurrence — kept
        _pair("2025-EO-052", "52", url_52b),  # SAME id, DIFFERENT pdf — conflict, kept
    ]
    index_rows, manifest_rows = _split(pairs)

    with caplog.at_level(logging.WARNING, logger="nyc_executive_orders.harvest"):
        kept_index, kept_manifest, dropped, conflicts = dedupe_rows(
            index_rows, manifest_rows
        )

    # The exact duplicate is gone; the conflicting-but-distinct record is retained.
    assert dropped == 1
    assert len(kept_index) == 3
    assert len(kept_manifest) == 3

    # 2025-EO-051 appears exactly once now.
    ids = [r.eo_id for r in kept_index]
    assert ids.count("2025-EO-051") == 1
    # Both 2025-EO-052 rows survive (the conflict was NOT silently merged/dropped).
    assert ids.count("2025-EO-052") == 2

    # The conflict is surfaced structurally and loudly.
    assert conflicts == [("2025-EO-052", url_52a, url_52b)]
    assert any(
        rec.levelno == logging.WARNING and "index.conflict" in rec.getMessage()
        for rec in caplog.records
    )


def test_no_duplicates_is_a_noop():
    pairs = [
        _pair("2025-EO-051", "51", "https://www.nyc.gov/.../eo-51.pdf"),
        _pair("2025-EO-052", "52", "https://www.nyc.gov/.../eo-52.pdf"),
    ]
    index_rows, manifest_rows = _split(pairs)
    kept_index, kept_manifest, dropped, conflicts = dedupe_rows(
        index_rows, manifest_rows
    )
    assert dropped == 0
    assert conflicts == []
    assert len(kept_index) == 2
    assert len(kept_manifest) == 2


def test_deduped_removed_count_is_logged(caplog):
    pairs = [
        _pair("2025-EO-051", "51", "https://www.nyc.gov/.../eo-51.pdf"),
        _pair("2025-EO-051", "51", "https://www.nyc.gov/.../eo-51.pdf"),
    ]
    index_rows, manifest_rows = _split(pairs)
    with caplog.at_level(logging.INFO, logger="nyc_executive_orders.harvest"):
        dedupe_rows(index_rows, manifest_rows)
    assert any("index.deduped removed=1" in rec.getMessage() for rec in caplog.records)
