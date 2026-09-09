"""Stage 2 — building post-1974 corpus records from committed VLM page records.

Offline: no model, no OCR binary. The page records are committed fixtures in the
exact shape vlm_ocr writes; the PDF is the committed scanned fixture, needed only
so textlayer classifies the record as CLASS_SCANNED.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from nyc_executive_orders.build_corpus import (
    OCR_ENGINE_AUTO,
    OCR_ENGINE_TESSERACT,
    OCR_ENGINE_VLM,
    TEXT_SOURCE_OCR_SKIPPED,
    parse_record,
)
from nyc_executive_orders.vlm_ocr import TEXT_SOURCE_OCR_VLM, TEXT_SOURCE_OCR_VLM_FAILED

FIXTURES = Path(__file__).parent / "fixtures"
POST1974 = FIXTURES / "post1974"

EO_ID = "1974-EO-001"
YEAR = 1974


@pytest.fixture
def repo(tmp_path, scanned_pdf):
    """A miniature repo: one scanned PDF where the record says it is."""
    pdf_dir = tmp_path / "pdfs" / str(YEAR)
    pdf_dir.mkdir(parents=True)
    shutil.copy(scanned_pdf, pdf_dir / f"{EO_ID}.pdf")
    return tmp_path


@pytest.fixture
def record():
    return {
        "eo_id": EO_ID,
        "year": YEAR,
        "number": "001",
        "is_emergency": False,
        "pdf_path": f"pdfs/{YEAR}/{EO_ID}.pdf",
        "title": None,
        "date_signed": None,
        "source": "live-nycgov",
        "source_pdf_url": "https://example.invalid/eo1.pdf",
    }


def seed(repo: Path, *pages: str) -> Path:
    """Put fixture page records where stage 1 would have written them."""
    ocr_root = repo / "sources" / "ocr"
    doc_dir = ocr_root / str(YEAR) / EO_ID
    doc_dir.mkdir(parents=True, exist_ok=True)
    for i, name in enumerate(pages, start=1):
        shutil.copy(POST1974 / f"{name}.json", doc_dir / f"page_{i:04d}.json")
    return ocr_root


def parse(record, repo, ocr_root=None, engine=OCR_ENGINE_AUTO, do_ocr=False):
    return parse_record(
        record,
        repo_root=repo,
        do_ocr=do_ocr,
        ocr_config=None,
        ocr_engine=engine,
        vlm_ocr_root=ocr_root,
    )


# --------------------------------------------------------------------------- #
# The happy path                                                                #
# --------------------------------------------------------------------------- #

def test_committed_records_become_an_ocr_vlm_corpus_record(record, repo):
    ocr_root = seed(repo, "clean")
    parsed = parse(record, repo, ocr_root)

    assert parsed.frontmatter["text_source"] == TEXT_SOURCE_OCR_VLM
    assert parsed.frontmatter["page_count"] == 1
    assert "NOW, THEREFORE" in parsed.body


def test_the_letterhead_survives_and_anchors_the_tier(record, repo):
    """clean._tier returns needs-review when it cannot find its anchor, and the
    anchors ARE the letterhead — which dots.ocr labels Page-header. Dropping
    headers as furniture would flip ~1,000 records to needs-review.

    Nothing lands in dropped_header here because the anchor is the very first
    line, so there is nothing above it to relocate. The committed Tesseract
    records look the same way (corpus/2023/2023-EEO-302.md: dropped_header "").
    """
    ocr_root = seed(repo, "clean")
    parsed = parse(record, repo, ocr_root)

    assert parsed.body.startswith("THE CITY OF NEW YORK")
    assert "OFFICE OF THE MAYOR" in parsed.body
    assert parsed.frontmatter["text_quality"] == "clean"


def test_the_signing_date_is_recovered_from_the_body(record, repo):
    ocr_root = seed(repo, "clean")
    parsed = parse(record, repo, ocr_root)
    assert parsed.frontmatter["date_signed"] == "1974-01-02"


def test_the_body_carries_no_markdown_or_html(record, repo):
    """A migrated record publishes beside 1,205 born-digital ones that carry
    neither."""
    ocr_root = seed(repo, "clean")
    parsed = parse(record, repo, ocr_root)
    assert "#" not in parsed.body
    assert "<" not in parsed.body
    assert "<!--" not in parsed.body


def test_soft_hyphen_wraps_are_rejoined(record, repo):
    """So a migrated body is shaped like its born-digital siblings in the same
    eo.json, which all went through extract.clean_text."""
    ocr_root = seed(repo, "clean")
    parsed = parse(record, repo, ocr_root)
    assert "commission" in parsed.body
    assert "commis-" not in parsed.body


def test_pictures_never_reach_the_body(record, repo):
    ocr_root = seed(repo, "clean")
    parsed = parse(record, repo, ocr_root)
    assert "[Picture" not in parsed.body
    assert "bbox" not in parsed.body


def test_tables_are_flattened_not_published_as_html(record, repo):
    """Verbatim <table> markup would push the body past clean's junk-ratio and
    force needs-review on every table-bearing order."""
    ocr_root = seed(repo, "with_table")
    parsed = parse(record, repo, ocr_root)
    assert "<table>" not in parsed.body
    assert "Agency | Amount" in parsed.body
    assert "DOT | $1,000" in parsed.body


def test_multiple_pages_are_joined_in_order(record, repo):
    ocr_root = seed(repo, "clean", "clean")
    parsed = parse(record, repo, ocr_root)
    assert parsed.frontmatter["page_count"] == 2
    assert parsed.body.count("NOW, THEREFORE") == 2


# --------------------------------------------------------------------------- #
# QA flags force review                                                         #
# --------------------------------------------------------------------------- #

def test_a_truncated_page_forces_needs_review(record, repo):
    """Truncation leaves valid-looking JSON that is simply missing its tail, so
    the text metrics would happily call it clean."""
    ocr_root = seed(repo, "truncated")
    parsed = parse(record, repo, ocr_root)
    assert parsed.frontmatter["text_source"] == TEXT_SOURCE_OCR_VLM
    assert parsed.frontmatter["text_quality"] == "needs-review"


def test_a_page_that_never_parsed_is_a_recorded_failure(record, repo):
    ocr_root = seed(repo, "parse_error")
    parsed = parse(record, repo, ocr_root)
    assert parsed.frontmatter["text_source"] == TEXT_SOURCE_OCR_VLM_FAILED
    assert parsed.frontmatter["text_quality"] == "no-text"


def test_model_scaffolding_never_reaches_the_body(record, repo):
    """raw_text is the model's half-written JSON, not a transcription."""
    ocr_root = seed(repo, "parse_error")
    parsed = parse(record, repo, ocr_root)
    assert '"elements"' not in parsed.body
    assert "bbox" not in parsed.body


