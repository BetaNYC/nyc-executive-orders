"""Offline tests for the gated GPP integration runner.

Exercises the runner's own logic — the authorization gate, the read-only dry-run,
and a gated merge into isolated scratch dirs (never the real repo). The merge math
itself is covered exhaustively in ``test_gpp.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

from nyc_executive_orders import config

import run_gpp_integration as runner

FIXTURES = Path(__file__).parent / "fixtures"


def inv_rec(gpp_id, title, dp, cr, fs, desc=""):
    return {"id": gpp_id, "t": title, "dp": dp, "cy": "", "cr": cr, "fs": fs,
            "desc": desc, "subj": "Government", "rt": "Executive Orders"}


def corp_rec(eo_id, mayor, is_em, number, year, pdf_path):
    return {
        "eo_id": eo_id, "number": number, "year": year, "is_emergency": is_em,
        "date_signed": f"{year}-01-01", "mayor": mayor, "administration": mayor,
        "admin_note": None, "title": f"Order {number}", "source": "live-nycgov",
        "source_pdf_url": None, "pdf_path": pdf_path,
        "supersedes": [], "superseded_by": [], "establishes_entity": None,
        "in_effect": None, "text_source": "born-digital" if pdf_path else "none",
        "page_count": 1 if pdf_path else None,
        "text_quality": "tier-1" if pdf_path else "no-text",
        "dropped_header": "", "dropped_marks": [],
        "full_text": "body" if pdf_path else "_No text available_",
        "full_text_raw": "body" if pdf_path else "_No text available_",
    }


def stage_pdf(staging_dir: Path, fileset_id: str):
    staging_dir.mkdir(parents=True, exist_ok=True)
    dest = staging_dir / config.GPP_STAGING_FILENAME_TEMPLATE.format(fileset_id=fileset_id)
    dest.write_bytes((FIXTURES / "born_digital_sample.pdf").read_bytes())
    return dest


def _tiny_inputs(tmp_path):
    """A minimal inventory + corpus + staging dir covering each disposition."""
    inv = [
        inv_rec("d1", "Emergency Executive Order 100", "2020-06-01", "Bill de Blasio", "fdual"),
        inv_rec("gc1", "Emergency Executive Order 290", "2022-12-21", "Eric Adams", "fgap"),
        inv_rec("nn1", "Emergency Executive Order 3.1", "2026-02-01", "Zohran Mamdani", "fnew"),
        inv_rec("v1", "Executive Orders and Memoranda", "1965-01-01", "John V. Lindsay", "fvol"),
    ]
    inv_path = tmp_path / "inventory.json"
    inv_path.write_text(json.dumps({"records": inv}), encoding="utf-8")

    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    corpus = [
        corp_rec("2020-EEO-100", "de Blasio", True, "100", 2020, "pdfs/2020/2020-EEO-100.pdf"),
        corp_rec("2022-EEO-290", "Adams", True, "290", 2022, None),
    ]
    (corpus_dir / "eo.json").write_text(json.dumps(corpus), encoding="utf-8")

    staging = tmp_path / "staging"
    for fs in ("fdual", "fgap", "fnew", "fvol"):
        stage_pdf(staging, fs)
    return inv_path, corpus_dir, staging


def _args(tmp_path, inv_path, corpus_dir, staging, *extra):
    return [
        "--inventory", str(inv_path),
        "--corpus-dir", str(corpus_dir),
        "--staging-dir", str(staging),
        "--sources-dir", str(tmp_path / "sources"),
        "--repo-root", str(tmp_path),
        "--report-path", str(tmp_path / "report.md"),
        *extra,
    ]


def test_gate_refuses_without_authorization(tmp_path, capsys):
    inv_path, corpus_dir, staging = _tiny_inputs(tmp_path)
    before = (corpus_dir / "eo.json").read_text()
    rc = runner.main(_args(tmp_path, inv_path, corpus_dir, staging, "--no-ocr"))
    assert rc == 2
    assert "REFUSING TO RUN" in capsys.readouterr().err
    # Nothing mutated.
    assert (corpus_dir / "eo.json").read_text() == before
    assert not (corpus_dir / config.GPP_PROVENANCE_JSON_NAME).exists()


def test_dry_run_writes_nothing(tmp_path, capsys):
    inv_path, corpus_dir, staging = _tiny_inputs(tmp_path)
    before = (corpus_dir / "eo.json").read_text()
    rc = runner.main(_args(tmp_path, inv_path, corpus_dir, staging, "--dry-run"))
    assert rc == 0
    out = capsys.readouterr().out
    assert "DRY-RUN" in out
    assert "net-new" in out
    assert (corpus_dir / "eo.json").read_text() == before
    assert not (corpus_dir / config.GPP_PROVENANCE_JSON_NAME).exists()
    assert not (tmp_path / "report.md").exists()


def test_operator_authorized_merge_writes(tmp_path, capsys):
    inv_path, corpus_dir, staging = _tiny_inputs(tmp_path)
    rc = runner.main(_args(tmp_path, inv_path, corpus_dir, staging,
                           "--no-ocr", "--operator-authorized"))
    assert rc == 0
    records = json.loads((corpus_dir / "eo.json").read_text())
    ids = {r["eo_id"] for r in records}
    # net-new minted, gap-closer got its pdf, dual untouched, volume parked.
    assert "2026-EEO-3.1" in ids                       # minted
    assert len(records) == 3                            # 2 existing + 1 mint
    gap = next(r for r in records if r["eo_id"] == "2022-EEO-290")
    assert gap["pdf_path"] == "pdfs/2022/2022-EEO-290.pdf"
    sidecar = json.loads((corpus_dir / config.GPP_PROVENANCE_JSON_NAME).read_text())
    assert "2020-EEO-100" in sidecar["orders"]          # dual recorded in sidecar
    assert (tmp_path / "sources" / "volumes.json").exists()
    assert (tmp_path / "report.md").exists()
    assert "WROTE:" in capsys.readouterr().out


def test_merge_is_resumable_and_idempotent(tmp_path):
    inv_path, corpus_dir, staging = _tiny_inputs(tmp_path)
    args = _args(tmp_path, inv_path, corpus_dir, staging, "--no-ocr", "--operator-authorized")
    assert runner.main(args) == 0
    eo1 = (corpus_dir / "eo.json").read_text()
    side1 = (corpus_dir / config.GPP_PROVENANCE_JSON_NAME).read_text()
    # Second run (the runner re-reads its own written corpus) is a no-op.
    assert runner.main(args) == 0
    assert (corpus_dir / "eo.json").read_text() == eo1
    assert (corpus_dir / config.GPP_PROVENANCE_JSON_NAME).read_text() == side1
