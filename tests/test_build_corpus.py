"""End-to-end corpus emit: frontmatter, bodies, stubs, bulk json, manifest."""

from __future__ import annotations

import json
import shutil

import pytest
import yaml

from nyc_executive_orders.build_corpus import (
    NO_TEXT_STUB,
    TEXT_QUALITY_NO_TEXT,
    TEXT_SOURCE_NONE,
    TEXT_SOURCE_OCR,
    TEXT_SOURCE_OCR_SKIPPED,
    FRONTMATTER_FIELDS,
    _run_clean_stage,
    build_corpus,
    render_markdown,
    parse_record,
)
from nyc_executive_orders.extract import TEXT_SOURCE_BORN_DIGITAL


def _records():
    """Three synthetic index rows: born-digital, scanned, and a no-PDF gap."""
    return [
        {
            "eo_id": "2003-EO-005", "number": "005", "year": 2003,
            "is_emergency": False, "date_signed": "2003-04-01",
            "title": "Executive Order 5", "source": "live-nycgov",
            "source_pdf_url": "https://www.nyc.gov/x/eo-5.pdf",
            "pdf_path": "pdfs/2003/2003-EO-005.pdf",
        },
        {
            "eo_id": "1975-EO-010", "number": "010", "year": 1975,
            "is_emergency": False, "date_signed": "1975-06-01",
            "title": "Executive Order 10", "source": "wayback",
            "source_pdf_url": "https://web.archive.org/x/1975EO010.pdf",
            "pdf_path": "pdfs/1975/1975-EO-010.pdf",
        },
        {
            "eo_id": "2022-EEO-290", "number": "290", "year": 2022,
            "is_emergency": True, "date_signed": "2022-12-21",
            "title": "Emergency Executive Order 290", "source": "live-nycgov",
            "source_pdf_url": "https://nyc-csg-web.csc.nycnet/x/eeo-290.pdf",
            "pdf_path": None,  # the 53-gap case: recorded but no PDF on disk
        },
    ]


def _setup_repo(tmp_path, born_digital_pdf, scanned_pdf):
    """Lay out a temp repo_root with the fixtures at the records' pdf_paths."""
    (tmp_path / "pdfs" / "2003").mkdir(parents=True)
    (tmp_path / "pdfs" / "1975").mkdir(parents=True)
    shutil.copy(born_digital_pdf, tmp_path / "pdfs" / "2003" / "2003-EO-005.pdf")
    shutil.copy(scanned_pdf, tmp_path / "pdfs" / "1975" / "1975-EO-010.pdf")


