"""The post-1974 QA report: the git-read baseline and the cutover gates.

Offline. Every input is a dict, a committed fixture record, or a throwaway git
repository built in tmp_path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import report_post1974_ocr as report  # noqa: E402
from nyc_executive_orders.vlm_corpus import STATUS_SCANNED, Candidate  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
POST1974 = FIXTURES / "post1974"


def seed(ocr_root: Path, eo_id: str, year: int, *pages: str) -> None:
    d = ocr_root / str(year) / eo_id
    d.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(pages, start=1):
        shutil.copy(POST1974 / f"{name}.json", d / f"page_{i:04d}.json")


def cand(eo_id="1974-EO-001", year=1974, pages=1):
    return Candidate(eo_id, year, Path("/x.pdf"), STATUS_SCANNED, page_count=pages)


# --------------------------------------------------------------------------- #
# Baseline — read out of git, never from the working tree                       #
# --------------------------------------------------------------------------- #

def test_baseline_captures_only_tesseract_records():
    records = [
        {"eo_id": "a", "text_source": "ocr", "full_text_raw": "hello world " * 20,
         "text_quality": "clean", "page_count": 1},
        {"eo_id": "b", "text_source": "born-digital", "full_text_raw": "x" * 100},
        {"eo_id": "c", "text_source": "ocr-skipped", "full_text_raw": ""},
    ]
    payload = report.baseline_from_records(records)

    assert set(payload) == {"a"}
    assert payload["a"]["chars"] == len("hello world " * 20)
    assert payload["a"]["text_quality"] == "clean"


def test_baseline_keeps_the_text_because_the_sample_diffs_need_it():
    records = [{"eo_id": "a", "text_source": "ocr", "full_text_raw": "secret " * 500}]
    payload = report.baseline_from_records(records)
    assert payload["a"]["text"] == "secret " * 500


def test_baseline_falls_back_to_full_text_when_raw_is_absent():
    records = [{"eo_id": "a", "text_source": "ocr", "full_text": "abc"}]
    assert report.baseline_from_records(records)["a"]["chars"] == 3


def git_repo(tmp_path, monkeypatch, *commits) -> Path:
    """A throwaway repo whose eo.json is committed once per `commits` entry."""
    run = lambda *a: subprocess.run(a, cwd=tmp_path, check=True,
                                    capture_output=True, text=True)
    run("git", "init", "-q")
    run("git", "config", "user.email", "t@t")
    run("git", "config", "user.name", "t")
    path = tmp_path / "eo.json"
    for i, records in enumerate(commits):
        path.write_text(json.dumps(records))
        run("git", "add", "eo.json")
        run("git", "commit", "-qm", f"c{i}")
    monkeypatch.setattr(report, "REPO_ROOT", tmp_path)
    return path


def test_the_baseline_is_the_newest_commit_that_predates_the_rebuild(tmp_path,
                                                                    monkeypatch):
    """THE BUG: after a rebuild the working tree holds the VLM text, so reading
    the old side from it puts the same text on both sides of every sample diff."""
    tesseract = [{"eo_id": "a", "text_source": "ocr", "full_text_raw": "old text here"}]
    rebuilt = [{"eo_id": "a", "text_source": "ocr-vlm", "full_text_raw": "new text"}]
    path = git_repo(tmp_path, monkeypatch, tesseract, rebuilt)

    baseline, label = report.load_baseline(path)

    assert baseline["a"]["text"] == "old text here"
    assert label.endswith(":eo.json")


def test_the_second_cutover_finds_its_own_baseline(tmp_path, monkeypatch):
    """The rule used to be "the newest revision in which NO record says ocr-vlm",
    which only ever worked once. Here `b` is the second cutover's population: it
    was born-digital before the gate could see it was a scan, became ocr-layer
    when it could, and is now being replaced by the VLM. The old rule walked past
    the ocr-layer commit --- because `a` already said ocr-vlm there --- landed on
    a revision where `b` said born-digital, found nothing tagged ocr-layer, and
    returned an EMPTY baseline. stage_diff then compared nothing and exited 0.
    """
    pre = [{"eo_id": "a", "text_source": "ocr", "full_text_raw": "tesseract text"},
           {"eo_id": "b", "text_source": "born-digital", "full_text_raw": "mislabelled"}]
    first_cutover = [
        {"eo_id": "a", "text_source": "ocr-vlm", "full_text_raw": "vlm text"},
        {"eo_id": "b", "text_source": "ocr-layer", "full_text_raw": "the overlay text"}]
    path = git_repo(tmp_path, monkeypatch, pre, first_cutover)

    baseline, _label = report.load_baseline(path, sources=frozenset({"ocr-layer"}))

    assert baseline["b"]["text"] == "the overlay text"
    assert "a" not in baseline, "only the population under test is the old side"


def test_each_cutover_reads_its_own_source_tag(tmp_path, monkeypatch):
    """The same history, asked about the FIRST cutover, still answers correctly."""
    pre = [{"eo_id": "a", "text_source": "ocr", "full_text_raw": "tesseract text"}]
    after = [{"eo_id": "a", "text_source": "ocr-vlm", "full_text_raw": "vlm text"}]
    path = git_repo(tmp_path, monkeypatch, pre, after)

    baseline, _label = report.load_baseline(path)

    assert baseline["a"]["text"] == "tesseract text"


def test_baseline_sources_selects_which_records_are_the_old_side():
    records = [{"eo_id": "a", "text_source": "ocr", "full_text_raw": "x" * 40},
               {"eo_id": "b", "text_source": "ocr-layer", "full_text_raw": "y" * 40}]
    assert set(report.baseline_from_records(records)) == {"a"}
    assert set(report.baseline_from_records(
        records, frozenset({"ocr-layer"}))) == {"b"}


def test_an_explicit_revision_wins_over_the_search(tmp_path, monkeypatch):
    tesseract = [{"eo_id": "a", "text_source": "ocr", "full_text_raw": "first"}]
    later = [{"eo_id": "a", "text_source": "ocr", "full_text_raw": "second"}]
    path = git_repo(tmp_path, monkeypatch, tesseract, later)
    first = subprocess.run(["git", "log", "--format=%H", "--reverse"], cwd=tmp_path,
                           capture_output=True, text=True).stdout.split()[0]

    baseline, _label = report.load_baseline(path, rev=first)

    assert baseline["a"]["text"] == "first"


def test_no_revision_holding_the_old_text_yields_no_baseline(tmp_path, monkeypatch):
    """Nothing in this history was ever tagged `ocr`, so there is no old side.
    stage_diff turns that into a hard failure rather than a quiet skip."""
    rebuilt = [{"eo_id": "a", "text_source": "ocr-vlm", "full_text_raw": "new"}]
    path = git_repo(tmp_path, monkeypatch, rebuilt)
    assert report.load_baseline(path) == ({}, "")


def test_outside_a_git_repository_the_baseline_is_simply_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPO_ROOT", tmp_path)
    assert report.load_baseline(tmp_path / "eo.json") == ({}, "")


# --------------------------------------------------------------------------- #
# Stage 0 — the calibration oracle                                              #
# --------------------------------------------------------------------------- #

def write_classify(ocr_root: Path, eo_id: str, year: int, blanks: list[bool]) -> None:
    d = ocr_root / str(year) / eo_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "classify_report.json").write_text(json.dumps({
        "model_classifier": "ink-stats",
        "params": {"dark_pixel_threshold": 100, "ink_roi_margin": 0.12,
                   "min_dark_fraction": 0.001, "min_contrast_std": 6.5},
        "pages": [{"page": i, "blank": b,
                   "page_stats": {"mean": 180.0, "std": 30.0, "dark_fraction": 0.02}}
                  for i, b in enumerate(blanks, start=1)],
    }))


def test_a_blank_page_in_a_document_that_has_text_is_a_proven_false_blank(tmp_path):
    """A blank page cannot have produced 900 characters. This is the whole gate."""
    write_classify(tmp_path, "1974-EO-001", 1974, [True])
    baseline = {"1974-EO-001": {"chars": 900}}

    lines, failures = report.stage_classify([cand()], tmp_path, baseline)

    assert failures, "a proven false blank must block cutover"
    assert "PROVEN FALSE" in "\n".join(lines)


def test_a_blank_page_in_a_document_with_no_prior_text_is_only_plausible(tmp_path):
    write_classify(tmp_path, "1974-EO-001", 1974, [True])
    lines, failures = report.stage_classify([cand()], tmp_path, baseline={})
    assert failures == []
    assert "plausible" in "\n".join(lines)


def test_no_blanks_at_all_passes_the_gate(tmp_path):
    write_classify(tmp_path, "1974-EO-001", 1974, [False, False])
    lines, failures = report.stage_classify([cand(pages=2)], tmp_path, {})
    assert failures == []
    assert "Gate passed" in "\n".join(lines)


def test_missing_classify_reports_say_so_rather_than_passing_silently(tmp_path):
    lines, failures = report.stage_classify([cand()], tmp_path, {})
    assert failures == []
    assert "--classify-blank-only" in "\n".join(lines)


# --------------------------------------------------------------------------- #
# Stage 1 — coverage                                                            #
# --------------------------------------------------------------------------- #

def test_records_that_yielded_no_text_are_a_hard_failure(tmp_path):
    seed(tmp_path, "1974-EO-001", 1974, "parse_error")
    _lines, failures = report.stage_ocr([cand()], tmp_path)
    assert any("no usable text" in f for f in failures)


def test_a_document_with_no_records_is_simply_not_yet_done(tmp_path):
    """Mid-run is a normal state, not a failure."""
    _lines, failures = report.stage_ocr([cand()], tmp_path)
    assert failures == []


def test_qa_flags_are_counted(tmp_path):
    seed(tmp_path, "1974-EO-001", 1974, "truncated")
    lines, _f = report.stage_ocr([cand()], tmp_path)
    assert "`truncated`" in "\n".join(lines)


# --------------------------------------------------------------------------- #
# Stage 2 — the cutover gate                                                    #
# --------------------------------------------------------------------------- #

def test_losing_text_that_used_to_exist_blocks_cutover(tmp_path):
    seed(tmp_path, "1974-EO-001", 1974, "parse_error")
    baseline = {"1974-EO-001": {"chars": 4000, "word_ratio": 0.95,
                                "junk_ratio": 0.01, "text_quality": "clean"}}
    _lines, failures = report.stage_diff([cand()], tmp_path, baseline, [], 0, tmp_path)
    assert any("VLM produced" in f for f in failures)


def test_an_improved_document_passes(tmp_path):
    seed(tmp_path, "1974-EO-001", 1974, "clean")
    baseline = {"1974-EO-001": {"chars": 260, "word_ratio": 0.80,
                                "junk_ratio": 0.05, "text_quality": "minor-noise"}}
    lines, failures = report.stage_diff([cand()], tmp_path, baseline,
                                        [{"eo_id": "1974-EO-001"}], 0, tmp_path)
    assert failures == []
    assert "1/1" in "\n".join(lines)


def test_an_order_with_no_baseline_is_skipped_not_failed(tmp_path):
    """The 67 orders that never had Tesseract text have nothing to regress
    against, so they drop out of the comparison — while the documents that DO
    have a baseline are still compared and still gate the cutover."""
    seed(tmp_path, "1974-EO-001", 1974, "clean")
    seed(tmp_path, "1974-EO-002", 1974, "clean")
    baseline = {"1974-EO-001": {"chars": 260, "word_ratio": 0.80, "junk_ratio": 0.05,
                                "text_quality": "minor-noise", "text": "OLD TEXT"}}
    lines, failures = report.stage_diff(
        [cand(), cand(eo_id="1974-EO-002")], tmp_path, baseline,
        [{"eo_id": "1974-EO-001"}, {"eo_id": "1974-EO-002"}], 0, tmp_path)
    assert failures == []
    assert "1/1" in "\n".join(lines)          # one compared, the other skipped


def test_comparing_nothing_is_a_failure_not_a_pass(tmp_path):
    """A gate that compared no document must not report success. It used to
    return prose and no failure, so main() exited 0 --- and after the first
    cutover load_baseline could no longer find the second cutover's population
    in its old form, so this was the state a real run would have landed in."""
    seed(tmp_path, "1974-EO-001", 1974, "clean")
    lines, failures = report.stage_diff([cand()], tmp_path, {"other": {"chars": 1}},
                                        [], 0, tmp_path)
    assert failures, "an empty comparison must block the cutover"
    assert "compared nothing" in "\n".join(lines)


def test_a_missing_baseline_tells_you_to_name_a_revision_and_fails(tmp_path):
    lines, failures = report.stage_diff([cand()], tmp_path, {}, [], 0, tmp_path)
    assert "--baseline-rev" in "\n".join(lines)
    assert failures, "no baseline means the gate compared nothing"


def test_a_sample_diff_shows_the_old_text_not_the_new_text_twice(tmp_path):
    """THE BUG, at the point it was visible: both blocks held the VLM text."""
    seed(tmp_path / "ocr", "1974-EO-001", 1974, "clean")
    baseline = {"1974-EO-001": {"chars": 260, "word_ratio": 0.80, "junk_ratio": 0.05,
                                "text_quality": "minor-noise",
                                "text": "TESSERACT ONLY LINE"}}
    samples = tmp_path / "samples"

    report.stage_diff([cand()], tmp_path / "ocr", baseline,
                      [{"eo_id": "1974-EO-001"}], 1, samples)

    written = (samples / "1974-EO-001.md").read_text()
    assert "TESSERACT ONLY LINE" in written
    assert "-TESSERACT ONLY LINE" in written      # it really reached the diff


# --------------------------------------------------------------------------- #
# Metrics                                                                       #
# --------------------------------------------------------------------------- #

def test_metrics_are_the_three_numbers_clean_tiers_on():
    text = "The Mayor of the City of New York hereby orders."
    m = report.metrics(text)
    assert set(m) == {"chars", "word_ratio", "junk_ratio"}
    assert m["chars"] == len(text)
    assert m["word_ratio"] > 0.9
    assert m["junk_ratio"] < 0.05


def test_quantile_helper_is_safe_on_an_empty_list():
    assert report._q([], 0.5) == 0.0
    assert report._q([1.0, 2.0, 3.0], 0.5) == 2.0
