"""Authorization gate + exit-code contract for scripts/run_gap_recovery_live.py.

Contract (parity with the Phase A / Phase B runners):
  * 0  — completed with zero errors
  * 1  — completed but result.errors > 0
  * 2  — missing BOTH authorization gates (refuses to run)

Two equally-strong gates: --i-am-a-human-running-this-supervised (human) or
--operator-authorized (agent, under explicit in-session operator authorization).

All offline: _build_client / run_gap_recovery are monkeypatched so nothing
touches the network, and the client is never built when the gate refuses.
(scripts/ is on the pytest pythonpath — see pyproject.toml.)
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field

import run_gap_recovery_live as rgrl


@dataclass
class _FakeResult:
    gaps_total: int = 1
    recovered: int = 1
    cached: int = 0
    would_recover: int = 0
    unrecoverable: int = 0
    errors: int = 0
    outcomes: list = field(default_factory=list)
    output_paths: dict = field(default_factory=dict)


def _argv(*extra: str) -> list[str]:
    return ["run_gap_recovery_live.py", "--dry-run", *extra]


def _patch(monkeypatch, result: _FakeResult) -> None:
    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(rgrl, "_build_client", lambda: _Client())
    monkeypatch.setattr(rgrl, "run_gap_recovery", lambda *a, **k: result)


def test_missing_both_gates_exits_2(monkeypatch):
    monkeypatch.setattr(sys, "argv", _argv())
    called = {"built": False, "recover": False}
    monkeypatch.setattr(rgrl, "_build_client", lambda: called.__setitem__("built", True))
    monkeypatch.setattr(
        rgrl, "run_gap_recovery", lambda *a, **k: called.__setitem__("recover", True)
    )
    assert rgrl.main() == 2
    assert called["built"] is False
    assert called["recover"] is False


def test_operator_authorized_alone_proceeds_and_logs(monkeypatch, caplog):
    _patch(monkeypatch, _FakeResult(errors=0))
    monkeypatch.setattr(sys, "argv", _argv("--operator-authorized"))
    with caplog.at_level(logging.INFO, logger="nyc_executive_orders.run_gap_recovery_live"):
        assert rgrl.main() == 0
    msg = " ".join(rec.getMessage() for rec in caplog.records)
    assert "explicit operator authorization" in msg
    assert "agent-executed" in msg
    assert "not a human-supervised run" in msg


def test_human_gate_clean_run_exits_0(monkeypatch):
    _patch(monkeypatch, _FakeResult(errors=0))
    monkeypatch.setattr(sys, "argv", _argv("--i-am-a-human-running-this-supervised"))
    assert rgrl.main() == 0


def test_completed_with_errors_exits_1(monkeypatch):
    _patch(monkeypatch, _FakeResult(errors=2, unrecoverable=2))
    monkeypatch.setattr(sys, "argv", _argv("--i-am-a-human-running-this-supervised"))
    assert rgrl.main() == 1
