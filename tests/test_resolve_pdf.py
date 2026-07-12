"""PDF-URL extraction from an article page."""

from __future__ import annotations

from nyc_executive_orders import resolve_pdf


def test_extract_pdf_url_picks_dam_link_ignoring_decoys(article_html):
    url = resolve_pdf.extract_pdf_url(article_html)
    assert url == (
        "https://www.nyc.gov/content/dam/nycgov/mayors-office/downloads/pdf/"
        "executive-orders/2024/EEO-718-of-2024.pdf"
    )


def test_extract_pdf_url_none_when_absent():
    html = '<html><body><a href="/about">About</a></body></html>'
    assert resolve_pdf.extract_pdf_url(html) is None


def test_extract_pdf_url_absolutizes_relative_href():
    html = (
        '<a href="/content/dam/nycgov/mayors-office/downloads/pdf/'
        'executive-orders/2023/EO-1-of-2023.pdf">pdf</a>'
    )
    assert resolve_pdf.extract_pdf_url(html) == (
        "https://www.nyc.gov/content/dam/nycgov/mayors-office/downloads/pdf/"
        "executive-orders/2023/EO-1-of-2023.pdf"
    )


def test_extract_pdf_url_ignores_non_eo_pdf():
    html = '<a href="/assets/downloads/pdf/reports/annual.pdf">report</a>'
    assert resolve_pdf.extract_pdf_url(html) is None


def test_resolve_pdf_url_uses_fetcher(fake_fetcher_cls):
    url = "https://www.nyc.gov/article.html"
    pdf = (
        "https://www.nyc.gov/content/dam/nycgov/mayors-office/downloads/pdf/"
        "executive-orders/2024/EO-9-of-2024.pdf"
    )
    fetcher = fake_fetcher_cls(texts={url: f'<a href="{pdf}">pdf</a>'})
    assert resolve_pdf.resolve_pdf_url(fetcher, url) == pdf
    assert fetcher.text_calls == [url]