# --------------------------------------------------------------------------- #
# Engine precedence, and the mixed state during a long run                      #
# --------------------------------------------------------------------------- #

def test_vlm_engine_with_no_records_says_so_rather_than_falling_back(record, repo):
    """--ocr-engine vlm must never silently reach for Tesseract."""
    parsed = parse(record, repo, repo / "sources" / "ocr", engine=OCR_ENGINE_VLM,
                   do_ocr=True)
    assert parsed.frontmatter["text_source"] == TEXT_SOURCE_OCR_SKIPPED
    assert parsed.body.strip() == "_No text available_"


def test_auto_with_no_records_and_no_ocr_is_the_status_quo(record, repo):
    """A document stage 1 has not reached yet reads exactly as it does today,
    which is what lets the corpus be rebuilt mid-run."""
    parsed = parse(record, repo, repo / "sources" / "ocr", engine=OCR_ENGINE_AUTO)
    assert parsed.frontmatter["text_source"] == TEXT_SOURCE_OCR_SKIPPED


def test_tesseract_engine_ignores_the_records_entirely(record, repo, monkeypatch):
    """The rollback path. Records on disk must not divert it."""
    ocr_root = seed(repo, "clean")
    called = []

    def _fake_ocr(pdf_path, *, config=None):
        called.append(pdf_path)
        from nyc_executive_orders.extract import ExtractResult
        return ExtractResult(text="tesseract text", page_count=1, char_count=14,
                             text_source="ocr")

    monkeypatch.setattr("nyc_executive_orders.build_corpus.ocr_and_extract", _fake_ocr)
    parsed = parse(record, repo, ocr_root, engine=OCR_ENGINE_TESSERACT, do_ocr=True)

    assert called, "the Tesseract path was not taken"
    assert parsed.frontmatter["text_source"] == "ocr"


