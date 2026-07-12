"""Fetch abstraction — one seam so the live run can pick a backend and tests
can mock it entirely (no network).

Why an abstraction: nyc.gov fronts its content with a WAF that 403s plain
non-browser HTTP (verified 2026-07-11 — WebFetch got 403s; the JSON loaded fine
in a real browser). So there are two live backends:

  * `RequestsFetcher` — `requests` with browser-like headers. Fast; works for
    the JSON API and most article pages. Raises `WAFBlocked` on a 403/406 so a
    caller can fall back.
  * `PlaywrightFetcher` — headless Chromium via Playwright's documented
    `APIRequestContext` (`browser_context.request.get(url)` ->
    `APIResponse.text()` / `.body()` / `.status` / `.ok`). Uses a real browser
    network stack, which clears the WAF. Playwright is an OPTIONAL dependency
    (`pip install 'nyc-executive-orders[live]'` + `playwright install chromium`)
    and is imported lazily, so the package and its offline tests do not need it.

  * `DefaultFetcher` — tries requests first, falls back to Playwright on
    `WAFBlocked`. This is what the supervised live harvest uses.

The `Fetcher` protocol is `get_text(url) -> str` and `get_bytes(url) -> bytes`.
Tests inject a fake object with the same two methods; the real backends are
never constructed in tests, and an autouse conftest guard blocks real sockets.

Playwright API references (built against, not guessed —
https://playwright.dev/python/docs/api/class-apirequestcontext):
  * sync_playwright().start() -> Playwright
  * playwright.chromium.launch(headless=True) -> Browser
  * browser.new_context(user_agent=..., extra_http_headers=...) -> BrowserContext
  * context.request.get(url) -> APIResponse
  * APIResponse.ok: bool ; .status: int ; .text() -> str ; .body() -> bytes
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

import requests

from . import config


class FetchError(RuntimeError):
    """Any failure to retrieve a URL."""


class WAFBlocked(FetchError):
    """The origin's WAF rejected the request (typically HTTP 403/406).

    Signals a caller (e.g. DefaultFetcher) that a browser-backed retry may
    succeed where a plain HTTP request did not.
    """


@runtime_checkable
class Fetcher(Protocol):
    """Minimal fetch surface the harvest depends on."""

    def get_text(self, url: str) -> str: ...

    def get_bytes(self, url: str) -> bytes: ...


def fetch_json(fetcher: Fetcher, url: str) -> dict:
    """Fetch `url` and parse it as JSON. Kept here so every backend is reused."""
    return json.loads(fetcher.get_text(url))


# --------------------------------------------------------------------------- #
# requests-based backend (browser-like headers)
# --------------------------------------------------------------------------- #
def _browser_headers() -> dict[str, str]:
    # Do NOT add custom X-* headers: the nyc.gov WAF rejects header names with
    # crawler-ish keywords even when the UA looks like a browser. The Sec-Fetch-*
    # set mimics a real top-level navigation and helps clear WAF fingerprinting.
    return {
        "User-Agent": config.USER_AGENT,
        "From": config.FROM_HEADER,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,*/*;q=0.8"
        ),
        "Accept-Encoding": "gzip, deflate",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }

# Status codes we treat as "the WAF blocked us; a browser backend might work."
_WAF_STATUS = frozenset({403, 406})


class RequestsFetcher:
    """`requests`-backed fetcher with browser-like headers."""

    def __init__(self, timeout: int = config.REQUEST_TIMEOUT):
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update(_browser_headers())

    def _get(self, url: str) -> requests.Response:
        try:
            resp = self._session.get(url, timeout=self._timeout, allow_redirects=True)
        except requests.RequestException as exc:  # network/DNS/timeout
            raise FetchError(f"requests GET failed for {url}: {exc}") from exc
        if resp.status_code in _WAF_STATUS:
            raise WAFBlocked(f"HTTP {resp.status_code} (WAF) for {url}")
        if resp.status_code >= 400:
            raise FetchError(f"HTTP {resp.status_code} for {url}")
        return resp

    def get_text(self, url: str) -> str:
        return self._get(url).text

    def get_bytes(self, url: str) -> bytes:
        return self._get(url).content


# --------------------------------------------------------------------------- #
# Playwright-based backend (WAF fallback) — lazily imported, optional dep.
# --------------------------------------------------------------------------- #
_PLAYWRIGHT_MISSING = (
    "Playwright is not installed. Install the optional live extra:\n"
    "    pip install 'nyc-executive-orders[live]'\n"
    "    python -m playwright install chromium"
)


class PlaywrightFetcher:
    """Headless-Chromium fetcher for WAF-blocked pages.

    Uses Playwright's documented sync `APIRequestContext`. The sync API MUST NOT
    be driven from inside a running asyncio event loop (Playwright raises in that
    case); the supervised live harvest script runs standalone with no loop, so
    this is safe there. We detect a running loop and fail with a clear message
    rather than breaking obscurely (a lesson borrowed from the newsletter
    scanner's fetch_browser).
    """

    def __init__(self):
        self._pw = None
        self._browser = None
        self._context = None

    def _ensure(self):
        if self._context is not None:
            return
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # no running loop — good, sync API is safe
        else:  # pragma: no cover - only under an async harness
            raise FetchError(
                "PlaywrightFetcher (sync API) cannot run inside a running asyncio "
                "loop. Run the supervised harvest as a standalone script."
            )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise FetchError(_PLAYWRIGHT_MISSING) from exc

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            user_agent=config.USER_AGENT,
            extra_http_headers={"From": config.FROM_HEADER},
        )

    def _request(self, url: str):
        self._ensure()
        resp = self._context.request.get(url, timeout=config.REQUEST_TIMEOUT * 1000)
        if resp.status in _WAF_STATUS:
            raise WAFBlocked(f"HTTP {resp.status} (WAF, via browser) for {url}")
        if not resp.ok:
            raise FetchError(f"HTTP {resp.status} (via browser) for {url}")
        return resp

    def get_text(self, url: str) -> str:
        return self._request(url).text()

    def get_bytes(self, url: str) -> bytes:
        return self._request(url).body()

    def close(self) -> None:  # pragma: no cover - env-dependent
        try:
            if self._context is not None:
                self._context.close()
            if self._browser is not None:
                self._browser.close()
            if self._pw is not None:
                self._pw.stop()
        finally:
            self._pw = self._browser = self._context = None


# --------------------------------------------------------------------------- #
# Default backend: requests, falling back to Playwright on a WAF block.
# --------------------------------------------------------------------------- #
class DefaultFetcher:
    """Try `requests`; on a WAF block, retry once with Playwright.

    The Playwright backend is constructed lazily on first fallback, so a run
    that never hits the WAF never launches a browser.
    """

    def __init__(self):
        self._requests = RequestsFetcher()
        self._browser: PlaywrightFetcher | None = None

    def _browser_backend(self) -> PlaywrightFetcher:
        if self._browser is None:
            self._browser = PlaywrightFetcher()
        return self._browser

    def get_text(self, url: str) -> str:
        try:
            return self._requests.get_text(url)
        except WAFBlocked:
            return self._browser_backend().get_text(url)

    def get_bytes(self, url: str) -> bytes:
        try:
            return self._requests.get_bytes(url)
        except WAFBlocked:
            return self._browser_backend().get_bytes(url)

    def close(self) -> None:  # pragma: no cover - env-dependent
        if self._browser is not None:
            self._browser.close()


def build_fetcher(backend: str = "default") -> Fetcher:
    """Construct a live fetcher. Only ever called by the supervised live run."""
    if backend == "requests":
        return RequestsFetcher()
    if backend == "playwright":
        return PlaywrightFetcher()
    if backend == "default":
        return DefaultFetcher()
    raise ValueError(f"unknown fetch backend: {backend!r}")
