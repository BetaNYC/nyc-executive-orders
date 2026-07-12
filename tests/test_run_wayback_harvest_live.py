"""Authorization gate + exit-code contract for scripts/run_wayback_harvest_live.py.

Contract (parity with the Phase A runner):
  * 0  — completed with zero errors
  * 1  — completed but result.errors > 0
  * 2  — missing BOTH authorization gates (refuses to run)

Two equally-strong gates: --i-am-a-human-running-this-supervised (human) or
--operator-authorized (agent, under explicit in-session operator authorization).

All offline: _build_client / run_wayback_harvest are monkeypatched so nothing
touches the network, and the client is never built when the gate refuses.
(scripts/ is on the pytest pythonpath — see pyproject.toml.)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field

import run_wayback_harvest_live as rwhl


@dataclass
class _FakeResult:
    enumerated: int = 1
    unique_urls: int = 1
    wayback_rows: int = 1
    wayback_kept: int = 1
    downloaded: int = 0
    cached: int = 0
    errors: int = 0
    flagged: list = field(default_factory=list)
    conflicts: list = field(default_factory=list)
    dropped_wayback_ids: list = field(default_factory=list)
    output_paths: dict = field(default_factory=dict)


def _argv(*extra: str) -> list[str]:
    return [
        "run_wayback_harvest_live.py",
        "--from-year",
        "1974",
        "--to-year",
        "2022",
        "--dry-run",
        *extra,
    ]


def _patch(monkeypatch, result: _FakeResult) -> None:
    # A context-manager client stand-in so `with client:` works.
    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rwhl, "_build_client", lambda: _Client())
    monkeypatch.setattr(rwhl, "run_wayback_harvest", lambda *a, **k: result)


def test_missing_both_gates_exits_2(monkeypatch):
    monkeypatch.setattr(sys, "argv", _argv())
    called = {"built": False, "harvest": False}
    monkeypatch.setattr(rwhl, "_build_client", lambda: called.__setitem__("built", True))
    monkeypatch.setattr(
        rwhl, "run_wayback_harvest", lambda *a, **k: called.__setitem__("harvest", True)
    )
    assert rwhl.main() == 2
    assert called["built"] is False
    assert called["harvest"] is False


def test_operator_authorized_alone_proceeds_and_logs(monkeypatch, caplog):
    _patch(monkeypatch, _FakeResult(errors=0))
    monkeypatch.setattr(sys, "argv", _argv("--operator-authorized"))
    with caplog.at_level(logging.INFO, logger="nyc_executive_orders.run_wayback_harvest_live"):
        assert rwhl.main() == 0
    msg = " ".join(rec.getMessage() for rec in caplog.records)
    assert "explicit operator authorization" in msg
    assert "agent-executed" in msg
    assert "not a human-supervised run" in msg


def test_human_gate_clean_run_exits_0(monkeypatch):
    _patch(monkeypatch, _FakeResult(errors=0))
    monkeypatch.setattr(sys, "argv", _argv("--i-am-a-human-running-this-supervised"))
    assert rwhl.main() == 0


def test_completed_with_errors_exits_1(monkeypatch):
    _patch(monkeypatch, _FakeResult(errors=2))
    monkeypatch.setattr(sys, "argv", _argv("--i-am-a-human-running-this-supervised"))
    assert rwhl.main() == 1
