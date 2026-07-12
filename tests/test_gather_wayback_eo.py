"""Phase B (historical Wayback backfill) — offline, mocked archiver client.

Every test injects a FakeWaybackClient (canned real `wayback.CdxRecord`s + fake
memento bytes). The autouse socket + wayback guards make any accidental live
Internet-Archive call fail loudly. Nothing here touches the network.

Coverage:
  * CDX prefix enumeration: correct prefix/match_type/MIME/status; archiver's
    client-side filter drops non-PDF captures.
  * filename -> eo_id parsing, including an unparseable filename that is FLAGGED.
  * fetch writes to pdfs/YYYY/<eo_id>.pdf; dry-run issues ZERO mementos.
  * select_best_capture_per_url keeps the latest capture of a URL.
  * merge prefers live-nycgov over wayback on an eo_id collision.
  * full run_wayback_harvest end-to-end into tmp dirs.
"""

from __future__ import annotations

import json

import pytest

from nyc_executive_orders import config
from nyc_executive_orders.gather_wayback_eo import (
    build_wayback_rows,
    enumerate_eo_captures,
    load_index_rows,
    merge_prefer_live,
    parse_eo_filename,
    run_wayback_harvest,
    select_best_capture_per_url,
)
from nyc_executive_orders.index import IndexRow

PREFIX = "nyc.gov/html/records/pdf/executive_orders/"


def _eo_url(name: str) -> str:
    return f"http://www.nyc.gov/html/records/pdf/executive_orders/{name}"


# --------------------------------------------------------------------------- #
# Filename -> identity parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name, year, number, is_emergency",
    [
        ("2013EO001.pdf", 2013, "001", False),
        ("2018EO042.pdf", 2018, "042", False),
        ("1978EO12.pdf", 1978, "12", False),
        ("2020EEO015.pdf", 2020, "015", True),
        ("2016eo7.pdf", 2016, "7", False),  # case-insensitive
        ("2011EO-88.pdf", 2011, "88", False),  # tolerated separator
    ],
)
def test_parse_eo_filename_variants(name, year, number, is_emergency):
    parsed = parse_eo_filename(_eo_url(name))
    assert parsed is not None
    assert parsed.year == year
    assert parsed.number == number
    assert parsed.is_emergency == is_emergency


@pytest.mark.parametrize(
    "name",
    [
        "designation-of-agencies.pdf",  # no year/EO token
        "executive_orders_index.pdf",  # prose, no identity
        "readme.txt",  # not even a pdf
    ],
)
def test_parse_eo_filename_unparseable_returns_none(name):
    assert parse_eo_filename(_eo_url(name)) is None


def test_parsed_number_feeds_mint_scheme():
    # Regular integer label zero-pads; emergency label stays literal.
    from nyc_executive_orders.identity import mint_eo_id

    reg = parse_eo_filename(_eo_url("2013EO7.pdf"))
    assert mint_eo_id(reg.year, reg.number, reg.is_emergency) == "2013-EO-007"
    emer = parse_eo_filename(_eo_url("2020EEO015.pdf"))
    assert mint_eo_id(emer.year, emer.number, emer.is_emergency) == "2020-EEO-015"


# --------------------------------------------------------------------------- #
# CDX prefix enumeration
# --------------------------------------------------------------------------- #
def test_enumerate_uses_prefix_and_filters_non_pdf(make_cdx, fake_wayback_client_cls):
    records = [
        make_cdx(_eo_url("2013EO001.pdf"), "20130601000000", mimetype="application/pdf"),
        make_cdx(_eo_url("2014EO002.pdf"), "20140601000000", mimetype="application/pdf"),
        # A non-PDF capture riding the same prefix — must be filtered out.
        make_cdx(_eo_url("index.html"), "20140601000000", mimetype="text/html"),
        # A 404 capture — filtered by the status=200 constraint.
        make_cdx(_eo_url("2015EO003.pdf"), "20150601000000", statuscode=404),
    ]
    client = fake_wayback_client_cls(records)

    kept = enumerate_eo_captures(client, from_year=1974, to_year=2022)

    # Only the two 200 PDFs survive the archiver's client-side filter.
    assert {r.original for r in kept} == {_eo_url("2013EO001.pdf"), _eo_url("2014EO002.pdf")}
    # The single CDX query targeted the EO prefix with a prefix match.
    assert len(client.search_calls) == 1
    url, kwargs = client.search_calls[0]
    assert url == config.WAYBACK_EO_URL_PREFIX == PREFIX
    assert kwargs["match_type"] == "prefix"


def test_year_window_filters_captures(make_cdx, fake_wayback_client_cls):
    records = [
        make_cdx(_eo_url("2013EO001.pdf"), "20130601000000"),
        make_cdx(_eo_url("2025EO050.pdf"), "20250601000000"),
    ]
    client = fake_wayback_client_cls(records)
    kept = enumerate_eo_captures(client, from_year=1974, to_year=2022)
    assert [r.original for r in kept] == [_eo_url("2013EO001.pdf")]


