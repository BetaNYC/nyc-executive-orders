"""Enumerate current-era executive orders via the nyc.gov articlesearch API.

Built against the captured JSON shape (config.py header). One year at a time:
fetch page 1 to learn `totalPages`, then page through `currentPage` 2..N. Each
`results[]` item yields an `EOEntry` with the parsed number, emergency flag,
signing year, and ISO date.

`title` carries the identity. The number *label* is captured verbatim — the
dotted prefix of a Mamdani-era emergency order is part of the identity and must
not be dropped:
  * "Emergency Executive Order 718"       -> is_emergency=True,  number="718"
  * "Emergency Executive Order No. 1.37"  -> is_emergency=True,  number="1.37"
  * "Executive Order No. 17"              -> is_emergency=False, number="17"

Non-EO documents that ride the same feed (e.g. an agency "Designation of ...")
carry no executive-order number pattern; they are filtered out of the
enumeration (see `is_executive_order`).
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

# The EO number sits right after the word "Order", with an optional "No."; it is
# an integer OR a dotted `X.YY` emergency label. Capture the whole token so the
# dotted prefix survives (dropping it collapses 1.37 and 2.37 onto one id).
_NUMBER_RE = re.compile(r"order\s+(?:no\.?\s*)?(\d+(?:\.\d+)?)", re.IGNORECASE)


@dataclass(frozen=True)
class EOEntry:
    """One enumerated executive order (pre-PDF-resolution).

    `number` is the literal number *label* ("718", "1.37", "17"), not an int:
    emergency numbering mixes plain integers and dotted `X.YY` labels, and the
    exact printed form is the identity. `None` means the title had no parseable
    number (still emitted; flagged downstream).
    """

    number: str | None
    is_emergency: bool
    year: int
    date_signed: str | None  # ISO YYYY-MM-DD
    title: str
    article_url: str


def parse_is_emergency(title: str) -> bool:
    """True when the title marks an Emergency Executive Order."""
    return title.strip().lower().startswith("emergency")


def is_executive_order(title: str) -> bool:
    """True when the title is an executive order (vs. a non-EO feed document).

    The nyc.gov article feed mixes in non-EO items (e.g. agency "Designation"
    notices) that have no PDF and no EO number. An executive order title always
    contains the phrase "executive order"; the Designation notice does not, so
    this phrase check cleanly excludes it while still keeping a real EO whose
    number failed to parse (that one is flagged downstream, not dropped).
    """
    return "executive order" in title.strip().lower()


def parse_number(title: str) -> str | None:
    """Parse the EO number *label* from the title, or None.

    Returns the literal token as printed, including any dotted emergency prefix
    ("1.37", "718", "17"). This is the collision-safe identity: the previous
    "last run of digits" heuristic dropped the `X.` prefix, mapping every
    `X.YY` order onto the same `YY`.
    """
    m = _NUMBER_RE.search(title.strip())
    return m.group(1) if m else None


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
    excluded = 0
    entries: list[EOEntry] = []
    excluded += _collect_page(payload, year, entries)

    for page in range(2, total_pages + 1):
        url = _articlesearch_url(from_date, to_date, page_size, page)
        payload = fetch_json(fetcher, url)
        if on_fetch:
            on_fetch()
        excluded += _collect_page(payload, year, entries)

    logger.info(
        "enumerate.year.done year=%d pages=%d entries=%d excluded_non_eo=%d",
        year,
        max(total_pages, 1),
        len(entries),
        excluded,
    )
    return entries


def _collect_page(payload: dict, year: int, entries: list[EOEntry]) -> int:
    """Append the EO results from one page to `entries`; return non-EOs dropped.

    Non-EO feed items (e.g. agency "Designation" notices) are filtered out here
    so they never enter the corpus, and their count is logged for auditability.
    """
    excluded = 0
    for r in payload.get("results") or []:
        title = (r.get("title") or "").strip()
        if not is_executive_order(title):
            excluded += 1
            logger.info("enumerate.excluded_non_eo title=%r", title)
            continue
        entries.append(entry_from_result(r, year))
    return excluded


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
