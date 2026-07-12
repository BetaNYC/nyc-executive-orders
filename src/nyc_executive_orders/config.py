"""Central configuration for the Phase A harvest — one auditable place.

Source shapes captured live (2026-07-11, Chrome pass — see project STATUS.md)
and built against here, per engineering-standards §0 (never guess an interface):

  * Enumeration API (returns JSON):
      https://www.nyc.gov/bin/nyc/articlesearch.json
        ?types=executive-orders
        &fromDate=YYYY-MM-DD&toDate=YYYY-MM-DD
        &pageSize=<n>&currentPage=<n>
    Response fields used: pageSize, currentPage, totalResults, totalPages,
    results[] = { link, title, articleDate, articleImage, articleImageAlt,
    articleDesc }. `title` carries the EO number and the "Emergency" flag.

  * Article page -> PDF: each article HTML page links the actual PDF at a "dam"
    path, e.g.
      https://www.nyc.gov/content/dam/nycgov/mayors-office/downloads/pdf/executive-orders/YYYY/<file>.pdf
    The filename is NOT derivable from the JSON, so the article page must be
    fetched and the PDF href extracted (see resolve_pdf.py).

  * WAF: nyc.gov WAF-blocks plain non-browser HTTP (403s). The fetch layer
    (fetch.py) sends browser-like headers and falls back to headless Playwright.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_NAME = "nyc-executive-orders"

# --- Enumeration API --------------------------------------------------------
NYCGOV_ORIGIN = "https://www.nyc.gov"
ARTICLESEARCH_PATH = "/bin/nyc/articlesearch.json"
ARTICLESEARCH_TYPE = "executive-orders"

# Page size for the paginated enumeration. A ceiling per request, not a target.
DEFAULT_PAGE_SIZE = 100

# Coverage floor for the live source. The `articlesearch.json` API reliably
# reaches back to ~2022; older years return fewer/none (historical is Phase B).
COVERAGE_FLOOR_YEAR = 2022

# --- Fetch etiquette --------------------------------------------------------
# Descriptive browser-like User-Agent. The WAF rejects obvious bot UAs, so we
# present a realistic Chrome UA and identify the operator via the From: header
# (RFC 7231 §5.5.2), matching the newsletter scanner's proven approach.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
FROM_HEADER = "free.stuff@beta.nyc"

# Go-slow delay (seconds) between LIVE network calls in the supervised harvest.
# ~1 request / 2.5s. Tests inject a fake fetcher and pass delay=0.
DEFAULT_DELAY_SECONDS = 2.5

# Per-request timeout (seconds) for the requests-based fetch path.
REQUEST_TIMEOUT = 30

# --- Paths ------------------------------------------------------------------
# Repo root = two levels up from this file (src/nyc_executive_orders/config.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Default output root. Index + manifest are written under here; PDFs land in
# pdfs/YYYY/ (git-LFS-tracked).
DEFAULT_OUT_DIR = _REPO_ROOT
DEFAULT_PDF_DIR = _REPO_ROOT / "pdfs"
DEFAULT_INDEX_DIR = _REPO_ROOT / "index"

# Provenance tag stamped on every index row produced by this (live) source.
SOURCE_LIVE = "live-nycgov"

# --- Phase B: historical Wayback backfill -----------------------------------
# Provenance tag for rows recovered from the Internet Archive (Wayback Machine).
SOURCE_WAYBACK = "wayback"

# CDX prefix for the historical EO PDFs the 2026 nyc.gov redesign removed. The
# documented old filename pattern (project STATUS.md; README data landscape) is
#   nyc.gov/html/records/pdf/executive_orders/YYYYEO0NN.pdf
# so a single CDX prefix-match query over this path enumerates the historical
# corpus (~801 orders in one query, per the README). We enumerate what CDX
# actually returns and parse each captured filename — filenames are NOT guessed.
WAYBACK_EO_URL_PREFIX = "nyc.gov/html/records/pdf/executive_orders/"

# Historical coverage floor: NYC Admin Code § 3-113.1 draws the line at EOs
# issued on or after January 1, 1974. Captures older than this are out of scope.
HISTORICAL_FLOOR_YEAR = 1974

# The live source (Phase A) is authoritative for the current era; the Wayback
# backfill (Phase B) fills 1974 -> ~2022. On an eo_id collision between the two,
# the live row wins (fresher, richer metadata) and the Wayback duplicate is
# dropped — see gather_wayback_eo.merge_prefer_live.