def test_auto_prefers_the_records_over_tesseract(record, repo, monkeypatch):
    ocr_root = seed(repo, "clean")

    def _boom(*a, **kw):
        raise AssertionError("Tesseract must not run when records exist")

    monkeypatch.setattr("nyc_executive_orders.build_corpus.ocr_and_extract", _boom)
    parsed = parse(record, repo, ocr_root, engine=OCR_ENGINE_AUTO, do_ocr=True)
    assert parsed.frontmatter["text_source"] == TEXT_SOURCE_OCR_VLM


# --------------------------------------------------------------------------- #
# Born-digital is untouched by every engine                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("engine", [OCR_ENGINE_AUTO, OCR_ENGINE_VLM, OCR_ENGINE_TESSERACT])
def test_born_digital_ignores_the_engine_entirely(tmp_path, born_digital_pdf, engine):
    pdf_dir = tmp_path / "pdfs" / "2003"
    pdf_dir.mkdir(parents=True)
    shutil.copy(born_digital_pdf, pdf_dir / "2003-EO-001.pdf")
    rec = {
        "eo_id": "2003-EO-001", "year": 2003, "number": "001", "is_emergency": False,
        "pdf_path": "pdfs/2003/2003-EO-001.pdf", "title": None, "date_signed": None,
        "source": "live-nycgov", "source_pdf_url": "https://example.invalid/x.pdf",
    }
    parsed = parse_record(rec, repo_root=tmp_path, do_ocr=False, ocr_config=None,
                          ocr_engine=engine, vlm_ocr_root=tmp_path / "sources" / "ocr")
    assert parsed.frontmatter["text_source"] == "born-digital"


# --------------------------------------------------------------------------- #
# Defaults                                                                      #
# --------------------------------------------------------------------------- #

def test_default_ocr_root_is_used_when_none_is_given(record, repo):
    """vlm_ocr_root=None must resolve to repo_root/sources/ocr, not be skipped."""
    seed(repo, "clean")
    parsed = parse_record(record, repo_root=repo, do_ocr=False, ocr_config=None)
    assert parsed.frontmatter["text_source"] == TEXT_SOURCE_OCR_VLM


def test_frontmatter_field_set_is_unchanged(record, repo):
    from nyc_executive_orders.build_corpus import FRONTMATTER_FIELDS

    ocr_root = seed(repo, "clean")
    parsed = parse(record, repo, ocr_root)
    assert list(parsed.frontmatter.keys()) == list(FRONTMATTER_FIELDS)


# --------------------------------------------------------------------------- #
# The forced-review verdict must survive a clean sweep                          #
# --------------------------------------------------------------------------- #

def test_clean_sweep_keeps_the_forced_review_verdict(record, repo, tmp_path):
    """A sweep re-cleans from full_text_raw and cannot see the page records, so
    the VLM's page-level QA verdict reaches it ONLY through the provenance
    sidecar. Without it, truncated text — which is clean prose right up to where
    it stops — scores clean on every text metric and is silently promoted.

    Measured on the 2026-09 corpus before this was wired up: 184 records
    (178 -> clean, 6 -> minor-noise).
    """
    from nyc_executive_orders.build_corpus import clean_existing_corpus

    ocr_root = seed(repo, "truncated")
    parsed = parse(record, repo, ocr_root)
    assert parsed.frontmatter["text_quality"] == "needs-review"
    assert parsed.vlm_provenance["forced_review"] is True

    swept = {**parsed.frontmatter,
             "full_text": parsed.body,
             "full_text_raw": parsed.raw_body}
    provenance = {EO_ID: parsed.vlm_provenance}

    result = clean_existing_corpus([swept], corpus_dir=tmp_path / "no-sidecar")
    without = json.loads(
        Path(result.output_paths["eo_json"]).read_text(encoding="utf-8"))
    assert without[0]["text_quality"] == "clean", (
        "the text metrics cannot see truncation — this is what the sidecar is for")

    result = clean_existing_corpus([swept], corpus_dir=tmp_path / "sidecar",
                                   vlm_provenance=provenance)
    with_sidecar = json.loads(
        Path(result.output_paths["eo_json"]).read_text(encoding="utf-8"))
    assert with_sidecar[0]["text_quality"] == "needs-review"


