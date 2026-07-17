"""Offline tests for the GPP (Phase D) integration.

No network (the autouse ``_no_live_network`` guard is active); no OCR (born-digital
fixture only). Two layers:

  * synthetic-fixture unit tests for the parser, rescues/exclusions, eo_id minting,
    disposition logic, staging validation, additive byte-identity, and idempotency;
  * a real-data cross-check against the COMMITTED inventory + corpus snapshot,
    which pins the authoritative disposition counts (the regression that would
    catch a parser/overlap drift), per engineering-standards §0 (mocks/inputs are
    the documented shapes, not guesses).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyc_executive_orders import config, gpp

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
COMMITTED_INVENTORY = config.DEFAULT_GPP_INVENTORY
COMMITTED_CORPUS = REPO_ROOT / "corpus" / "eo.json"


# --------------------------------------------------------------------------- #
# Small synthetic builders (match the real inventory / corpus shapes)
# --------------------------------------------------------------------------- #
def inv_rec(gpp_id, title, dp, cr, fs, desc=""):
    """One GPP inventory record (the documented field set)."""
    return {"id": gpp_id, "t": title, "dp": dp, "cy": "", "cr": cr, "fs": fs,
            "desc": desc, "subj": "Government", "rt": "Executive Orders"}


def corp_rec(eo_id, mayor, is_em, number, year, pdf_path):
    """A corpus record with enough fields for match + gap-closer re-parse."""
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


def stage_pdf(staging_dir: Path, fileset_id: str, *, born_digital=True, corrupt=False):
    """Drop a ``gpp-<fsid>.pdf`` into a staging dir (real PDF bytes, or corrupt)."""
    staging_dir.mkdir(parents=True, exist_ok=True)
    dest = staging_dir / config.GPP_STAGING_FILENAME_TEMPLATE.format(fileset_id=fileset_id)
    if corrupt:
        dest.write_bytes(b"not a pdf at all")
    else:
        dest.write_bytes((FIXTURES / "born_digital_sample.pdf").read_bytes())
    return dest


# --------------------------------------------------------------------------- #
# Title parsing (incl. defect cases)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("title,kind,number", [
    ("Executive Order No. 40", gpp.KIND_EO, "40"),
    ("Executive Order 50 - Something", gpp.KIND_EO, "50"),
    ("Executive Order 51, 2020", gpp.KIND_EO, "51"),
    ("EO 56 - Title", gpp.KIND_EO, "56"),
    ("Emergency Executive Order No. 188", gpp.KIND_EEO, "188"),
    ("EEO 131 - description", gpp.KIND_EEO, "131"),
    ("EEO 1.37", gpp.KIND_EEO, "1.37"),
    ("Emergency Executive Order 3.1", gpp.KIND_EEO, "3.1"),
    ("Executive Orders and Memoranda", gpp.KIND_VOLUME, None),
    ("Annual Report on Advertising FY2020", gpp.KIND_UNPARSED, None),
    ("Emergency Execurive Order No. 526", gpp.KIND_UNPARSED, None),  # typo defeats parser
])
def test_parse_title_variants(title, kind, number):
    assert gpp.parse_title(title) == (kind, number)


def test_norm_num_strips_zeros_keeps_dotted():
    assert gpp.norm_num("008") == "8"
    assert gpp.norm_num("1.37") == "1.37"
    assert gpp.norm_num("290") == "290"
    assert gpp.norm_num(None) is None


def test_clean_gpp_title_collapses_and_strips():
    assert gpp.clean_gpp_title("  Executive   Order  40.  ") == "Executive Order 40"
    assert gpp.clean_gpp_title(None) == ""


def test_download_url_is_the_documented_shape():
    # recon report §4: /downloads/<file_set_id>, origin a860-gpp.nyc.gov
    assert gpp.download_url("vq27zn61z") == "https://a860-gpp.nyc.gov/downloads/vq27zn61z"


# --------------------------------------------------------------------------- #
# Rescues + exclusions (the 3 defect ids, the 7 excluded ids)
# --------------------------------------------------------------------------- #
def test_rescue_ids_fix_only_the_parse_from_description():
    # "Number Ninety Nine" / "Execurive" defeat the title parser; the rescue map
    # supplies the identity read from the human-verified description.
    items = gpp.build_items([
        inv_rec("f1881n57p", "Emergency Executive Order - Occupancy Enforcement",
                "2020-03-16", "Bill de Blasio", "fs99"),
        inv_rec("3b591d01g", "Emergency Execurive Order No. 526", "2023-12-01",
                "Eric Adams", "fs526"),
        inv_rec("jw827d19v", "Ban on Non-Essential Travel", "2020-03-11",
                "Bill de Blasio", "fs55"),
    ])
    by_id = {it.gpp_id: it for it in items}
    assert (by_id["f1881n57p"].kind, by_id["f1881n57p"].number) == (gpp.KIND_EEO, "99")
    assert (by_id["3b591d01g"].kind, by_id["3b591d01g"].number) == (gpp.KIND_EEO, "526")
    assert (by_id["jw827d19v"].kind, by_id["jw827d19v"].number) == (gpp.KIND_EO, "55")
    assert all(it.rescued for it in items)


def test_excluded_ids_are_dropped_with_reason():
    inv = [inv_rec(gid, "whatever non-EO", "2020-01-01", "", "fsx")
           for gid in list(gpp.EXCLUDED_IDS)[:3]]
    result = gpp.classify(inv, [])
    assert len(result.orders) == 0
    assert {gid for gid, _reason in result.excluded} == set(list(gpp.EXCLUDED_IDS)[:3])


# --------------------------------------------------------------------------- #
# eo_id minting (padding + dotted + Koch)
# --------------------------------------------------------------------------- #
def test_minting_pads_regular_and_preserves_dotted():
    inv = [
        inv_rec("g1", "Executive Order 31", "2018-05-01", "Bill de Blasio", "f1"),  # known-missing → mint
        inv_rec("g2", "Executive Order 9", "1978-03-16", "Edward I. Koch", "f2"),   # Koch known-missing
        inv_rec("g3", "Emergency Executive Order 3.1", "2026-02-01", "Zohran Mamdani", "f3"),  # net-new dotted
        inv_rec("g4", "Executive Order 15", "2026-01-15", "Zohran Mamdani", "f4"),  # net-new regular
    ]
    result = gpp.classify(inv, [])
    minted = {o.eo_id: o.disposition for o in result.orders}
    assert minted["2018-EO-031"] == gpp.GAP_CLOSER_MINT   # zero-padded to 3
    assert minted["1978-EO-009"] == gpp.GAP_CLOSER_MINT    # Koch, padded
    assert minted["2026-EEO-3.1"] == gpp.NET_NEW           # dotted, unpadded
    assert minted["2026-EO-015"] == gpp.NET_NEW            # padded


def test_mint_defers_when_no_signing_year():
    inv = [inv_rec("g1", "Executive Order 15", None, "Zohran Mamdani", "f1")]
    result = gpp.classify(inv, [])
    assert result.orders[0].eo_id is None  # can't mint without a year


# --------------------------------------------------------------------------- #
# Disposition logic (all six classes)
# --------------------------------------------------------------------------- #
def _mixed_inventory():
    return [
        inv_rec("d1", "Emergency Executive Order 100", "2020-06-01", "Bill de Blasio", "fdual"),   # dual
        inv_rec("gc1", "Emergency Executive Order 290", "2022-12-21", "Eric Adams", "fgap"),        # gap-closer existing
        inv_rec("nn1", "Emergency Executive Order 3.1", "2026-02-01", "Zohran Mamdani", "fnew"),    # net-new
        inv_rec("m1", "Executive Order 9", "1978-03-16", "Edward I. Koch", "fkoch"),                # gap-closer mint
        inv_rec("v1", "Executive Orders and Memoranda", "1965-01-01", "John V. Lindsay", "fvol"),   # volume
        inv_rec(list(gpp.EXCLUDED_IDS)[0], "Some agency report", "2020-01-01", "", "fexc"),         # excluded
    ]


def _mixed_corpus():
    return [
        corp_rec("2020-EEO-100", "de Blasio", True, "100", 2020, "pdfs/2020/2020-EEO-100.pdf"),
        corp_rec("2022-EEO-290", "Adams", True, "290", 2022, None),  # no-pdf → gap-closer
    ]


def test_disposition_all_classes():
    result = gpp.classify(_mixed_inventory(), _mixed_corpus())
    dispo = {o.eo_id: o.disposition for o in result.orders}
    assert dispo["2020-EEO-100"] == gpp.DUAL
    assert dispo["2022-EEO-290"] == gpp.GAP_CLOSER_EXISTING
    assert dispo["2026-EEO-3.1"] == gpp.NET_NEW
    assert dispo["1978-EO-009"] == gpp.GAP_CLOSER_MINT
    assert len(result.volumes) == 1 and result.volumes[0].gpp_id == "v1"
    assert len(result.excluded) == 1


def test_rescue_disposition_derived_from_corpus_not_hardcoded():
    # f1881n57p ("Number Ninety Nine") matches an EXISTING corpus EEO 99 WITH a
    # pdf → it is DUAL, never a duplicate mint. This is the authoritative-data
    # override of the "net-new" label (the corpus already holds 2020-EEO-99).
    inv = [inv_rec("f1881n57p", "Emergency Executive Order - Occupancy Enforcement",
                   "2020-03-16", "Bill de Blasio", "fs99",
                   desc="Emergency Executive Order Number Ninety Nine")]
    corpus = [corp_rec("2020-EEO-99", "de Blasio", True, "99", 2020,
                       "pdfs/2020/2020-EEO-99.pdf")]
    result = gpp.classify(inv, corpus)
    assert result.orders[0].disposition == gpp.DUAL
    assert result.orders[0].eo_id == "2020-EEO-99"


# --------------------------------------------------------------------------- #
# Staging validation (present / missing / corrupt / extra, partial-tolerant)
# --------------------------------------------------------------------------- #
def test_validate_staging_partial(tmp_path):
    staging = tmp_path / "staging"
    stage_pdf(staging, "present1")
    stage_pdf(staging, "bad1", corrupt=True)
    stage_pdf(staging, "extra1")  # staged but not expected
    report = gpp.validate_staging(["present1", "bad1", "missing1"], staging)
    assert report.present == ["present1"]
    assert report.corrupt == ["bad1"]
    assert report.missing == ["missing1"]
    assert report.extra == ["extra1"]
    assert report.expected == 3
    assert not report.complete
    assert "1 of expected 3 present" in report.summary()


def test_is_valid_pdf(tmp_path):
    good = stage_pdf(tmp_path, "g")
    bad = stage_pdf(tmp_path, "b", corrupt=True)
    empty = tmp_path / "empty.pdf"
    empty.write_bytes(b"")
    assert gpp.is_valid_pdf(good)
    assert not gpp.is_valid_pdf(bad)
    assert not gpp.is_valid_pdf(empty)
    assert not gpp.is_valid_pdf(tmp_path / "nope.pdf")


# --------------------------------------------------------------------------- #
# Additive merge — byte-identity of untouched records
# --------------------------------------------------------------------------- #
def test_integrate_additive_byte_identity(tmp_path):
    staging = tmp_path / "staging"
    for fs in ("fdual", "fgap", "fnew", "fkoch", "fvol"):
        stage_pdf(staging, fs)
    # An extra corpus record GPP never touches — must stay byte-identical.
    untouched = corp_rec("1974-EO-001", "Beame", False, "1", 1974, "pdfs/1974/1974-EO-001.pdf")
    corpus = _mixed_corpus() + [untouched]
    before = json.dumps(untouched, sort_keys=True)

    result = gpp.integrate(
        _mixed_inventory(), corpus, staging_dir=staging, repo_root=tmp_path,
        corpus_dir=tmp_path / "corpus", sources_dir=tmp_path / "sources",
        do_ocr=False, do_write=True,
    )

    merged = {r["eo_id"]: r for r in result.records}
    # Untouched record: byte-identical.
    assert json.dumps(merged["1974-EO-001"], sort_keys=True) == before
    # Dual record: byte-identical (its GPP lineage lives in the sidecar).
    assert merged["2020-EEO-100"] == corp_rec(
        "2020-EEO-100", "de Blasio", True, "100", 2020, "pdfs/2020/2020-EEO-100.pdf")
    # Net-new + mint records were appended (source=gpp, primary pdf under pdfs/).
    assert merged["2026-EEO-3.1"]["source"] == config.SOURCE_GPP
    assert merged["2026-EEO-3.1"]["pdf_path"] == "pdfs/2026/2026-EEO-3.1.pdf"
    assert merged["1978-EO-009"]["mayor"] == "Koch"
    # Gap-closer record: pdf attached, text now present, metadata preserved.
    assert merged["2022-EEO-290"]["pdf_path"] == "pdfs/2022/2022-EEO-290.pdf"
    assert merged["2022-EEO-290"]["text_source"] == "born-digital"
    assert merged["2022-EEO-290"]["number"] == "290"  # preserved
    # Corpus grew by the minted count; never shrank.
    assert result.corpus_after == result.corpus_before + result.minted
    assert result.minted == 2 and result.gap_closed == 1
    # Dual copy placed under the parallel tree; primary pdfs/ untouched.
    assert (tmp_path / "sources" / "2020" / "2020-EEO-100--fdual.pdf").exists()
    assert not (tmp_path / "pdfs" / "2020" / "2020-EEO-100.pdf").exists()
    # Volume parked, no record.
    assert (tmp_path / "sources" / "volumes" / "fvol.pdf").exists()
    assert "1965-EO-000" not in merged


def test_integrate_dry_run_writes_no_files(tmp_path):
    # do_write=False computes the merged corpus in memory but touches nothing.
    staging = tmp_path / "staging"
    for fs in ("fnew", "fkoch"):
        stage_pdf(staging, fs)
    inv = [
        inv_rec("nn1", "Emergency Executive Order 3.1", "2026-02-01", "Zohran Mamdani", "fnew"),
        inv_rec("m1", "Executive Order 9", "1978-03-16", "Edward I. Koch", "fkoch"),
    ]
    result = gpp.integrate(inv, [], staging_dir=staging, repo_root=tmp_path,
                           corpus_dir=tmp_path / "corpus", sources_dir=tmp_path / "sources",
                           do_ocr=False, do_write=False)
    assert result.minted == 2
    assert {r["eo_id"] for r in result.records} == {"2026-EEO-3.1", "1978-EO-009"}
    # Nothing written to disk.
    assert not (tmp_path / "corpus").exists()
    assert not (tmp_path / "pdfs").exists()
    assert not (tmp_path / "sources").exists()


def test_integrate_defers_unstaged_primary(tmp_path):
    # Net-new order whose primary file is NOT staged → deferred, not minted.
    staging = tmp_path / "staging"  # empty
    staging.mkdir()
    inv = [inv_rec("nn1", "Emergency Executive Order 3.1", "2026-02-01", "Zohran Mamdani", "fnew")]
    result = gpp.integrate(inv, [], staging_dir=staging, repo_root=tmp_path,
                           corpus_dir=tmp_path / "corpus", sources_dir=tmp_path / "sources",
                           do_ocr=False, do_write=True)
    assert result.minted == 0
    assert any("2026-EEO-3.1" in d for d in result.deferred)


# --------------------------------------------------------------------------- #
# Idempotency — re-run over the same staging is a no-op
# --------------------------------------------------------------------------- #
def test_integrate_idempotent(tmp_path):
    staging = tmp_path / "staging"
    for fs in ("fdual", "fgap", "fnew", "fkoch", "fvol"):
        stage_pdf(staging, fs)
    corpus_dir = tmp_path / "corpus"
    sources_dir = tmp_path / "sources"

    def run(corpus):
        ledger = gpp.provenance_ledger(corpus_dir)
        res = gpp.integrate(_mixed_inventory(), corpus, staging_dir=staging,
                            repo_root=tmp_path, corpus_dir=corpus_dir,
                            sources_dir=sources_dir, prior_ledger=ledger,
                            do_ocr=False, do_write=True)
        gpp.write_provenance(res, corpus_dir)
        return res

    r1 = run(_mixed_corpus())
    eo1 = (corpus_dir / "eo.json").read_text()
    sidecar1 = (corpus_dir / config.GPP_PROVENANCE_JSON_NAME).read_text()

    # Re-run reads the corpus written by run 1 (as the real runner does).
    r2 = run(json.loads(eo1))
    eo2 = (corpus_dir / "eo.json").read_text()
    sidecar2 = (corpus_dir / config.GPP_PROVENANCE_JSON_NAME).read_text()

    assert r2.corpus_after == r1.corpus_after       # no growth on re-run
    assert r2.minted == 0                            # nothing re-minted
    assert eo2 == eo1                                # byte-identical corpus
    assert sidecar2 == sidecar1                      # byte-identical sidecar


# --------------------------------------------------------------------------- #
# Real-data cross-check (the authoritative regression)
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(not COMMITTED_INVENTORY.exists() or not COMMITTED_CORPUS.exists(),
                    reason="committed GPP inventory / corpus snapshot not present")
def test_real_inventory_disposition_counts():
    inv = gpp.load_inventory(COMMITTED_INVENTORY)
    corpus = json.loads(COMMITTED_CORPUS.read_text(encoding="utf-8"))
    result = gpp.classify(inv, corpus)
    counts = result.counts()
    assert counts == {
        gpp.NET_NEW: 79,
        gpp.GAP_CLOSER_MINT: 20,
        gpp.GAP_CLOSER_EXISTING: 53,
        gpp.DUAL: 2129,
        "volume": 14,
        "excluded": 7,
    }
    minted = {o.eo_id for o in result.orders
              if o.disposition in (gpp.NET_NEW, gpp.GAP_CLOSER_MINT)}
    # +99 records → 2,291 (NOT 2,272: the recon omitted the 20 gap-closer mints,
    # and EEO 99 is dual not net-new — see the module docstring / handoff).
    assert len(corpus) + len(minted) == 2291
    # Both Phase-C dangling supersession targets are now minted.
    assert {"2018-EO-031", "2020-EO-056"} <= minted
    # Koch EO 9 minted; the 3 rescues resolve to existing (dual) records.
    assert "1978-EO-009" in minted
    for eo_id in ("2020-EEO-99", "2020-EO-055", "2023-EEO-526"):
        order = next(o for o in result.orders if eo_id in o.corpus_eo_ids)
        assert order.disposition == gpp.DUAL


@pytest.mark.skipif(not COMMITTED_INVENTORY.exists(),
                    reason="committed GPP inventory snapshot not present")
def test_real_inventory_derivation_matches_committed_tsvs():
    """Cross-check derived buckets against the committed overlap TSVs (audit trail)."""
    inputs = COMMITTED_INVENTORY.parent
    inv = gpp.load_inventory(COMMITTED_INVENTORY)
    corpus = json.loads(COMMITTED_CORPUS.read_text(encoding="utf-8"))
    result = gpp.classify(inv, corpus)
    counts = result.counts()

    def tsv_rows(name):
        lines = (inputs / name).read_text(encoding="utf-8").splitlines()
        return len(lines) - 1  # minus header

    # net_new TSV (79) + gap_closers TSV (73) + volumes TSV (14) match derivation;
    # dual TSV (2128) + 1 rescued (EEO 526) = 2129.
    assert counts[gpp.NET_NEW] == tsv_rows("overlap_net_new.tsv")
    assert counts[gpp.GAP_CLOSER_MINT] + counts[gpp.GAP_CLOSER_EXISTING] == \
        tsv_rows("overlap_gap_closers.tsv")
    assert counts["volume"] == tsv_rows("volumes_pre1974.tsv")
    assert counts[gpp.DUAL] == tsv_rows("overlap_dual_provenance.tsv") + 1
