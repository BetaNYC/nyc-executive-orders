"""Phase B.4 (de Blasio 2014-2021 backfill) — offline, mocked archiver client.

Injects real `wayback.CdxRecord`s (make_cdx) into a FakeWaybackClient. The
autouse socket + wayback guards make any accidental live call fail loudly.

The de Blasio path differs from the historical Phase B path in ways this suite
pins down:
  * year comes from the URL PATH (`/executive-orders/YYYY/`), not the filename;
  * filename separator drifts (`eo_34`, `eo-34`, `eeo-173`, `EO14`);
  * MPO / election-proclamation docs on the same path are NON-EO (flagged);
  * the same file is archived under BOTH www and www1 -> must collapse to ONE
    row per eo_id (no false same-id/different-URL conflicts);
  * pre-2014 / 2022+ files on the path are out of the de Blasio window.
"""

from __future__ import annotations

import json

import pytest

from nyc_executive_orders import config
from nyc_executive_orders.gather_wayback_eo import (
    assets_path_year,
    build_wayback_rows,
    parse_eo_assets_url,
    run_deblasio_harvest,
    select_best_capture_per_identity,
)
from nyc_executive_orders.identity import mint_eo_id


def _assets_url(year: int, name: str, host: str = "www.nyc.gov") -> str:
    return f"https://{host}/assets/home/downloads/pdf/executive-orders/{year}/{name}"


# --------------------------------------------------------------------------- #
# parse_eo_assets_url — year from path, separator drift, non-EO, malformed
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "year, name, exp_year, exp_num, exp_emerg",
    [
        (2014, "eeo_1.pdf", 2014, "1", True),      # early underscore, emergency
        (2018, "eo-34.pdf", 2018, "34", False),    # later hyphen, regular
        (2021, "eeo-173.pdf", 2021, "173", True),
        (2016, "EO14.pdf", 2016, "14", False),     # no separator, mixed case
        (2018, "eo_31.pdf", 2018, "31", False),    # the dangling-citation shape
    ],
)
def test_parse_assets_variants(year, name, exp_year, exp_num, exp_emerg):
    parsed = parse_eo_assets_url(_assets_url(year, name))
    assert parsed is not None
    assert (parsed.year, parsed.number, parsed.is_emergency) == (exp_year, exp_num, exp_emerg)


@pytest.mark.parametrize(
    "year, name",
    [
        (2016, "mpo-2016-1.pdf"),                    # Mayoral Personnel Order
        (2015, "election-proclamation-07-13-2015.pdf"),
        (2019, "eo-45.pdf.105"),                     # malformed citation-cruft tail
        (2020, "eeo-153.pd"),                        # truncated
        (2020, "BCF2-ZJ34"),                         # junk
    ],
)
def test_parse_assets_non_eo_and_malformed_return_none(year, name):
    assert parse_eo_assets_url(_assets_url(year, name)) is None


def test_parse_assets_no_year_dir_returns_none():
    # A URL missing the /YYYY/ directory cannot yield a signing year.
    assert parse_eo_assets_url("https://www.nyc.gov/assets/home/downloads/pdf/eo-1.pdf") is None


def test_assets_path_year():
    assert assets_path_year(_assets_url(2018, "eo-31.pdf")) == 2018
    assert assets_path_year("https://x/other/eo-1.pdf") is None


def test_assets_parse_feeds_mint_scheme():
    # The dangling citation "EO No. 31, dated March 7, 2018" -> 2018-EO-031.
    p = parse_eo_assets_url(_assets_url(2018, "eo-31.pdf"))
    assert mint_eo_id(p.year, p.number, p.is_emergency) == "2018-EO-031"
    e = parse_eo_assets_url(_assets_url(2020, "eeo-140.pdf"))
    assert mint_eo_id(e.year, e.number, e.is_emergency) == "2020-EEO-140"


# --------------------------------------------------------------------------- #
# select_best_capture_per_identity — collapse www/www1, keep latest, flag junk
# --------------------------------------------------------------------------- #
def test_identity_collapse_folds_www_www1(make_cdx):
    # Same EO under www1 (older capture) and www (newer) -> ONE record, latest.
    records = [
        make_cdx(_assets_url(2018, "eo-34.pdf", host="www1.nyc.gov"), "20190101000000"),
        make_cdx(_assets_url(2018, "eo-34.pdf", host="www.nyc.gov"), "20220101000000"),
    ]
    kept = select_best_capture_per_identity(records, parse_eo_assets_url)
    assert len(kept) == 1
    assert "www.nyc.gov" in kept[0].original  # newer capture won


def test_identity_collapse_passes_unparseable_deduped(make_cdx):
    records = [
        make_cdx(_assets_url(2019, "eo-45.pdf.105")),  # junk
        make_cdx(_assets_url(2019, "eo-45.pdf.105")),  # same junk basename -> dedup
        make_cdx(_assets_url(2018, "eo-34.pdf")),      # real
    ]
    kept = select_best_capture_per_identity(records, parse_eo_assets_url)
    # one real + one (deduped) passthrough junk
    assert len(kept) == 2


