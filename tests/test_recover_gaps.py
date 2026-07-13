"""Phase B.2 (current-era gap recovery) — offline, mocked archiver client.

Every test injects a RoutingWaybackClient (canned real `wayback.CdxRecord`s keyed
by the candidate URL + fake memento bytes). The autouse socket + wayback guards
in conftest make any accidental live Internet-Archive call fail loudly. Nothing
here touches the network.

Coverage:
  * candidate-URL derivation: host-normalization (www / www1 / nycnet -> www) and
    pattern reconstruction for the one order with no recorded URL.
  * gap detection keys off disk presence, not the recorded pdf_path.
  * exact-URL Wayback lookup: newest capture wins; non-PDF/404 filtered; empty -> None.
  * magic-byte validation rejects a non-PDF snapshot (never written to disk).
  * download writes pdfs/YYYY/<eo_id>.pdf, flips source -> "wayback-gap", keeps the
    original recorded URL (and fills it for the no-URL order).
  * dry-run issues ZERO mementos.
  * idempotent re-run (file present -> cached, no fetch).
  * fetch error recorded, not raised.
  * full run_gap_recovery end-to-end into tmp dirs, with the gaps.md sections.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from nyc_executive_orders import config
from nyc_executive_orders.index import IndexRow
from nyc_executive_orders.recover_gaps import (
    ST_ERROR,
    ST_NO_CANDIDATE,
    ST_NO_SNAPSHOT,
    ST_NOT_PDF,
    ST_WOULD_RECOVER,
    candidate_public_url,
    compute_gaps,
    find_latest_pdf_capture,
    run_gap_recovery,
)

DAM = "/content/dam/nycgov/mayors-office/downloads/pdf/executive-orders"


# --------------------------------------------------------------------------- #
# Routing fake: search() keyed by exact candidate URL; per-record memento bytes.
# --------------------------------------------------------------------------- #
class RoutingWaybackClient:
    """Duck-typed archiver client that routes search() by the exact URL queried.

    Unlike conftest's FakeWaybackClient (which yields the same records for any
    query), gap recovery issues a DISTINCT exact-URL lookup per gap, so the fake
    must return different records — or none — per candidate URL. Records the
    search + memento calls for assertions.
    """

    def __init__(
        self,
        by_url: dict[str, list] | None = None,
        *,
        content_by_original: dict[str, bytes] | None = None,
        default_content: bytes = b"%PDF-1.4 recovered",
        raise_on_memento: Exception | None = None,
    ):
        self._by_url = by_url or {}
        self._content = content_by_original or {}
        self._default = default_content
        self._raise = raise_on_memento
        self.search_calls: list[tuple[str, dict]] = []
        self.memento_calls: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def search(self, url, **kwargs):
        self.search_calls.append((url, kwargs))
        yield from self._by_url.get(url, [])

    def get_memento(self, record, **kwargs):
        self.memento_calls.append(record)
        if self._raise is not None:
            raise self._raise
        content = self._content.get(record.original, self._default)
        return SimpleNamespace(content=content, status_code=200, ok=True)


def _row(eo_id, *, year, number, is_emergency, url, source=config.SOURCE_LIVE):
    return IndexRow(
        eo_id=eo_id,
        number=number,
        year=year,
        is_emergency=is_emergency,
        date_signed=None,
        title=f"Order {eo_id}",
        source_pdf_url=url,
        pdf_path=None,
        source=source,
    )


# --------------------------------------------------------------------------- #
# candidate_public_url
# --------------------------------------------------------------------------- #
def test_candidate_www_nycgov_unchanged():
    row = _row("2022-EEO-271", year=2022, number="271", is_emergency=True,
               url=f"https://www.nyc.gov{DAM}/2022/eeo-271.pdf")
    assert candidate_public_url(row) == f"https://www.nyc.gov{DAM}/2022/eeo-271.pdf"


def test_candidate_www1_host_normalized():
    row = _row("2022-EEO-190", year=2022, number="190", is_emergency=True,
               url=f"https://www1.nyc.gov{DAM}/2022/eeo-190.pdf")
    assert candidate_public_url(row) == f"https://www.nyc.gov{DAM}/2022/eeo-190.pdf"


def test_candidate_nycnet_host_normalized():
    row = _row("2022-EEO-290", year=2022, number="290", is_emergency=True,
               url=f"https://nyc-csg-web.csc.nycnet{DAM}/2022/eeo-290.pdf")
    assert candidate_public_url(row) == f"https://www.nyc.gov{DAM}/2022/eeo-290.pdf"


def test_candidate_reconstructed_when_no_url_emergency():
    # The real no-URL case: 2022-EEO-164 (emergency, number "164").
    row = _row("2022-EEO-164", year=2022, number="164", is_emergency=True, url=None)
    assert candidate_public_url(row) == f"https://www.nyc.gov{DAM}/2022/eeo-164.pdf"


def test_candidate_reconstructed_when_no_url_regular():
    row = _row("2024-EO-042", year=2024, number="42", is_emergency=False, url=None)
    assert candidate_public_url(row) == f"https://www.nyc.gov{DAM}/2024/eo-42.pdf"


def test_candidate_none_when_no_url_and_no_number():
    row = _row("2022-EEO-UNK", year=2022, number=None, is_emergency=True, url=None)
    assert candidate_public_url(row) is None


# --------------------------------------------------------------------------- #
# compute_gaps
# --------------------------------------------------------------------------- #
def test_compute_gaps_by_disk_presence(make_cdx, tmp_path):
    pdf_dir = tmp_path / "pdfs"
    # One order already on disk, one missing.
    present = _row("2022-EO-100", year=2022, number="100", is_emergency=False,
                   url="https://www.nyc.gov/x/eo-100.pdf")
    (pdf_dir / "2022").mkdir(parents=True)
    (pdf_dir / "2022" / "2022-EO-100.pdf").write_bytes(b"%PDF here")
    missing = _row("2022-EEO-271", year=2022, number="271", is_emergency=True,
                   url="https://www.nyc.gov/x/eeo-271.pdf")

    gaps = compute_gaps([present, missing], pdf_dir)
    assert [g.eo_id for g in gaps] == ["2022-EEO-271"]


# --------------------------------------------------------------------------- #
# find_latest_pdf_capture
# --------------------------------------------------------------------------- #
def test_find_latest_capture_picks_newest(make_cdx):
    url = f"https://www.nyc.gov{DAM}/2022/eeo-271.pdf"
    early = make_cdx(url, "20220601000000", digest="EARLY")
    late = make_cdx(url, "20230601000000", digest="LATE")
    client = RoutingWaybackClient({url: [early, late]})

    rec = find_latest_pdf_capture(client, url)
    assert rec.digest == "LATE"
    # Exact-match query on the candidate URL.
    assert client.search_calls[0][0] == url
    assert client.search_calls[0][1]["match_type"] == "exact"


def test_find_latest_capture_filters_non_pdf_and_404(make_cdx):
    url = f"https://www.nyc.gov{DAM}/2022/eeo-271.pdf"
    html = make_cdx(url, "20230601000000", mimetype="text/html")
    gone = make_cdx(url, "20230701000000", statuscode=404)
    client = RoutingWaybackClient({url: [html, gone]})
    assert find_latest_pdf_capture(client, url) is None


def test_find_latest_capture_none_when_empty():
    url = f"https://www.nyc.gov{DAM}/2022/eeo-999.pdf"
    client = RoutingWaybackClient({})
    assert find_latest_pdf_capture(client, url) is None


# --------------------------------------------------------------------------- #
# run_gap_recovery — download path
# --------------------------------------------------------------------------- #
def _write_index(tmp_path, rows: list[dict]):
    index_dir = tmp_path / "index"
    index_dir.mkdir()
    (index_dir / "eo_index.json").write_text(json.dumps(rows), encoding="utf-8")
    return index_dir


def test_recover_downloads_and_flips_source(make_cdx, tmp_path):
    url = f"https://www.nyc.gov{DAM}/2022/eeo-271.pdf"
    index_dir = _write_index(tmp_path, [
        {"eo_id": "2022-EEO-271", "number": "271", "year": 2022, "is_emergency": True,
         "date_signed": None, "title": "EEO 271", "source_pdf_url": url,
         "pdf_path": None, "source": config.SOURCE_LIVE},
    ])
    rec = make_cdx(url, "20230601000000")
    client = RoutingWaybackClient({url: [rec]})

    result = run_gap_recovery(
        client, download=True, pdf_dir=tmp_path / "pdfs",
        index_dir=index_dir, out_dir=tmp_path,
    )

    assert result.gaps_total == 1
    assert result.recovered == 1
    dest = tmp_path / "pdfs" / "2022" / "2022-EEO-271.pdf"
    assert dest.exists()
    assert dest.read_bytes().startswith(b"%PDF")
    row = result.index_rows[0]
    # (_rel_to_repo returns an absolute path under a tmp pdf_dir; under the real
    # repo it is the clean "pdfs/YYYY/..." form.)
    assert row.pdf_path is not None
    assert row.pdf_path.endswith("2022/2022-EEO-271.pdf")
    assert row.source == config.SOURCE_WAYBACK_GAP
    # Original recorded URL is preserved (task rule).
    assert row.source_pdf_url == url
    # The written index round-trips with the recovered path + flipped source.
    written = json.loads((index_dir / "eo_index.json").read_text(encoding="utf-8"))
    assert written[0]["pdf_path"] is not None
    assert written[0]["source"] == config.SOURCE_WAYBACK_GAP


def test_recover_no_url_row_fills_source_url(make_cdx, tmp_path):
    # 2022-EEO-164 has no recorded URL; on recovery its source_pdf_url is filled
    # with the Wayback playback URL (it had none to preserve).
    reconstructed = f"https://www.nyc.gov{DAM}/2022/eeo-164.pdf"
    index_dir = _write_index(tmp_path, [
        {"eo_id": "2022-EEO-164", "number": "164", "year": 2022, "is_emergency": True,
         "date_signed": None, "title": "EEO 164", "source_pdf_url": None,
         "pdf_path": None, "source": config.SOURCE_LIVE},
    ])
    rec = make_cdx(reconstructed, "20230601000000")
    client = RoutingWaybackClient({reconstructed: [rec]})

    result = run_gap_recovery(
        client, download=True, pdf_dir=tmp_path / "pdfs",
        index_dir=index_dir, out_dir=tmp_path,
    )
    row = result.index_rows[0]
    assert row.source == config.SOURCE_WAYBACK_GAP
    assert row.source_pdf_url.startswith("https://web.archive.org/web/")


def test_recover_rejects_non_pdf_snapshot(make_cdx, tmp_path):
    url = f"https://www.nyc.gov{DAM}/2022/eeo-271.pdf"
    index_dir = _write_index(tmp_path, [
        {"eo_id": "2022-EEO-271", "number": "271", "year": 2022, "is_emergency": True,
         "date_signed": None, "title": "EEO 271", "source_pdf_url": url,
         "pdf_path": None, "source": config.SOURCE_LIVE},
    ])
    rec = make_cdx(url, "20230601000000")
    # Snapshot fetched is an HTML soft-404, not a PDF.
    client = RoutingWaybackClient(
        {url: [rec]}, content_by_original={url: b"<html>Not Found</html>"}
    )

    result = run_gap_recovery(
        client, download=True, pdf_dir=tmp_path / "pdfs",
        index_dir=index_dir, out_dir=tmp_path,
    )
    assert result.recovered == 0
    assert result.unrecoverable == 1
    assert result.outcomes[0].status == ST_NOT_PDF
    # Nothing written to disk; row stays a gap.
    assert not (tmp_path / "pdfs" / "2022" / "2022-EEO-271.pdf").exists()
    assert result.index_rows[0].pdf_path is None
    assert result.index_rows[0].source == config.SOURCE_LIVE


def test_recover_no_snapshot_is_unrecoverable(make_cdx, tmp_path):
    url = f"https://www.nyc.gov{DAM}/2022/eeo-271.pdf"
    index_dir = _write_index(tmp_path, [
        {"eo_id": "2022-EEO-271", "number": "271", "year": 2022, "is_emergency": True,
         "date_signed": None, "title": "EEO 271", "source_pdf_url": url,
         "pdf_path": None, "source": config.SOURCE_LIVE},
    ])
    client = RoutingWaybackClient({})  # no captures for any URL

    result = run_gap_recovery(
        client, download=True, pdf_dir=tmp_path / "pdfs",
        index_dir=index_dir, out_dir=tmp_path,
    )
    assert result.unrecoverable == 1
    assert result.outcomes[0].status == ST_NO_SNAPSHOT
    assert client.memento_calls == []  # no snapshot -> no fetch


def test_recover_error_recorded_not_raised(make_cdx, tmp_path):
    url = f"https://www.nyc.gov{DAM}/2022/eeo-271.pdf"
    index_dir = _write_index(tmp_path, [
        {"eo_id": "2022-EEO-271", "number": "271", "year": 2022, "is_emergency": True,
         "date_signed": None, "title": "EEO 271", "source_pdf_url": url,
         "pdf_path": None, "source": config.SOURCE_LIVE},
    ])
    rec = make_cdx(url, "20230601000000")
    client = RoutingWaybackClient({url: [rec]}, raise_on_memento=RuntimeError("archive 503"))

    result = run_gap_recovery(
        client, download=True, pdf_dir=tmp_path / "pdfs",
        index_dir=index_dir, out_dir=tmp_path,
    )
    assert result.errors == 1
    assert result.outcomes[0].status == ST_ERROR
    assert "archive 503" in result.outcomes[0].reason


def test_recover_is_idempotent(make_cdx, tmp_path):
    url = f"https://www.nyc.gov{DAM}/2022/eeo-271.pdf"
    pdf_dir = tmp_path / "pdfs"
    dest = pdf_dir / "2022" / "2022-EEO-271.pdf"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"%PDF already here")
    index_dir = _write_index(tmp_path, [
        {"eo_id": "2022-EEO-271", "number": "271", "year": 2022, "is_emergency": True,
         "date_signed": None, "title": "EEO 271", "source_pdf_url": url,
         "pdf_path": None, "source": config.SOURCE_LIVE},
    ])
    rec = make_cdx(url, "20230601000000")
    client = RoutingWaybackClient({url: [rec]})

    result = run_gap_recovery(
        client, download=True, pdf_dir=pdf_dir, index_dir=index_dir, out_dir=tmp_path,
    )
    # File already present is NOT a gap at all (reconcile stamps pdf_path first),
    # so nothing is attempted and the bytes are untouched.
    assert result.gaps_total == 0
    assert client.memento_calls == []
    assert dest.read_bytes() == b"%PDF already here"


# --------------------------------------------------------------------------- #
# run_gap_recovery — dry run
# --------------------------------------------------------------------------- #
def test_dry_run_issues_zero_mementos(make_cdx, tmp_path):
    url = f"https://www.nyc.gov{DAM}/2022/eeo-271.pdf"
    index_dir = _write_index(tmp_path, [
        {"eo_id": "2022-EEO-271", "number": "271", "year": 2022, "is_emergency": True,
         "date_signed": None, "title": "EEO 271", "source_pdf_url": url,
         "pdf_path": None, "source": config.SOURCE_LIVE},
    ])
    rec = make_cdx(url, "20230601000000")
    client = RoutingWaybackClient({url: [rec]})

    result = run_gap_recovery(
        client, download=False, pdf_dir=tmp_path / "pdfs",
        index_dir=index_dir, out_dir=tmp_path,
    )
    assert client.memento_calls == []
    assert result.recovered == 0
    assert result.would_recover == 1
    assert result.outcomes[0].status == ST_WOULD_RECOVER
    assert not (tmp_path / "pdfs" / "2022" / "2022-EEO-271.pdf").exists()


# --------------------------------------------------------------------------- #
# End-to-end: mixed corpus, outputs + gaps.md sections
# --------------------------------------------------------------------------- #
def test_end_to_end_mixed_corpus(make_cdx, tmp_path):
    recov_url = f"https://www.nyc.gov{DAM}/2022/eeo-271.pdf"
    nycnet_url = f"https://nyc-csg-web.csc.nycnet{DAM}/2022/eeo-290.pdf"
    normalized_290 = f"https://www.nyc.gov{DAM}/2022/eeo-290.pdf"
    miss_url = f"https://www.nyc.gov{DAM}/2022/eeo-999.pdf"

    # A row already on disk (present) + three gaps: one recoverable, one nycnet
    # (host-normalized) recoverable, one with no snapshot.
    pdf_dir = tmp_path / "pdfs"
    (pdf_dir / "2022").mkdir(parents=True)
    (pdf_dir / "2022" / "2022-EO-100.pdf").write_bytes(b"%PDF present")

    index_dir = _write_index(tmp_path, [
        {"eo_id": "2022-EO-100", "number": "100", "year": 2022, "is_emergency": False,
         "date_signed": None, "title": "EO 100", "source_pdf_url": "https://www.nyc.gov/x.pdf",
         "pdf_path": None, "source": config.SOURCE_LIVE},  # pdf_path null but file present
        {"eo_id": "2022-EEO-271", "number": "271", "year": 2022, "is_emergency": True,
         "date_signed": None, "title": "EEO 271", "source_pdf_url": recov_url,
         "pdf_path": None, "source": config.SOURCE_LIVE},
        {"eo_id": "2022-EEO-290", "number": "290", "year": 2022, "is_emergency": True,
         "date_signed": None, "title": "EEO 290", "source_pdf_url": nycnet_url,
         "pdf_path": None, "source": config.SOURCE_LIVE},
        {"eo_id": "2022-EEO-999", "number": "999", "year": 2022, "is_emergency": True,
         "date_signed": None, "title": "EEO 999", "source_pdf_url": miss_url,
         "pdf_path": None, "source": config.SOURCE_LIVE},
    ])

    client = RoutingWaybackClient({
        recov_url: [make_cdx(recov_url, "20230601000000")],
        normalized_290: [make_cdx(normalized_290, "20230601000000")],
        # miss_url: none
    })

    result = run_gap_recovery(
        client, download=True, pdf_dir=pdf_dir, index_dir=index_dir, out_dir=tmp_path,
    )

    # The on-disk row was reconciled (not a gap); three real gaps; two recovered.
    assert result.gaps_total == 3
    assert result.recovered == 2
    assert result.unrecoverable == 1

    # Reconciled present row got its pdf_path stamped and kept source live.
    present = next(r for r in result.index_rows if r.eo_id == "2022-EO-100")
    assert present.pdf_path is not None
    assert present.pdf_path.endswith("2022/2022-EO-100.pdf")
    assert present.source == config.SOURCE_LIVE

    # Outputs written.
    assert (index_dir / "eo_index.json").exists()
    assert (index_dir / "eo_index.csv").exists()
    assert (tmp_path / "manifest.csv").exists()
    gaps_md = (tmp_path / "gaps.md").read_text(encoding="utf-8")
    assert "## Recovered via Wayback gap pass" in gaps_md
    assert "## Unrecoverable after Wayback pass" in gaps_md
    assert "2022-EEO-999" in gaps_md  # the residual gap is listed
    assert "2022-EEO-271" in gaps_md  # recovered listed


def test_no_candidate_row_is_unrecoverable(tmp_path):
    index_dir = _write_index(tmp_path, [
        {"eo_id": "2022-EEO-UNK", "number": None, "year": 2022, "is_emergency": True,
         "date_signed": None, "title": "unparseable", "source_pdf_url": None,
         "pdf_path": None, "source": config.SOURCE_LIVE},
    ])
    client = RoutingWaybackClient({})
    result = run_gap_recovery(
        client, download=True, pdf_dir=tmp_path / "pdfs",
        index_dir=index_dir, out_dir=tmp_path,
    )
    assert result.outcomes[0].status == ST_NO_CANDIDATE
    assert result.unrecoverable == 1
    assert client.search_calls == []  # never even queried Wayback