def test_no_ocr_build(tmp_path, born_digital_pdf, scanned_pdf):
    _setup_repo(tmp_path, born_digital_pdf, scanned_pdf)
    corpus = tmp_path / "corpus"
    index = tmp_path / "index"

    result = build_corpus(
        _records(), repo_root=tmp_path, corpus_dir=corpus, index_dir=index,
        do_ocr=False,
    )

    assert result.total == 3
    assert result.by_text_source[TEXT_SOURCE_BORN_DIGITAL] == 1
    assert result.by_text_source[TEXT_SOURCE_OCR_SKIPPED] == 1
    assert result.by_text_source[TEXT_SOURCE_NONE] == 1

    # Born-digital md: valid frontmatter, locked field set, real body.
    md = (corpus / "2003" / "2003-EO-005.md").read_text()
    assert md.startswith("---\n")
    front_block = md.split("---\n")[1]
    front = yaml.safe_load(front_block)
    assert list(front.keys()) == FRONTMATTER_FIELDS
    assert front["eo_id"] == "2003-EO-005"
    assert front["mayor"] == "Bloomberg"      # 2003 -> Bloomberg
    assert front["admin_note"] is None
    assert front["text_source"] == TEXT_SOURCE_BORN_DIGITAL
    assert front["supersedes"] == []
    assert front["in_effect"] is None
    assert "OVERSIGHT" in md  # body present

    # Scanned under --no-ocr: stub body, ocr-skipped provenance.
    md_scan = (corpus / "1975" / "1975-EO-010.md").read_text()
    front_scan = yaml.safe_load(md_scan.split("---\n")[1])
    assert front_scan["text_source"] == TEXT_SOURCE_OCR_SKIPPED
    assert front_scan["mayor"] == "Beame"     # 1975 -> Beame
    assert NO_TEXT_STUB in md_scan

    # Gap EO (no PDF): stub, text_source none.
    md_gap = (corpus / "2022" / "2022-EEO-290.md").read_text()
    front_gap = yaml.safe_load(md_gap.split("---\n")[1])
    assert front_gap["text_source"] == TEXT_SOURCE_NONE
    assert front_gap["page_count"] is None
    assert NO_TEXT_STUB in md_gap

    # Bulk json shape.
    bulk = json.loads((corpus / "eo.json").read_text())
    assert len(bulk) == 3
    assert all("full_text" in r for r in bulk)
    assert all(all(f in r for f in FRONTMATTER_FIELDS) for r in bulk)

    # Manifest.
    manifest = (corpus / "manifest.csv").read_text()
    assert "2003-EO-005" in manifest
    assert TEXT_SOURCE_BORN_DIGITAL in manifest

    # Probe report side output.
    report = json.loads((index / "textlayer_report.json").read_text())
    assert report["summary"]["text"] == 1
    assert report["summary"]["scanned"] == 1


def test_year_and_limit_filters(tmp_path, born_digital_pdf, scanned_pdf):
    _setup_repo(tmp_path, born_digital_pdf, scanned_pdf)
    result = build_corpus(
        _records(), repo_root=tmp_path, corpus_dir=tmp_path / "c",
        index_dir=tmp_path / "i", do_ocr=False, year=2003,
    )
    assert result.total == 1
    assert result.by_text_source.get(TEXT_SOURCE_BORN_DIGITAL) == 1


def test_build_is_idempotent(tmp_path, born_digital_pdf, scanned_pdf):
    _setup_repo(tmp_path, born_digital_pdf, scanned_pdf)
    kw = dict(repo_root=tmp_path, corpus_dir=tmp_path / "corpus",
              index_dir=tmp_path / "index", do_ocr=False)
    build_corpus(_records(), **kw)
    first = (tmp_path / "corpus" / "eo.json").read_text()
    build_corpus(_records(), **kw)  # re-run
    assert (tmp_path / "corpus" / "eo.json").read_text() == first


def test_render_markdown_valid_yaml(tmp_path, born_digital_pdf):
    (tmp_path / "pdfs" / "2003").mkdir(parents=True)
    shutil.copy(born_digital_pdf, tmp_path / "pdfs" / "2003" / "2003-EO-005.pdf")
    parsed = parse_record(
        _records()[0], repo_root=tmp_path, do_ocr=False, ocr_config=None,
    )
    md = render_markdown(parsed)
    # Round-trips through a YAML parser without error.
    front = yaml.safe_load(md.split("---\n")[1])
    assert front["title"] == "Executive Order 5"


# --------------------------------------------------------------------------- #
# Clean-stage wiring (offline, no PDFs) — the integration point added to the
# pipeline. These exercise _run_clean_stage directly with documented OCR shapes.
# --------------------------------------------------------------------------- #

def _bare_record(eo_id, year, **over):
    r = {"eo_id": eo_id, "number": eo_id.split("-")[-1], "year": year,
         "is_emergency": False, "date_signed": None, "title": "",
         "source": "wayback", "source_pdf_url": "", "pdf_path": None}
    r.update(over)
    return r


