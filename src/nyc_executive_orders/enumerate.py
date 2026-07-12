"""Enumerate current-era executive orders via the nyc.gov articlesearch API.

Built against the captured JSON shape (config.py header). One year at a time:
fetch page 1 to learn `totalPages`, then page through `currentPage` 2..N. Each
`results[]` item yields an `EOEntry` with the parsed number, emergency flag,
signing year, and ISO date.

`title` carries the identity:
  * "Emergency Executive Order 718"  -> is_emergency=True,  number=718
  * "Executive Order 42"             -> is_emergency=False, number=42
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlencode

from . import config
from .fetch import Fetcher, fetch_json

logger = logging.getLogger("nyc_executive_orders.enumerate")

# The last run of digits in the title is the EO number (see docstring examples).
_NUMBER_RE = re.compile(r"(\d+)\D*$")


@dataclass(frozen=True)
class EOEntry:
    """One enumerated executive order (pre-PDF-resolution)."""

    number: int | None
    is_emergency: bool
    year: int
    date_signed: str | None  # ISO YYYY-MM-DD
    title: str
    article_url: str


def parse_is_emergency(title: str) -> bool:
    """True when the title marks an Emergency Executive Order."""
    return title.strip().lower().startswith("emergency")


def parse_number(title: str) -> int | None:
    """Parse the EO number as the final integer in the title, or None."""
    m = _NUMBER_RE.search(title.strip())
    return int(m.group(1)) if m else None


def parse_article_date(article_date: str) -> str | None:
    """Parse an articleDate like 'December 29, 2024' to ISO 'YYYY-MM-DD'.

    Returns None if the value is missing or not in the expected format (the row
    is still emitted; the gap is recorded downstream).
    """
    if not article_date:
        return None
    try:
        return datetime.strptime(article_date.strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        logger.warning("date.parse_failed value=%r", article_date)
        return None


def _article_url(link: str) -> str:
    """Full article URL from a results[].link path."""
    if link.startswith("http://") or link.startswith("https://"):
        return link
    if not link.startswith("/"):
        link = "/" + link
    return config.NYCGOV_ORIGIN + link


def _articlesearch_url(from_date: str, to_date: str, page_size: int, page: int) -> str:
    """Build one articlesearch.json request URL."""
    query = urlencode(
        {
            "types": config.ARTICLESEARCH_TYPE,
            "fromDate": from_date,
            "toDate": to_date,
            "pageSize": page_size,
            "currentPage": page,
        }
    )
    return f"{config.NYCGOV_ORIGIN}{config.ARTICLESEARCH_PATH}?{query}"


def entry_from_result(result: dict, fallback_year: int) -> EOEntry:
    """Map one articlesearch result dict to an EOEntry.

    `year` is the signing year: taken from the parsed date when available, else
    the enumeration window's year (fallback_year).
    """
    title = (result.get("title") or "").strip()
    date_signed = parse_article_date(result.get("articleDate") or "")
    year = int(date_signed[:4]) if date_signed else fallback_year
    return EOEntry(
        number=parse_number(title),
        is_emergency=parse_is_emergency(title),
        year=year,
        date_signed=date_signed,
        title=title,
        article_url=_article_url(result.get("link") or ""),
    )


def enumerate_year(
    fetcher: Fetcher,
    year: int,
    *,
    page_size: int = config.DEFAULT_PAGE_SIZE,
    on_fetch=None,
) -> list[EOEntry]:
    """Enumerate every EO whose articleDate falls in `year`.

    `on_fetch`, if given, is called with no args after each live page fetch —
    the harvest uses it to apply the go-slow delay. Pagination follows the API's
    reported `totalPages`.
    """
    from_date = f"{year}-01-01"
    to_date = f"{year}-12-31"

    first_url = _articlesearch_url(from_date, to_date, page_size, 1)
    logger.info("enumerate.year.start year=%d", year)
    payload = fetch_json(fetcher, first_url)
    if on_fetch:
        on_fetch()

    total_pages = int(payload.get("totalPages") or 0)
    entries = [entry_from_result(r, year) for r in (payload.get("results") or [])]

    for page in range(2, total_pages + 1):
        url = _articlesearch_url(from_date, to_date, page_size, page)
        payload = fetch_json(fetcher, url)
        if on_fetch:
            on_fetch()
        entries.extend(
            entry_from_result(r, year) for r in (payload.get("results") or [])
        )

    logger.info(
        "enumerate.year.done year=%d pages=%d entries=%d",
        year,
        max(total_pages, 1),
        len(entries),
    )
    return entries


def enumerate_years(
    fetcher: Fetcher,
    from_year: int,
    to_year: int,
    *,
    page_size: int = config.DEFAULT_PAGE_SIZE,
    on_fetch=None,
) -> list[EOEntry]:
    """Enumerate EOs across an inclusive year range."""
    entries: list[EOEntry] = []
    for year in range(from_year, to_year + 1):
        entries.extend(
            enumerate_year(fetcher, year, page_size=page_size, on_fetch=on_fetch)
        )
    return entries