# --------------------------------------------------------------------------- #
# Capture selection
# --------------------------------------------------------------------------- #
def test_select_best_capture_keeps_latest(make_cdx):
    early = make_cdx(_eo_url("2013EO001.pdf"), "20130601000000", digest="EARLY")
    late = make_cdx(_eo_url("2013EO001.pdf"), "20190601000000", digest="LATE")
    other = make_cdx(_eo_url("2014EO002.pdf"), "20140601000000")

    best = select_best_capture_per_url([early, late, other])

    assert len(best) == 2
    by_url = {r.original: r for r in best}
    assert by_url[_eo_url("2013EO001.pdf")].digest == "LATE"


# --------------------------------------------------------------------------- #
# Row construction + fetch
# --------------------------------------------------------------------------- #
def test_build_rows_flags_unparseable(make_cdx, fake_wayback_client_cls):
    records = [
        make_cdx(_eo_url("2013EO001.pdf")),
        make_cdx(_eo_url("mystery-file.pdf")),  # unparseable -> flagged
    ]
    client = fake_wayback_client_cls(records)

    build = build_wayback_rows(client, records, download=False)

    assert [r.eo_id for r in build.index_rows] == ["2013-EO-001"]
    assert len(build.flagged) == 1
    assert build.flagged[0].basename == "mystery-file.pdf"
    # Dry-run: no memento fetches at all.
    assert client.memento_calls == []


def test_fetch_writes_to_eo_id_path(make_cdx, fake_wayback_client_cls, tmp_path):
    rec = make_cdx(_eo_url("2018EO005.pdf"))
    client = fake_wayback_client_cls([rec], memento_content=b"%PDF-1.4 hello")
    pdf_dir = tmp_path / "pdfs"

    build = build_wayback_rows(client, [rec], download=True, pdf_dir=pdf_dir)

    dest = pdf_dir / "2018" / "2018-EO-005.pdf"
    assert dest.exists()
    assert dest.read_bytes() == b"%PDF-1.4 hello"
    assert build.downloaded == 1
    assert build.index_rows[0].source == config.SOURCE_WAYBACK
    # source_pdf_url is the resolved Wayback playback URL.
    assert build.index_rows[0].source_pdf_url.startswith("https://web.archive.org/web/")
    assert client.memento_calls == [rec]


def test_fetch_is_idempotent(make_cdx, fake_wayback_client_cls, tmp_path):
    rec = make_cdx(_eo_url("2018EO005.pdf"))
    pdf_dir = tmp_path / "pdfs"
    dest = pdf_dir / "2018" / "2018-EO-005.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"%PDF already here")

    client = fake_wayback_client_cls([rec])
    build = build_wayback_rows(client, [rec], download=True, pdf_dir=pdf_dir)

    # Present already -> cached, no memento fetch, bytes untouched.
    assert build.cached == 1
    assert client.memento_calls == []
    assert dest.read_bytes() == b"%PDF already here"


def test_fetch_error_recorded_not_raised(make_cdx, fake_wayback_client_cls, tmp_path):
    rec = make_cdx(_eo_url("2018EO005.pdf"))
    client = fake_wayback_client_cls([rec], raise_on_memento=RuntimeError("archive 503"))

    build = build_wayback_rows(client, [rec], download=True, pdf_dir=tmp_path / "pdfs")

    assert build.errors == 1
    assert build.manifest_rows[0].download_status == "error"
    assert "archive 503" in build.manifest_rows[0].download_error


# --------------------------------------------------------------------------- #
# Merge: prefer live over wayback
# --------------------------------------------------------------------------- #
def _index_row(eo_id, *, year, number, source, url="http://x/pdf"):
    return IndexRow(
        eo_id=eo_id,
        number=number,
        year=year,
        is_emergency=False,
        date_signed=None,
        title="",
        source_pdf_url=url,
        pdf_path=None,
        source=source,
    )


def test_merge_prefers_live_on_collision(caplog):
    from nyc_executive_orders.gather_wayback_eo import _manifest_from_index

    live = [_index_row("2020-EO-010", year=2020, number="010", source=config.SOURCE_LIVE)]
    wb_index = [
        _index_row("2020-EO-010", year=2020, number="010", source=config.SOURCE_WAYBACK),
        _index_row("2015-EO-003", year=2015, number="003", source=config.SOURCE_WAYBACK),
    ]
    wb_manifest = [_manifest_from_index(r) for r in wb_index]

    import logging

    with caplog.at_level(logging.INFO, logger="nyc_executive_orders.gather_wayback_eo"):
        merged = merge_prefer_live(live, wb_index, wb_manifest)

    ids = [r.eo_id for r in merged.index_rows]
    # The live row survives; its wayback twin is dropped; the historical-only
    # wayback row is kept.
    assert ids.count("2020-EO-010") == 1
    assert "2015-EO-003" in ids
    assert merged.dropped_wayback_ids == ["2020-EO-010"]
    assert merged.kept_wayback == 1
    # The surviving 2020-EO-010 row is the live one.
    survivor = next(r for r in merged.index_rows if r.eo_id == "2020-EO-010")
    assert survivor.source == config.SOURCE_LIVE
    assert any("preferring live" in rec.getMessage() for rec in caplog.records)


