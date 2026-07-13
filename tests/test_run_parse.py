"""run_parse.py CLI: the OCR authorization gate + the ungated --no-ocr path.

The gate covers only the OCR path (long, deliberate). --no-ocr needs no
authorization. To keep these fast, the test index holds a single BORN-DIGITAL
record, so the authorized run passes the gate and completes without invoking OCR.
"""

from __future__ import annotations

import json

from run_parse import main


def _write_index(tmp_path, pdf_abspath):
    records = [{
        "eo_id": "2003-EO-005", "number": "005", "year": 2003,
        "is_emergency": False, "date_signed": "2003-04-01",
        "title": "Executive Order 5", "source": "live-nycgov",
        "source_pdf_url": "https://www.nyc.gov/x/eo-5.pdf",
        "pdf_path": str(pdf_abspath),  # absolute -> resolves regardless of repo root
    }]
    idx = tmp_path / "eo_index.json"
    idx.write_text(json.dumps(records), encoding="utf-8")
    return idx


def _args(tmp_path, idx, *extra):
    return [
        "--index", str(idx),
        "--corpus-dir", str(tmp_path / "corpus"),
        "--index-dir", str(tmp_path / "index"),
        *extra,
    ]


def test_ocr_path_refuses_without_authorization(tmp_path, born_digital_pdf):
    idx = _write_index(tmp_path, born_digital_pdf)
    rc = main(_args(tmp_path, idx))  # OCR is the default; no gate flag
    assert rc == 2
    # Refused before writing anything.
    assert not (tmp_path / "corpus").exists()


def test_no_ocr_runs_without_gate(tmp_path, born_digital_pdf):
    idx = _write_index(tmp_path, born_digital_pdf)
    rc = main(_args(tmp_path, idx, "--no-ocr"))
    assert rc == 0
    assert (tmp_path / "corpus" / "2003" / "2003-EO-005.md").exists()


def test_operator_authorized_passes_gate(tmp_path, born_digital_pdf):
    idx = _write_index(tmp_path, born_digital_pdf)
    rc = main(_args(tmp_path, idx, "--operator-authorized"))
    assert rc == 0
    assert (tmp_path / "corpus" / "2003" / "2003-EO-005.md").exists()


def test_human_flag_passes_gate(tmp_path, born_digital_pdf):
    idx = _write_index(tmp_path, born_digital_pdf)
    rc = main(_args(tmp_path, idx, "--i-am-a-human-running-this-supervised"))
    assert rc == 0