def test_clean_stage_ocr_trims_header_and_fills_metadata():
    body = (
        "routing stamp junk line\n"
        "OFFICE OF THE MAYOR\n"
        "EXECUTIVE ORDER NO. 64\n"
        "July 26, 1976\n"
        "ESTABLISHMENT OF THE MAYOR'S MIDTOWN ACTION OFFICE\n"
        "Whereas, the offices had broad objectives; and\n"
    )
    out = _run_clean_stage(_bare_record("1976-EO-064", 1976), body,
                           text_source=TEXT_SOURCE_OCR, year=1976)
    assert "routing stamp junk" in out["dropped_header"]
    assert "routing stamp junk" not in out["body"]
    assert out["body"].startswith("OFFICE OF THE MAYOR")
    assert out["raw_body"] == body                    # verbatim preserved
    assert out["title"] == "ESTABLISHMENT OF THE MAYOR'S MIDTOWN ACTION OFFICE"
    assert out["date_signed"] == "1976-07-26"
    assert out["text_quality"] in ("clean", "minor-noise")


def test_clean_stage_born_digital_body_is_byte_identical():
    body = (
        "THE CITY OF NEW YORK\n"
        "OFFICE OF THE MAYOR\n"
        "EXECUTIVE ORDER NO. 5\n"
        "April 1, 2003\n"
        "REAL SUBJECT LINE OF THIS ORDER\n"
        "WHEREAS the following is ordered;\n"
    )
    out = _run_clean_stage(_bare_record("2003-EO-005", 2003), body,
                           text_source=TEXT_SOURCE_BORN_DIGITAL, year=2003)
    assert out["body"] == body                        # byte-for-byte unchanged
    assert out["dropped_header"] == ""
    assert out["dropped_marks"] == []
    # An empty title/date is still gap-filled from the body.
    assert out["title"] == "REAL SUBJECT LINE OF THIS ORDER"
    assert out["date_signed"] == "2003-04-01"


def test_clean_stage_existing_metadata_not_overwritten():
    body = "EXECUTIVE ORDER NO. 5\nApril 1, 2003\nSOME CAPS LINE\nWHEREAS x;\n"
    out = _run_clean_stage(
        _bare_record("2003-EO-005", 2003, title="Real Title", date_signed="2003-01-02"),
        body, text_source=TEXT_SOURCE_BORN_DIGITAL, year=2003)
    assert out["title"] == "Real Title"
    assert out["date_signed"] == "2003-01-02"


def test_clean_stage_no_text_stub_not_cleaned():
    out = _run_clean_stage(_bare_record("2022-EEO-290", 2022), NO_TEXT_STUB,
                           text_source=TEXT_SOURCE_NONE, year=2022)
    assert out["body"] == NO_TEXT_STUB
    assert out["text_quality"] == TEXT_QUALITY_NO_TEXT
    assert out["dropped_header"] == "" and out["dropped_marks"] == []


def test_corpus_carries_clean_provenance_and_raw(tmp_path, born_digital_pdf, scanned_pdf):
    _setup_repo(tmp_path, born_digital_pdf, scanned_pdf)
    build_corpus(_records(), repo_root=tmp_path, corpus_dir=tmp_path / "corpus",
                 index_dir=tmp_path / "index", do_ocr=False)
    front = yaml.safe_load(
        (tmp_path / "corpus" / "2003" / "2003-EO-005.md").read_text().split("---\n")[1])
    for f in ("text_quality", "dropped_header", "dropped_marks"):
        assert f in front
    bulk = json.loads((tmp_path / "corpus" / "eo.json").read_text())
    assert all("full_text_raw" in r for r in bulk)


@pytest.mark.slow
def test_ocr_build_produces_body(tmp_path, born_digital_pdf, scanned_pdf):
    pytest.importorskip("ocrmypdf")
    _setup_repo(tmp_path, born_digital_pdf, scanned_pdf)
    result = build_corpus(
        _records(), repo_root=tmp_path, corpus_dir=tmp_path / "corpus",
        index_dir=tmp_path / "index", do_ocr=True,
    )
    assert result.by_text_source[TEXT_SOURCE_OCR] == 1
    md_scan = (tmp_path / "corpus" / "1975" / "1975-EO-010.md").read_text()
    assert "OVERSIGHT" in md_scan.upper()
