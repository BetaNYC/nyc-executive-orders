"""--classify-blank-only: no model, but its verdicts must land on disk.

Before this, the CLI printed each page's blank/keep verdict to stdout and
exited -- nothing was written, so a calibration pass over a new volume left no
trace once the terminal scrolled. These tests exercise the real CLI path
(argparse + main()) against the tiny committed scanned fixture, which needs no
network and no `vlm` extra (render-only, ink-stats only).
"""

from __future__ import annotations

import json
import sys

from nyc_executive_orders import vlm_ocr


def _run_classify_only(monkeypatch, pdf_path, out_dir, extra_args=()):
    argv = [
        "vlm_ocr",
        str(pdf_path),
        "--output-dir",
        str(out_dir),
        "--classify-blank-only",
        "--dpi",
        "100",
        *extra_args,
    ]
    monkeypatch.setattr(sys, "argv", argv)
    vlm_ocr.main()


def test_classify_only_writes_report_with_expected_shape(monkeypatch, tmp_path, scanned_pdf):
    out_dir = tmp_path / "out"
    _run_classify_only(monkeypatch, scanned_pdf, out_dir)

    # Beside the page records (--json-dir, defaulting to out_dir/json), not in
    # the scratch root: it is a durable finding about the volume.
    report_path = out_dir / "json" / "classify_report.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text())

    assert report["model_classifier"] == "ink-stats"
    assert set(report["params"]) == {
        "dark_pixel_threshold",
        "ink_roi_margin",
        "min_dark_fraction",
        "min_contrast_std",
    }
    assert len(report["pages"]) == 1
    page = report["pages"][0]
    assert page["page"] == 1
    assert set(page["page_stats"]) == {"mean", "std", "dark_fraction"}
    # The fixture has real rendered text on it -- it must not be classified blank.
    assert page["blank"] is False

    # classify-blank-only never loads a model or runs OCR: no page JSON, no
    # overlays, no memory profile.
    assert list((out_dir / "json").glob("page_*.json")) == []
    assert list((out_dir / "overlays").glob("page_*.png")) == []
    assert not (out_dir / "memory_profile.json").exists()


def test_classify_only_resume_merges_into_existing_report(monkeypatch, tmp_path, scanned_pdf):
    out_dir = tmp_path / "out"
    _run_classify_only(monkeypatch, scanned_pdf, out_dir)

    # Re-run over the same (single-page) range with --start-page: the merge
    # path must key by page number rather than duplicating or truncating.
    _run_classify_only(monkeypatch, scanned_pdf, out_dir, extra_args=["--start-page", "1"])

    report = json.loads((out_dir / "json" / "classify_report.json").read_text())
    assert [p["page"] for p in report["pages"]] == [1]
