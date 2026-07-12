"""End-to-end harvest: dry-run makes zero downloads; download mode works;
re-runs are idempotent. All offline via the injected FakeFetcher."""

from __future__ import annotations

import json

from nyc_executive_orders.harvest import run_harvest


def _dirs(tmp_path):
    return {
        "out_dir": tmp_path,
        "pdf_dir": tmp_path / "pdfs",
        "index_dir": tmp_path / "index",
    }


def test_dry_run_makes_zero_downloads(tmp_path, harvest_fetcher):
    result = run_harvest(harvest_fetcher, 2024, 2024, download=False, **_dirs(tmp_path))
    assert result.dry_run is True
    assert result.enumerated == 3
    assert result.resolved == 3  # all three article pages yield a PDF URL
    assert result.downloaded == 0
    # The guardrail check: not a single get_bytes call in a dry run.
    assert harvest_fetcher.bytes_calls == []
    # Index rows carry resolved source_pdf_url but NO pdf_path in a dry run.
    assert all(r.source_pdf_url is not None for r in result.index_rows)
    assert all(r.pdf_path is None for r in result.index_rows)


def test_dry_run_writes_index_manifest_gaps(tmp_path, harvest_fetcher):
    result = run_harvest(harvest_fetcher, 2024, 2024, download=False, **_dirs(tmp_path))
    assert result.output_paths["index_json"].exists()
    assert result.output_paths["index_csv"].exists()
    assert result.output_paths["manifest"].exists()
    assert result.output_paths["gaps"].exists()

    data = json.loads(result.output_paths["index_json"].read_text(encoding="utf-8"))
    ids = sorted(r["eo_id"] for r in data)
    assert ids == ["2024-EEO-716", "2024-EEO-718", "2024-EO-042"]

    # 717 is missing from the emergency series -> flagged in gaps.md.
    gaps = result.output_paths["gaps"].read_text(encoding="utf-8")
    assert "717" in gaps


def test_download_mode_writes_pdfs(tmp_path, harvest_fetcher):
    dirs = _dirs(tmp_path)
    result = run_harvest(harvest_fetcher, 2024, 2024, download=True, **dirs)
    assert result.dry_run is False
    assert result.downloaded == 3
    assert len(harvest_fetcher.bytes_calls) == 3
    assert (dirs["pdf_dir"] / "2024" / "2024-EEO-718.pdf").exists()
    assert (dirs["pdf_dir"] / "2024" / "2024-EO-042.pdf").exists()
    # pdf_path populated in the index for downloaded orders.
    assert all(r.pdf_path is not None for r in result.index_rows)


def test_download_mode_is_idempotent(tmp_path, harvest_fetcher, fake_fetcher_cls):
    dirs = _dirs(tmp_path)
    run_harvest(harvest_fetcher, 2024, 2024, download=True, **dirs)

    # Fresh fetcher for the second run so we can prove no PDF bytes were fetched.
    second = fake_fetcher_cls(
        pages=harvest_fetcher._pages, texts=harvest_fetcher._texts
    )
    result = run_harvest(second, 2024, 2024, download=True, **dirs)
    assert result.cached == 3
    assert result.downloaded == 0
    assert second.bytes_calls == []  # everything served from the local cache


def test_unresolved_pdf_flows_to_manifest(tmp_path, fake_fetcher_cls, articlesearch_pages):
    # Article pages with NO EO PDF link -> resolved=0, still indexed + flagged.
    origin = "https://www.nyc.gov"
    texts = {
        f"{origin}/mayors-office/news/2024/12/emergency-executive-order-718.html": "<html></html>",
        f"{origin}/mayors-office/news/2024/12/emergency-executive-order-716.html": "<html></html>",
        f"{origin}/mayors-office/news/2024/01/executive-order-42.html": "<html></html>",
    }
    fetcher = fake_fetcher_cls(pages=articlesearch_pages, texts=texts)
    result = run_harvest(fetcher, 2024, 2024, download=True, **_dirs(tmp_path))
    assert result.resolved == 0
    assert result.errors == 3  # download requested but no URL to fetch
    gaps = result.output_paths["gaps"].read_text(encoding="utf-8")
    assert "could not be resolved" in gaps
