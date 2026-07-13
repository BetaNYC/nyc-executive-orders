"""Prove the offline guard actually blocks real network calls.

If this test ever fails, the autouse `_no_live_network` fixture is not doing its
job and the suite could be silently reaching nyc.gov.
"""

from __future__ import annotations

import socket

import pytest


def test_getaddrinfo_is_blocked():
    with pytest.raises(RuntimeError, match="LIVE network call"):
        socket.getaddrinfo("www.nyc.gov", 443)


def test_socket_connect_is_blocked():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(RuntimeError, match="LIVE network call"):
        s.connect(("93.184.216.34", 80))


def test_requests_backend_cannot_reach_network():
    # Constructing the real requests backend is fine; using it must hit the guard.
    from nyc_executive_orders.fetch import FetchError, RequestsFetcher

    fetcher = RequestsFetcher()
    with pytest.raises((RuntimeError, FetchError)):
        fetcher.get_text("https://www.nyc.gov/bin/nyc/articlesearch.json")


def test_local_ocr_makes_zero_network_calls(tmp_path):
    """The OCR path must be fully local — no network, no cloud (standards §7).

    Under the autouse socket guard (which raises on ANY getaddrinfo/connect), a
    real local ocrmypdf run over the scanned fixture must succeed and recover
    text. If any code path tried to phone home, the guard would fire. This is the
    OCR analogue of the harvest/Wayback network guards above.
    """
    pytest.importorskip("ocrmypdf")
    from pathlib import Path

    from nyc_executive_orders.ocr import TEXT_SOURCE_OCR, ocr_and_extract

    scanned = Path(__file__).parent / "fixtures" / "scanned_sample.pdf"
    result = ocr_and_extract(scanned)
    # Completed locally (no guard tripped) AND produced text.
    assert result.text_source == TEXT_SOURCE_OCR
    assert result.has_text


def test_gap_recovery_real_wayback_client_is_blocked():
    """The gap-recovery path (Phase B.2) must not reach the live Internet Archive.

    Building the archiver's real go-slow client opens no socket, but the moment a
    real CDX search runs it must hit the autouse `_no_live_wayback` guard — the
    same protection Phase B relies on, now proven for the new gap-recovery code.
    """
    pytest.importorskip("wayback")
    from nyc_executive_orders.recover_gaps import find_latest_pdf_capture
    from run_gap_recovery_live import _build_client

    client = _build_client()  # constructs a WaybackClient; no network yet
    with pytest.raises(RuntimeError, match="LIVE (wayback|network) call"):
        find_latest_pdf_capture(client, "https://www.nyc.gov/x/eeo-271.pdf")