# --------------------------------------------------------------------------- #
# build_wayback_rows — pluggable parser + source tag
# --------------------------------------------------------------------------- #
def test_build_rows_stamp_deblasio_source(make_cdx, fake_wayback_client_cls):
    records = [make_cdx(_assets_url(2020, "eo-50.pdf"))]
    client = fake_wayback_client_cls()
    build = build_wayback_rows(
        client, records, download=False,
        parser=parse_eo_assets_url, source=config.SOURCE_WAYBACK_DEBLASIO,
    )
    assert len(build.index_rows) == 1
    row = build.index_rows[0]
    assert row.eo_id == "2020-EO-050"
    assert row.source == config.SOURCE_WAYBACK_DEBLASIO
    assert row.year == 2020 and row.is_emergency is False


def test_build_rows_flags_non_eo(make_cdx, fake_wayback_client_cls):
    records = [make_cdx(_assets_url(2016, "mpo-2016-1.pdf"))]
    build = build_wayback_rows(
        fake_wayback_client_cls(), records, parser=parse_eo_assets_url,
    )
    assert build.index_rows == []
    assert len(build.flagged) == 1


# --------------------------------------------------------------------------- #
# run_deblasio_harvest — end-to-end into tmp dirs
# --------------------------------------------------------------------------- #
def _harvest_records(make_cdx):
    """A representative mixed capture set: in-window regular + emergency, a
    www/www1 duplicate, an out-of-window year, and a non-EO doc."""
    return [
        make_cdx(_assets_url(2014, "eo-1.pdf")),
        make_cdx(_assets_url(2018, "eo-34.pdf", host="www1.nyc.gov"), "20190101000000"),
        make_cdx(_assets_url(2018, "eo-34.pdf", host="www.nyc.gov"), "20220101000000"),  # dup
        make_cdx(_assets_url(2020, "eeo-140.pdf")),
        make_cdx(_assets_url(2016, "mpo-2016-1.pdf")),   # non-EO -> flagged
        make_cdx(_assets_url(2013, "eo-90.pdf")),        # out of window (pre-2014)
        make_cdx(_assets_url(2022, "eo-1.pdf")),         # out of window (post-2021)
    ]


def test_run_deblasio_dry_run_no_downloads(make_cdx, fake_wayback_client_cls, tmp_path):
    client = fake_wayback_client_cls(_harvest_records(make_cdx))
    result = run_deblasio_harvest(
        client, download=False,
        pdf_dir=tmp_path / "pdfs", index_dir=tmp_path / "index", out_dir=tmp_path,
        live_index_path=tmp_path / "none.json",
    )
    # 3 distinct in-window EOs (2014-EO-001, 2018-EO-034, 2020-EEO-140); dup folded;
    # 2013/2022 dropped by window; mpo flagged.
    ids = {r.eo_id for r in result.merged_index}
    assert ids == {"2014-EO-001", "2018-EO-034", "2020-EEO-140"}
    assert result.wayback_rows == 3
    assert len(result.conflicts) == 0          # www/www1 folded -> no false conflict
    assert len(result.flagged) == 1            # the mpo doc
    assert client.memento_calls == []          # dry run: ZERO downloads
    assert all(r.source == config.SOURCE_WAYBACK_DEBLASIO for r in result.merged_index)


def test_run_deblasio_download_writes_pdfs(make_cdx, fake_wayback_client_cls, tmp_path):
    client = fake_wayback_client_cls(_harvest_records(make_cdx))
    pdf_dir = tmp_path / "pdfs"
    result = run_deblasio_harvest(
        client, download=True,
        pdf_dir=pdf_dir, index_dir=tmp_path / "index", out_dir=tmp_path,
        live_index_path=tmp_path / "none.json",
    )
    assert result.downloaded == 3
    assert (pdf_dir / "2014" / "2014-EO-001.pdf").exists()
    assert (pdf_dir / "2018" / "2018-EO-034.pdf").exists()
    assert (pdf_dir / "2020" / "2020-EEO-140.pdf").exists()
    # idempotent: re-run finds them cached, issues no new download
    client2 = fake_wayback_client_cls(_harvest_records(make_cdx))
    result2 = run_deblasio_harvest(
        client2, download=True,
        pdf_dir=pdf_dir, index_dir=tmp_path / "index", out_dir=tmp_path,
        live_index_path=tmp_path / "none.json",
    )
    assert result2.downloaded == 0 and result2.cached == 3


def test_run_deblasio_merge_prefers_live(make_cdx, fake_wayback_client_cls, tmp_path):
    # A live index already holding 2018-EO-034 -> the wayback dup is dropped.
    live = [{
        "eo_id": "2018-EO-034", "number": "34", "year": 2018, "is_emergency": False,
        "date_signed": "2018-06-01", "title": "Live Order 34",
        "source_pdf_url": "https://www.nyc.gov/live.pdf", "pdf_path": None,
        "source": config.SOURCE_LIVE,
    }]
    live_path = tmp_path / "live.json"
    live_path.write_text(json.dumps(live), encoding="utf-8")
    client = fake_wayback_client_cls(_harvest_records(make_cdx))
    result = run_deblasio_harvest(
        client, download=False,
        pdf_dir=tmp_path / "pdfs", index_dir=tmp_path / "index", out_dir=tmp_path,
        live_index_path=live_path,
    )
    assert "2018-EO-034" in result.dropped_wayback_ids
    order34 = [r for r in result.merged_index if r.eo_id == "2018-EO-034"]
    assert len(order34) == 1 and order34[0].source == config.SOURCE_LIVE