def test_merge_wayback_only_when_no_live():
    from nyc_executive_orders.gather_wayback_eo import _manifest_from_index

    wb_index = [_index_row("2015-EO-003", year=2015, number="003", source=config.SOURCE_WAYBACK)]
    wb_manifest = [_manifest_from_index(r) for r in wb_index]
    merged = merge_prefer_live([], wb_index, wb_manifest)
    assert [r.eo_id for r in merged.index_rows] == ["2015-EO-003"]
    assert merged.dropped_wayback_ids == []
    assert merged.kept_wayback == 1


def test_load_index_rows_absent_returns_empty(tmp_path):
    assert load_index_rows(tmp_path / "nope.json") == []


# --------------------------------------------------------------------------- #
# Full pipeline
# --------------------------------------------------------------------------- #
def test_run_wayback_harvest_end_to_end(make_cdx, fake_wayback_client_cls, tmp_path):
    # A live corpus already on disk (Phase A output): one order that will collide.
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    live = [
        {
            "eo_id": "2022-EO-100",
            "number": "100",
            "year": 2022,
            "is_emergency": False,
            "date_signed": "2022-03-01",
            "title": "Executive Order 100",
            "source_pdf_url": "https://www.nyc.gov/.../eo-100.pdf",
            "pdf_path": "pdfs/2022/2022-EO-100.pdf",
            "source": config.SOURCE_LIVE,
        }
    ]
    (index_dir / "eo_index.json").write_text(json.dumps(live), encoding="utf-8")

    records = [
        make_cdx(_eo_url("2013EO001.pdf"), "20130601000000"),
        make_cdx(_eo_url("2013EO001.pdf"), "20190601000000"),  # later capture of same URL
        make_cdx(_eo_url("2015EEO042.pdf"), "20150601000000"),
        make_cdx(_eo_url("2022EO100.pdf"), "20220601000000"),  # collides with live
        make_cdx(_eo_url("garbled.pdf"), "20140601000000"),  # flagged
    ]
    client = fake_wayback_client_cls(records)

    result = run_wayback_harvest(
        client,
        from_year=1974,
        to_year=2022,
        download=True,
        delay=0.0,
        pdf_dir=tmp_path / "pdfs",
        index_dir=index_dir,
        out_dir=tmp_path,
    )

    assert result.enumerated == 5
    assert result.unique_urls == 4  # the doubled 2013EO001 URL collapses to one
    assert len(result.flagged) == 1  # garbled.pdf
    assert "2022-EO-100" in result.dropped_wayback_ids  # collided with live -> dropped
    assert result.wayback_kept == 2  # 2013-EO-001 + 2015-EEO-042

    merged_ids = {r.eo_id for r in result.merged_index}
    assert merged_ids == {"2022-EO-100", "2013-EO-001", "2015-EEO-042"}
    # The kept live row is authoritative.
    live_row = next(r for r in result.merged_index if r.eo_id == "2022-EO-100")
    assert live_row.source == config.SOURCE_LIVE

    # Outputs written; index round-trips.
    written = json.loads((index_dir / "eo_index.json").read_text(encoding="utf-8"))
    assert {r["eo_id"] for r in written} == merged_ids
    assert (tmp_path / "manifest.csv").exists()
    assert (tmp_path / "gaps.md").exists()
    # Downloaded exactly the two kept historical PDFs (collided/flagged not fetched
    # into the corpus; the collided one was fetched then dropped at merge — assert
    # the two kept ones landed on disk).
    assert (tmp_path / "pdfs" / "2013" / "2013-EO-001.pdf").exists()
    assert (tmp_path / "pdfs" / "2015" / "2015-EEO-042.pdf").exists()


def test_dry_run_issues_zero_mementos(make_cdx, fake_wayback_client_cls, tmp_path):
    records = [make_cdx(_eo_url("2013EO001.pdf")), make_cdx(_eo_url("2014EO002.pdf"))]
    client = fake_wayback_client_cls(records)

    result = run_wayback_harvest(
        client,
        from_year=1974,
        to_year=2022,
        download=False,
        pdf_dir=tmp_path / "pdfs",
        index_dir=tmp_path / "index",
        out_dir=tmp_path,
    )

    assert client.memento_calls == []
    assert result.downloaded == 0
    assert result.wayback_kept == 2
