"""NYC mayoral executive orders — Phase A harvester + light metadata index.

Phase A scope (this build): enumerate current-era EOs from live nyc.gov via the
`articlesearch.json` API, resolve each order's source PDF URL from its article
page, download the PDFs (git-LFS), and emit a light metadata index + manifest.

Explicitly OUT of Phase A: OCR, full-text parsing, supersession graphs, and the
historical Wayback backfill. See README.md and
team/research/mayoral-executive-orders/2026-07-11-eo-scraper-build-plan.md.
"""

__version__ = "0.1.0"
