"""Resolve an EO's source PDF URL from its article page.

The `articlesearch.json` results do NOT carry the PDF filename, so each article
HTML page must be fetched and its dam PDF link extracted. The captured shape
(config.py header) is:

    https://www.nyc.gov/content/dam/nycgov/mayors-office/downloads/pdf/executive-orders/YYYY/<file>.pdf

We match any <a href> pointing at a `.../executive-orders/....pdf` under the dam
downloads tree (absolute or root-relative), which is tolerant of small path
variations while still being specific to the EO PDF location.
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from . import config
from .fetch import Fetcher

logger = logging.getLogger("nyc_executive_orders.resolve_pdf")

# An href is an EO PDF when it points under an .../executive-orders/... path and
# ends in .pdf (case-insensitive). Query strings/fragments are tolerated.
_EO_PDF_HREF_RE = re.compile(
    r"executive-orders/.*\.pdf(?:[?#].*)?$", re.IGNORECASE
)


def _absolutize(href: str) -> str:
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if not href.startswith("/"):
        href = "/" + href
    return config.NYCGOV_ORIGIN + href


def extract_pdf_url(html: str) -> str | None:
    """Return the first EO dam PDF URL in an article page, or None.

    Pure function over HTML text — the offline tests exercise this directly with
    a saved article fixture, no network involved.
    """
    soup = BeautifulSoup(html, "html.parser")
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if _EO_PDF_HREF_RE.search(href):
            return _absolutize(href)
    return None


def resolve_pdf_url(fetcher: Fetcher, article_url: str) -> str | None:
    """Fetch an article page and extract its source PDF URL (or None)."""
    html = fetcher.get_text(article_url)
    pdf_url = extract_pdf_url(html)
    if pdf_url is None:
        logger.warning("resolve.no_pdf article=%s", article_url)
    else:
        logger.info("resolve.ok article=%s pdf=%s", article_url, pdf_url)
    return pdf_url
