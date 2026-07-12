"""PDF download: writes to pdfs/YYYY/, idempotent cache, error handling."""

from __future__ import annotations

from nyc_executive_orders.download import download_pdf, pdf_dest


def test_pdf_dest_layout(tmp_path):
    dest = pdf_dest("2024-EEO-718", 2024, tmp_path)
    assert dest == tmp_path / "2024" / "2024-EEO-718.pdf"


def test_download_writes_file(tmp_path, fake_fetcher_cls):
    fetcher = fake_fetcher_cls()
    url = "https://www.nyc.gov/eo.pdf"
    result = download_pdf(fetcher, "2024-EEO-718", 2024, url, pdf_dir=tmp_path)
    assert result.status == "downloaded"
    dest = tmp_path / "2024" / "2024-EEO-718.pdf"
    assert dest.exists()
    assert dest.read_bytes() == b"%PDF-1.4 fake body"
    assert fetcher.bytes_calls == [url]


def test_download_is_idempotent(tmp_path, fake_fetcher_cls):
    fetcher = fake_fetcher_cls()
    url = "https://www.nyc.gov/eo.pdf"
    download_pdf(fetcher, "2024-EEO-718", 2024, url, pdf_dir=tmp_path)
    fetcher2 = fake_fetcher_cls()
    result = download_pdf(fetcher2, "2024-EEO-718", 2024, url, pdf_dir=tmp_path)
    assert result.status == "cached"
    # No second network call — the cached file short-circuits the fetch.
    assert fetcher2.bytes_calls == []


def test_download_on_fetch_only_on_real_download(tmp_path, fake_fetcher_cls):
    calls = []
    url = "https://www.nyc.gov/eo.pdf"
    download_pdf(
        fake_fetcher_cls(), "2024-EEO-718", 2024, url,
        pdf_dir=tmp_path, on_fetch=lambda: calls.append(1),
    )
    assert calls == [1]
    # Cached re-run must not invoke the go-slow delay.
    download_pdf(
        fake_fetcher_cls(), "2024-EEO-718", 2024, url,
        pdf_dir=tmp_path, on_fetch=lambda: calls.append(1),
    )
    assert calls == [1]


def test_download_records_error(tmp_path, fake_fetcher_cls):
    fetcher = fake_fetcher_cls(raise_on_bytes=RuntimeError("boom"))
    result = download_pdf(fetcher, "2024-EEO-718", 2024, "u", pdf_dir=tmp_path)
    assert result.status == "error"
    assert result.pdf_path is None
    assert "boom" in result.error
