"""Offline guard for the lineage tests.

No network, no PDF, no external binary, no LLM (engineering-standards §7). Mirrors
``tests/conftest.py`` in the main suite: any live socket call raises rather than
quietly reaching out.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise RuntimeError(
            "A test attempted a LIVE network call. The lineage pass is fully "
            "local (engineering-standards §7).")

    monkeypatch.setattr(socket, "getaddrinfo", _blocked)
    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