# --------------------------------------------------------------------------- #
# A scan carrying somebody else's OCR                                           #
# --------------------------------------------------------------------------- #

@pytest.fixture
def overlay_repo(tmp_path, ocr_layer_pdf):
    """The same miniature repo, but the PDF is a scan with an OCR overlay."""
    pdf_dir = tmp_path / "pdfs" / str(YEAR)
    pdf_dir.mkdir(parents=True)
    shutil.copy(ocr_layer_pdf, pdf_dir / f"{EO_ID}.pdf")
    return tmp_path


def test_an_ocr_overlay_gets_its_own_provenance(record, overlay_repo):
    """It has extractable text, so it is NOT stubbed out — but that text is
    second-hand OCR of a page image, not what `born-digital` promises."""
    from nyc_executive_orders.build_corpus import TEXT_SOURCE_OCR_LAYER

    parsed = parse(record, overlay_repo, engine=OCR_ENGINE_VLM)
    assert parsed.frontmatter["text_source"] == TEXT_SOURCE_OCR_LAYER
    assert "OVERSIGHT" in parsed.body


def test_an_ocr_overlay_keeps_its_body_when_no_records_exist_yet(record, overlay_repo):
    """THE trap this branch exists to avoid. If CLASS_OCR_LAYER fell through to
    the scanned branch, `--ocr-engine vlm` with no page records on disk would
    stamp ocr-skipped and leave the body as the stub — replacing 665 real bodies
    with "_No text available_" on the next run of the documented command.
    CorpusShrinkError counts records, not text, so nothing would catch it."""
    from nyc_executive_orders.build_corpus import NO_TEXT_STUB

    parsed = parse(record, overlay_repo, engine=OCR_ENGINE_VLM)
    assert parsed.body != NO_TEXT_STUB


def test_real_records_win_over_the_overlay(record, overlay_repo):
    """Once stage 1 has read the scan properly, its output replaces the overlay
    with no further code change."""
    ocr_root = seed(overlay_repo, "clean")
    parsed = parse(record, overlay_repo, ocr_root, engine=OCR_ENGINE_VLM)
    assert parsed.frontmatter["text_source"] == TEXT_SOURCE_OCR_VLM
    assert "NOW, THEREFORE" in parsed.body


def test_an_overlay_is_selected_for_ocr(record, overlay_repo):
    """vlm_corpus and build_corpus must agree about the population; both ask
    TextLayerResult.needs_ocr."""
    from nyc_executive_orders.vlm_corpus import probe_record

    assert probe_record(record, overlay_repo).selected is True


# --------------------------------------------------------------------------- #
# A born-digital document that nevertheless holds a scanned page                #
# --------------------------------------------------------------------------- #

@pytest.fixture
def mixed_repo(tmp_path, mixed_pages_pdf):
    pdf_dir = tmp_path / "pdfs" / str(YEAR)
    pdf_dir.mkdir(parents=True)
    shutil.copy(mixed_pages_pdf, pdf_dir / f"{EO_ID}.pdf")
    return tmp_path


def test_a_lost_page_forces_review(record, mixed_repo):
    """48 documents publish 59 pages as nothing, and the mean that let them
    through cannot see it. 2025-EO-057 was tiered clean while missing its whole
    first page."""
    parsed = parse(record, mixed_repo, engine=OCR_ENGINE_VLM)
    assert parsed.frontmatter["text_source"] == "born-digital"
    assert parsed.frontmatter["text_quality"] == "needs-review"


def test_a_lost_page_puts_the_document_on_the_worklist(record, mixed_repo):
    """There is no per-page routing here and 8 documents do not justify building
    one, so the whole document goes to the VLM."""
    from nyc_executive_orders.vlm_corpus import probe_record

    assert probe_record(record, mixed_repo).selected is True
