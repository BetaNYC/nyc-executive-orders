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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
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


@pytest.fixture(autouse=True)
def _no_live_wayback(monkeypatch):
    """Fail hard if any test causes a real archiver/Wayback client call.

    Belt-and-suspenders with the socket guard: monkeypatch the REAL
    `wayback.WaybackClient.search` / `.get_memento` to blow up. Phase B tests
    inject a FakeWaybackClient; if a code path ever built the real client, this
    fires loudly. Skipped if `wayback` isn't importable (it is a dev dep).
    """
    wayback = pytest.importorskip("wayback")

    def _boom(*_args, **_kwargs):  # pragma: no cover - only fires on misuse
        raise RuntimeError(
            "A test attempted a LIVE wayback call. Tests must use FakeWaybackClient."
        )

    monkeypatch.setattr(wayback.WaybackClient, "search", _boom, raising=True)
    monkeypatch.setattr(wayback.WaybackClient, "get_memento", _boom, raising=True)
    yield


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Parse-pipeline fixtures — tiny committed PDFs (regenerate with
# tests/fixtures/generate_fixtures.py). born_digital has a real text layer with
# a hyphenated line-wrap; scanned is the same content rendered image-only.
# --------------------------------------------------------------------------- #
@pytest.fixture
def born_digital_pdf() -> Path:
    return FIXTURES / "born_digital_sample.pdf"


@pytest.fixture
def scanned_pdf() -> Path:
    return FIXTURES / "scanned_sample.pdf"


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


# --------------------------------------------------------------------------- #
# Phase B (Wayback) fixtures — faithful to the archiver / EDGI `wayback` surface
# --------------------------------------------------------------------------- #
@pytest.fixture
def make_cdx():
    """Factory building REAL `wayback.CdxRecord` instances (version-robust).

    Mirrors the archiver's own test fixture so Phase B mocks encode the library's
    documented record shape (urlkey, timestamp, original, mimetype, statuscode,
    digest, length) rather than a guessed one — engineering-standards §0.
    """
    wayback = pytest.importorskip("wayback")
    CdxRecord = wayback.CdxRecord

    def _make(
        original: str,
        timestamp="20180601000000",
        *,
        mimetype: str = "application/pdf",
        statuscode: int | None = 200,
        digest: str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
        length: int | None = 4096,
        urlkey: str | None = None,
    ):
        if isinstance(timestamp, datetime):
            ts = timestamp
        else:
            ts = datetime.strptime(timestamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        values = {
            "urlkey": urlkey or original,
            "timestamp": ts,
            "original": original,
            "mimetype": mimetype,
            "statuscode": statuscode,
            "digest": digest,
            "length": length,
        }
        return CdxRecord(**{f: values.get(f) for f in CdxRecord._fields})

    return _make


class FakeWaybackClient:
    """Duck-typed stand-in for the archiver's go-slow `wayback.WaybackClient`.

    Records every search + get_memento call so tests can assert (e.g. that a
    dry-run issues ZERO memento fetches). `search` yields the canned records
    regardless of query (the archiver applies the MIME/status/year filters);
    `get_memento` returns a Memento-shaped object exposing `.content` (the
    documented attribute Phase B reads).
    """

    def __init__(
        self,
        records: list | None = None,
        *,
        memento_content: bytes = b"%PDF-1.4 archived body",
        raise_on_memento: Exception | None = None,
    ):
        self._records = list(records or [])
        self._memento_content = memento_content
        self._raise_on_memento = raise_on_memento
        self.search_calls: list[tuple[str, dict]] = []
        self.memento_calls: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search(self, url, **kwargs):
        self.search_calls.append((url, kwargs))
        yield from self._records

    def get_memento(self, record, **kwargs):
        self.memento_calls.append(record)
        if self._raise_on_memento is not None:
            raise self._raise_on_memento
        return SimpleNamespace(
            content=self._memento_content, status_code=200, ok=True
        )


@pytest.fixture
def fake_wayback_client_cls():
    return FakeWaybackClient


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
