"""End-to-end corpus emit: frontmatter, bodies, stubs, bulk json, manifest."""

from __future__ import annotations

import json
import shutil

import pytest
import yaml

from nyc_executive_orders.build_corpus import (
    NO_TEXT_STUB,
    TEXT_SOURCE_NONE,
    TEXT_SOURCE_OCR,
    TEXT_SOURCE_OCR_SKIPPED,
    FRONTMATTER_FIELDS,
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
