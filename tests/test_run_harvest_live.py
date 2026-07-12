"""Exit-code contract for the supervised live runner (scripts/run_harvest_live.py).

Contract:
  * 0  — completed with zero errors
  * 1  — completed but result.errors > 0
  * 2  — missing BOTH authorization gates (refuses to run)

Two equally-strong gates authorize a run: --i-am-a-human-running-this-supervised
(human) or --operator-authorized (agent, under explicit in-session operator
authorization). Either alone proceeds; neither refuses (exit 2).

All offline: build_fetcher / run_harvest are monkeypatched so nothing touches
the network. (scripts/ is on the pytest pythonpath — see pyproject.toml.)
"""

from __future__ import annotations

import logging
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


def test_missing_both_gates_exits_2(monkeypatch):
    # Neither gate flag -> refuses before ever building a fetcher.
    monkeypatch.setattr(sys, "argv", _argv())
    called = {"harvest": False}
    monkeypatch.setattr(
        rhl, "run_harvest", lambda *a, **k: called.__setitem__("harvest", True)
    )
    assert rhl.main() == 2
    assert called["harvest"] is False


def test_operator_authorized_alone_proceeds_and_logs(monkeypatch, caplog):
    # The agent path: --operator-authorized alone authorizes the run, and a
    # truthful non-human provenance line is logged.
    _patch_harvest(monkeypatch, _FakeResult(errors=0))
    monkeypatch.setattr(sys, "argv", _argv("--operator-authorized"))
    with caplog.at_level(
        logging.INFO, logger="nyc_executive_orders.run_harvest_live"
    ):
        assert rhl.main() == 0
    msg = " ".join(rec.getMessage() for rec in caplog.records)
    assert "explicit operator authorization" in msg
    assert "agent-executed" in msg
    assert "not a human-supervised run" in msg


def test_operator_authorized_does_not_claim_human(monkeypatch, caplog):
    # The provenance line must never assert a human is running the harvest.
    _patch_harvest(monkeypatch, _FakeResult(errors=0))
    monkeypatch.setattr(sys, "argv", _argv("--operator-authorized"))
    with caplog.at_level(
        logging.INFO, logger="nyc_executive_orders.run_harvest_live"
    ):
        rhl.main()
    msg = " ".join(rec.getMessage() for rec in caplog.records)
    assert "human-supervised run" not in msg.replace("not a human-supervised run", "")


def test_clean_run_exits_0(monkeypatch):
    _patch_harvest(monkeypatch, _FakeResult(errors=0))
    monkeypatch.setattr(sys, "argv", _argv("--i-am-a-human-running-this-supervised"))
    assert rhl.main() == 0


def test_completed_with_errors_exits_1(monkeypatch):
    _patch_harvest(monkeypatch, _FakeResult(errors=3))
    monkeypatch.setattr(sys, "argv", _argv("--i-am-a-human-running-this-supervised"))
    assert rhl.main() == 1
