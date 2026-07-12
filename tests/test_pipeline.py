"""Dry-run pipeline wrapper (scripts/run_pipeline.py) — offline.

The per-step invocation (`run_step`) is mocked to return controllable
(exit_code, errors) results so no subprocess is spawned and no network is
touched. Assertions cover the four behaviours in the spec:

  * advances to the next step on a clean step;
  * STOPS on a failing step and does NOT invoke later steps (non-zero rc);
  * after all steps pass, HALTS before download (prints the manual command,
    rc 0, never calls any download path);
  * refuses without the human flag (exit 2).

scripts/ is on the pytest pythonpath (see pyproject.toml).
"""

from __future__ import annotations

import run_pipeline as rp

HUMAN_FLAG = rp.HUMAN_FLAG


def _steps(n: int = 2) -> list[rp.PipelineStep]:
    return [
        rp.PipelineStep(label=f"step-{i}", from_year=2020 + i, to_year=2020 + i)
        for i in range(1, n + 1)
    ]


class _RecordingRunner:
    """Fake step runner: returns preset (exit_code, errors) results in order.

    Records every step it was asked to run so tests can assert which steps ran.
    """

    def __init__(self, outcomes: list[tuple[int, int]]):
        self._outcomes = list(outcomes)
        self.calls: list[rp.PipelineStep] = []

    def __call__(self, step: rp.PipelineStep) -> rp.StepResult:
        self.calls.append(step)
        exit_code, errors = self._outcomes.pop(0)
        # Give each a small non-zero enumerated so the inventory line is realistic.
        return rp.StepResult(
            step=step, exit_code=exit_code, enumerated=5, resolved=5, errors=errors
        )


def test_advances_through_all_clean_steps(capsys):
    steps = _steps(2)
    runner = _RecordingRunner([(0, 0), (0, 0)])
    rc, results = rp.run_pipeline(steps, runner=runner)

    assert rc == 0
    assert len(runner.calls) == 2  # both steps ran, in order
    assert [s.label for s in runner.calls] == ["step-1", "step-2"]
    assert all(r.clean for r in results)


def test_stops_on_first_failing_step_and_skips_later(capsys):
    steps = _steps(2)
    # First step fails (exit 1, errors 3); second must never run.
    runner = _RecordingRunner([(1, 3), (0, 0)])
    rc, results = rp.run_pipeline(steps, runner=runner)

    assert rc == 1
    assert len(runner.calls) == 1  # later step NOT invoked
    assert results[-1].step.label == "step-1"
    err = capsys.readouterr().err
    assert "PIPELINE STOPPED at step 1/2" in err
    assert "Later steps were NOT run" in err


def test_stops_when_middle_step_errors_despite_exit_zero(capsys):
    # A step can exit 0 but report errors>0 (not clean) — must still STOP.
    steps = _steps(2)
    runner = _RecordingRunner([(0, 0), (0, 4)])
    rc, results = rp.run_pipeline(steps, runner=runner)

    assert rc == 1  # coerced non-zero even though the step's exit_code was 0
    assert len(runner.calls) == 2
    assert results[-1].errors == 4


def test_halts_before_download_after_all_clean(capsys):
    steps = _steps(2)
    runner = _RecordingRunner([(0, 0), (0, 0)])
    rc, _ = rp.run_pipeline(steps, runner=runner)

    out = capsys.readouterr().out
    assert rc == 0
    # Consolidated inventory printed...
    assert "Dry-run inventory (all steps clean)" in out
    # ...and the HALT message with the exact MANUAL download command (spanning the
    # widest range: 2021..2022 for _steps(2)), never auto-invoked.
    assert "run the download yourself" in out
    assert (
        f"uv run python scripts/run_harvest_live.py "
        f"--from-year 2021 --to-year 2022 {HUMAN_FLAG}" in out
    )


def test_download_command_uses_widest_range():
    steps = [
        rp.PipelineStep(label="a", from_year=2024, to_year=2024),
        rp.PipelineStep(label="b", from_year=2022, to_year=2026),
    ]
    cmd = rp._download_command(steps)
    assert "--from-year 2022 --to-year 2026" in cmd
    assert "--dry-run" not in cmd  # the manual command actually downloads


def test_default_sequence_is_the_documented_two_steps():
    labels = [s.label for s in rp.DEFAULT_STEPS]
    ranges = [(s.from_year, s.to_year) for s in rp.DEFAULT_STEPS]
    assert ranges == [(2024, 2024), (2022, 2026)]
    assert "2024" in labels[0]
    assert "full current-era inventory" in labels[1]


def test_main_refuses_without_human_flag(monkeypatch, capsys):
    # run_step must never be called when the flag is absent.
    def _boom(*_a, **_k):  # pragma: no cover - only fires on regression
        raise AssertionError("run_step called without the human flag")

    monkeypatch.setattr(rp, "run_step", _boom)
    rc = rp.main([])
    assert rc == 2
    assert "REFUSING TO RUN" in capsys.readouterr().err


def test_main_runs_default_sequence_with_flag(monkeypatch, capsys):
    # Patch the module-level step call; main() drives the whole default sequence.
    runner = _RecordingRunner([(0, 0), (0, 0)])
    monkeypatch.setattr(rp, "run_step", runner)
    rc = rp.main([HUMAN_FLAG])
    assert rc == 0
    assert len(runner.calls) == len(rp.DEFAULT_STEPS)
    out = capsys.readouterr().out
    assert "run the download yourself" in out


def test_parse_summary_reads_live_runner_line():
    line = (
        "\nDone: enumerated=42 pdf_resolved=40 downloaded=0 cached=0 errors=2\n"
    )
    assert rp._parse_summary(line) == (42, 40, 2)


def test_parse_summary_missing_line_returns_zeros():
    assert rp._parse_summary("no summary here") == (0, 0, 0)
