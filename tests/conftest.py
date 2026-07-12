"""Shared fixtures + a hard guarantee that no test hits the live network.

Two design points keep this suite offline and faithful:

1. `_no_live_network` (autouse) monkeypatches `socket.getaddrinfo` and
   `socket.socket.connect` to raise. Any code path that tried a real network
   call (requests, Playwright, urllib — anything) would fail loudly rather than
   silently reaching nyc.gov. This is the offline guard the build guardrail
   requires.

2. `FakeFetcher` implements the exact `Fetcher` surface (`get_text` / `get_bytes`)
   the pipeline depends on, and its canned payloads are REAL captured shapes:
   the `articlesearch.json` fixtures and a saved article HTML with a genuine dam
   PDF `<a href>` (engineering-standards §0 — mocks encode documented shapes,
   not guesses). It records every call so tests can assert, e.g., that a dry run
   issues ZERO `get_bytes` (download) calls.
"""

from __future__ import annotations

import socket
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    """Fail hard if any test attempts a real network connection."""

    def _boom(*_args, **_kwargs):  # pragma: no cover - only fires on misuse
        raise RuntimeError(
            "A test attempted a LIVE network call. Tests must use FakeFetcher."
        )

    monkeypatch.setattr(socket, "getaddrinfo", _boom, raising=True)
    monkeypatch.setattr(socket.socket, "connect", _boom, raising=True)
    monkeypatch.setattr(socket.socket, "connect_ex", _boom, raising=True)
    yield


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeFetcher:
    """In-memory stand-in for a real Fetcher (get_text / get_bytes).

    Routing:
      * an articlesearch.json URL -> the page whose number matches the
        `currentPage` query param, from `pages` ({page_int: json_str}).
      * any other URL -> `texts[url]` (article HTML), else KeyError.
    `get_bytes` returns `pdf_bytes.get(url, default_pdf)` and records the call.
    """

    def __init__(
        self,
        *,
        pages: dict[int, str] | None = None,
        texts: dict[str, str] | None = None,
        pdf_bytes: dict[str, bytes] | None = None,
        default_pdf: bytes = b"%PDF-1.4 fake body",
        raise_on_bytes: Exception | None = None,
    ):
        self._pages = pages or {}
        self._texts = texts or {}
        self._pdf_bytes = pdf_bytes or {}
        self._default_pdf = default_pdf
        self._raise_on_bytes = raise_on_bytes
        self.text_calls: list[str] = []
        self.bytes_calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.text_calls.append(url)
        if "articlesearch.json" in url:
            page = int(parse_qs(urlparse(url).query).get("currentPage", ["1"])[0])
            return self._pages[page]
        return self._texts[url]

    def get_bytes(self, url: str) -> bytes:
        self.bytes_calls.append(url)
        if self._raise_on_bytes is not None:
            raise self._raise_on_bytes
        return self._pdf_bytes.get(url, self._default_pdf)


@pytest.fixture
def fake_fetcher_cls():
    return FakeFetcher


@pytest.fixture
def articlesearch_pages() -> dict[int, str]:
    return {
        1: load_fixture("articlesearch_2024_page1.json"),
        2: load_fixture("articlesearch_2024_page2.json"),
    }


@pytest.fixture
def article_html() -> str:
    return load_fixture("article_eeo718.html")


@pytest.fixture
def harvest_fetcher(articlesearch_pages, article_html):
    """A FakeFetcher wired for the full 2024 fixture harvest (3 EOs).

    The three article URLs match the `link`s in the JSON fixtures. The 718
    article uses the real saved fixture (with decoy links); the other two use
    minimal-but-valid dam-link HTML.
    """
    origin = "https://www.nyc.gov"

    def dam(year: int, name: str) -> str:
        return (
            f"{origin}/content/dam/nycgov/mayors-office/downloads/pdf/"
            f"executive-orders/{year}/{name}.pdf"
        )

    def minimal_article(pdf_url: str) -> str:
        return f'<html><body><a href="{pdf_url}">Download (PDF)</a></body></html>'

    url_718 = f"{origin}/mayors-office/news/2024/12/emergency-executive-order-718.html"
    url_716 = f"{origin}/mayors-office/news/2024/12/emergency-executive-order-716.html"
    url_42 = f"{origin}/mayors-office/news/2024/01/executive-order-42.html"

    texts = {
        url_718: article_html,  # real fixture with decoys
        url_716: minimal_article(dam(2024, "EEO-716-of-2024")),
        url_42: minimal_article(dam(2024, "EO-42-of-2024")),
    }
    return FakeFetcher(pages=articlesearch_pages, texts=texts)
