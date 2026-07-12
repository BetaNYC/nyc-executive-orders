"""Exit-code contract for the supervised live runner (scripts/run_harvest_live.py).

Contract:
  * 0  — completed with zero errors
  * 1  — completed but result.errors > 0
  * 2  — missing the human-gate flag (refuses to run)

All offline: build_fetcher / run_harvest are monkeypatched so nothing touches
the network. (scripts/ is on the pytest pythonpath — see pyproject.toml.)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

import run_harvest_live as rhl


@dataclass
class _FakeResult:
    enumerated: int = 1
    resolved: int = 1
    downloaded: int = 0
    cached: int = 0
    errors: int = 0
    output_paths: dict = field(default_factory=dict)


def _argv(*extra: str) -> list[str]:
    return [
        "run_harvest_live.py",
        "--from-year",
        "2024",
        "--to-year",
        "2024",
        "--dry-run",
        *extra,
    ]


def _patch_harvest(monkeypatch, result: _FakeResult) -> None:
    monkeypatch.setattr(rhl, "build_fetcher", lambda backend: object())
    monkeypatch.setattr(rhl, "run_harvest", lambda *a, **k: result)


def test_missing_human_flag_exits_2(monkeypatch):
    # No flag -> refuses before ever building a fetcher.
    monkeypatch.setattr(sys, "argv", _argv())
    called = {"harvest": False}
    monkeypatch.setattr(
        rhl, "run_harvest", lambda *a, **k: called.__setitem__("harvest", True)
    )
    assert rhl.main() == 2
    assert called["harvest"] is False


def test_clean_run_exits_0(monkeypatch):
    _patch_harvest(monkeypatch, _FakeResult(errors=0))
    monkeypatch.setattr(sys, "argv", _argv("--i-am-a-human-running-this-supervised"))
    assert rhl.main() == 0


def test_completed_with_errors_exits_1(monkeypatch):
    _patch_harvest(monkeypatch, _FakeResult(errors=3))
    monkeypatch.setattr(sys, "argv", _argv("--i-am-a-human-running-this-supervised"))
    assert rhl.main() == 1
