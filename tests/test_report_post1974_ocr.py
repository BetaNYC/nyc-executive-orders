"""The post-1974 QA report: the baseline snapshot and the cutover gates.

Offline. Every input is a dict or a committed fixture record.
"""

from __future__ import annotations

import json
import shutil
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
# Baseline snapshot                                                             #
# --------------------------------------------------------------------------- #

def test_snapshot_captures_only_tesseract_records(tmp_path):
    records = [
        {"eo_id": "a", "text_source": "ocr", "full_text_raw": "hello world " * 20,
         "text_quality": "clean", "page_count": 1},
        {"eo_id": "b", "text_source": "born-digital", "full_text_raw": "x" * 100},
        {"eo_id": "c", "text_source": "ocr-skipped", "full_text_raw": ""},
    ]
    out = tmp_path / "baseline.json"
    payload = report.snapshot(records, out)

    assert set(payload) == {"a"}
    assert payload["a"]["chars"] == len("hello world " * 20)
    assert payload["a"]["text_quality"] == "clean"
    assert json.loads(out.read_text()) == payload


def test_snapshot_stores_metrics_not_a_second_copy_of_the_text(tmp_path):
    """~200 KB rather than megabytes: the text itself stays in git history."""
    records = [{"eo_id": "a", "text_source": "ocr", "full_text_raw": "secret " * 500}]
    payload = report.snapshot(records, tmp_path / "b.json")
    assert "secret" not in json.dumps(payload)
    assert set(payload["a"]) == {"chars", "word_ratio", "junk_ratio",
                                 "text_quality", "page_count"}


def test_snapshot_falls_back_to_full_text_when_raw_is_absent(tmp_path):
    records = [{"eo_id": "a", "text_source": "ocr", "full_text": "abc"}]
    assert report.snapshot(records, tmp_path / "b.json")["a"]["chars"] == 3


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


def test_the_67_orders_with_no_baseline_are_not_compared(tmp_path):
    """They never had Tesseract text; there is nothing to regress against."""
    seed(tmp_path, "1974-EO-001", 1974, "clean")
    lines, failures = report.stage_diff([cand()], tmp_path, {"other": {"chars": 1}},
                                        [], 0, tmp_path)
    assert failures == []
    assert "No document has both" in "\n".join(lines)


def test_a_missing_baseline_tells_you_to_snapshot_first(tmp_path):
    lines, failures = report.stage_diff([cand()], tmp_path, {}, [], 0, tmp_path)
    assert "--snapshot" in "\n".join(lines)
    assert failures == []


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
