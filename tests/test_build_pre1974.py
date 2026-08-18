"""Phase E emit — frontmatter shape, id minting, quality forcing, idempotency.

Runs the real stage-2 build over the committed page fixtures into a tmp corpus
dir, so the assertions are about output a consumer would actually receive.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from nyc_executive_orders.build_corpus import FRONTMATTER_FIELDS
from nyc_executive_orders.build_pre1974 import (
    SOURCE_GPP_VOLUME,
    Volume,
    build_pre1974,
    load_volumes,
    render_report,
)
from nyc_executive_orders.clean import TEXT_QUALITY_REVIEW
from nyc_executive_orders.vlm_ocr import TEXT_SOURCE_OCR_VLM

FIXTURES = Path(__file__).parent / "fixtures" / "pre1974"
VOLUME_STEM = "1950-11-16_1953-11-24_Impellitteri-Sharkey_Memoranda"


@pytest.fixture
def volume() -> Volume:
    """The real Impellitteri-Sharkey volume spec, as volumes.json records it."""
    return Volume(
        gpp_id="cf95jd15r",
        fileset_id="pz50gx73n",
        pdf_relpath=f"sources/gpp/volumes/{VOLUME_STEM}.pdf",
        description="Executive Memoranda issued by Mayor Vincent R. Impellitteri "
                    "and Acting Mayor Joseph T. Sharkey.",
        download_url="https://a860-gpp.nyc.gov/downloads/pz50gx73n",
        start_date="1950-11-16",
        end_date="1953-11-24",
    )


@pytest.fixture
def built(tmp_path, volume):
    """Run the real build into a scratch corpus dir."""
    ocr_root = tmp_path / "ocr"
    (ocr_root / VOLUME_STEM).mkdir(parents=True)
    for page in FIXTURES.glob("page_*.json"):
        shutil.copy2(page, ocr_root / VOLUME_STEM / page.name)
    corpus_dir = tmp_path / "corpus"
    result = build_pre1974(
        [volume], repo_root=tmp_path, corpus_dir=corpus_dir, ocr_root=ocr_root,
        ocr_meta={"model": "mlx-community/dots.mocr-8bit", "dpi": 200},
    )
    return result, corpus_dir


def read_front(md_path: Path) -> dict:
    text = md_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    front_block = text.split("---\n", 2)[1]
    return yaml.safe_load(front_block)


# --------------------------------------------------------------------------- #
# Schema                                                                        #
# --------------------------------------------------------------------------- #

def test_frontmatter_matches_the_locked_field_set(built):
    """A 1951 order must present the exact same 21 fields as a 2024 one.

    This is the whole reason Phase E emits through build_corpus.build_frontmatter
    rather than assembling its own dict.
    """
    _, corpus_dir = built
    for md in sorted(corpus_dir.rglob("*.md")):
        assert list(read_front(md).keys()) == FRONTMATTER_FIELDS


def test_bulk_json_is_separate_from_the_statutory_corpus(built):
    """Pre-1974 records go to eo_pre1974.json — never into eo.json."""
    _, corpus_dir = built
    assert (corpus_dir / "eo_pre1974.json").exists()
    assert not (corpus_dir / "eo.json").exists()


def test_bulk_records_carry_text_and_raw_text(built):
    _, corpus_dir = built
    bulk = json.loads((corpus_dir / "eo_pre1974.json").read_text(encoding="utf-8"))
    assert bulk
    for record in bulk:
        assert set(FRONTMATTER_FIELDS) <= set(record)
        assert "full_text" in record and "full_text_raw" in record


# --------------------------------------------------------------------------- #
# Metadata                                                                      #
# --------------------------------------------------------------------------- #

def test_records_land_in_their_signing_year(built):
    _, corpus_dir = built
    md = corpus_dir / "1951" / "1951-EO-014.md"
    assert md.exists()
    front = read_front(md)
    assert front["year"] == 1951
    assert front["date_signed"] == "1951-12-12"


def test_mayor_resolves_for_the_pre_1974_era(built):
    _, corpus_dir = built
    front = read_front(corpus_dir / "1951" / "1951-EO-014.md")
    assert front["mayor"] == "Impellitteri"
    assert front["administration"] == "Impellitteri"
    assert front["admin_note"] is None


def test_provenance_points_at_the_volume_not_a_per_order_pdf(built):
    _, corpus_dir = built
    front = read_front(corpus_dir / "1951" / "1951-EO-014.md")
    assert front["source"] == SOURCE_GPP_VOLUME
    assert front["pdf_path"].endswith(f"{VOLUME_STEM}.pdf")
    assert front["text_source"] == TEXT_SOURCE_OCR_VLM
    assert front["is_emergency"] is False   # no emergency series exists pre-1974


def test_hyphenated_suffix_gets_its_own_record(built):
    """7 and 7-A are different instruments and must not overwrite each other."""
    _, corpus_dir = built
    assert (corpus_dir / "1951" / "1951-EO-007A.md").exists()
    front = read_front(corpus_dir / "1951" / "1951-EO-007A.md")
    assert front["number"] == "7A"
    assert front["date_signed"] == "1951-07-16"


def test_ids_are_unique(built):
    _, corpus_dir = built
    bulk = json.loads((corpus_dir / "eo_pre1974.json").read_text(encoding="utf-8"))
    ids = [r["eo_id"] for r in bulk]
    assert len(ids) == len(set(ids))


# --------------------------------------------------------------------------- #
# Failure handling                                                              #
# --------------------------------------------------------------------------- #

def test_a_record_touching_a_failed_page_is_forced_to_review(built):
    """Memorandum 28 spans page 83, whose JSON never parsed.

    Its surviving text reads perfectly well, so the ordinary text metrics would
    call it "clean". It is not clean — it is incomplete — and the record has to
    say so.
    """
    _, corpus_dir = built
    front = read_front(corpus_dir / "1953" / "1953-EO-028.md")
    assert front["text_quality"] == TEXT_QUALITY_REVIEW

    sidecar = json.loads(
        (corpus_dir / "pre1974_provenance.json").read_text(encoding="utf-8"))
    flags = sidecar["1953-EO-028"]["flags"]
    assert any("parse-error" in f for f in flags)


def test_sidecar_records_page_span_and_lineage(built):
    _, corpus_dir = built
    sidecar = json.loads(
        (corpus_dir / "pre1974_provenance.json").read_text(encoding="utf-8"))
    entry = sidecar["1951-EO-014"]
    assert entry["page_span"] == [48, 48]
    assert entry["volume"]["fileset_id"] == "pz50gx73n"
    assert entry["instrument"]["printed_label"] == "EXECUTIVE ORDER NO. 14"
    assert entry["ocr"]["model"] == "mlx-community/dots.mocr-8bit"


def test_report_names_unreadable_pages(built):
    result, _ = built
    report = render_report(result)
    assert "83" in report
    assert "Pages the OCR could not read" in report


# --------------------------------------------------------------------------- #
# Determinism                                                                   #
# --------------------------------------------------------------------------- #

def test_rebuild_is_byte_identical(tmp_path, volume):
    """Re-running must not churn the corpus — the same guarantee eo.json has."""
    ocr_root = tmp_path / "ocr"
    (ocr_root / VOLUME_STEM).mkdir(parents=True)
    for page in FIXTURES.glob("page_*.json"):
        shutil.copy2(page, ocr_root / VOLUME_STEM / page.name)

    outputs = []
    for run in ("a", "b"):
        corpus_dir = tmp_path / f"corpus_{run}"
        build_pre1974([volume], repo_root=tmp_path, corpus_dir=corpus_dir,
                      ocr_root=ocr_root, ocr_meta={"model": "m", "dpi": 200})
        outputs.append((corpus_dir / "eo_pre1974.json").read_text(encoding="utf-8"))
    assert outputs[0] == outputs[1]


def test_volume_with_no_ocr_is_reported_not_skipped_silently(tmp_path, volume):
    result = build_pre1974([volume], repo_root=tmp_path,
                           corpus_dir=tmp_path / "corpus",
                           ocr_root=tmp_path / "empty")
    assert result.total == 0
    assert result.volumes[0]["status"] == "not-ocred"
    assert "not yet OCR'd" in render_report(result)


# --------------------------------------------------------------------------- #
# volumes.json                                                                  #
# --------------------------------------------------------------------------- #

def test_load_volumes_reads_the_real_manifest():
    """All 14 bound volumes, with coverage parsed out of their filenames."""
    repo_root = Path(__file__).resolve().parents[1]
    volumes = load_volumes(repo_root / "sources" / "gpp" / "volumes.json")
    assert len(volumes) == 14
    impellitteri = next(v for v in volumes if v.stem == VOLUME_STEM)
    assert (impellitteri.min_year, impellitteri.max_year) == (1950, 1953)
    # Coverage comes from the filename's date range, NOT date_published (which is
    # the compilation's print date and runs years after its contents).
    assert impellitteri.start_date == "1950-11-16"
    assert all(v.min_year < 1974 for v in volumes)
