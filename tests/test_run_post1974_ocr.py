"""The post-1974 stage-1 driver: selection, sharding, resume, fan-out.

Every test here is offline and model-free. Two properties are load-bearing and
asserted directly:

  * --status and --dry-run must import no backend at all, so they run in CI and
    on a machine with neither mlx nor torch installed;
  * a document that raises must not stop its worker's shard.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_post1974_ocr as driver  # noqa: E402
from nyc_executive_orders.vlm_corpus import (  # noqa: E402
    STATUS_BORN_DIGITAL,
    STATUS_NO_PDF,
    STATUS_SCANNED,
    STATUS_UNREADABLE,
    Candidate,
    bin_pack,
    select_candidates,
)

FIXTURES = Path(__file__).parent / "fixtures"


def make_record(eo_id, year, pdf_rel, **extra):
    return {"eo_id": eo_id, "year": year, "pdf_path": pdf_rel, **extra}


@pytest.fixture
def repo(tmp_path, scanned_pdf, born_digital_pdf):
    """A miniature repo holding one of each kind of PDF the probe must sort."""
    pdfs = tmp_path / "pdfs" / "1974"
    pdfs.mkdir(parents=True)
    shutil.copy(scanned_pdf, pdfs / "1974-EO-001.pdf")
    shutil.copy(born_digital_pdf, pdfs / "1974-EO-002.pdf")
    (pdfs / "1974-EO-004.pdf").write_bytes(b"not a pdf")
    return tmp_path


@pytest.fixture
def records():
    return [
        make_record("1974-EO-001", 1974, "pdfs/1974/1974-EO-001.pdf", text_source="ocr"),
        make_record("1974-EO-002", 1974, "pdfs/1974/1974-EO-002.pdf",
                    text_source="born-digital"),
        make_record("1974-EO-003", 1974, "pdfs/1974/1974-EO-003.pdf",
                    text_source="ocr-skipped"),   # file absent
        make_record("1974-EO-004", 1974, "pdfs/1974/1974-EO-004.pdf", text_source="ocr"),
    ]


# --------------------------------------------------------------------------- #
# Selection                                                                     #
# --------------------------------------------------------------------------- #

def test_the_probe_decides_not_the_recorded_text_source(records, repo):
    """Selecting on a fresh probe is what makes the 67 previously-skipped orders
    need no special case, and what keeps stage 1 and stage 2 agreeing about the
    population."""
    by_id = {c.eo_id: c for c in select_candidates(records, repo_root=repo)}

    assert by_id["1974-EO-001"].status == STATUS_SCANNED
    assert by_id["1974-EO-002"].status == STATUS_BORN_DIGITAL
    assert by_id["1974-EO-003"].status == STATUS_NO_PDF
    assert by_id["1974-EO-004"].status == STATUS_UNREADABLE


def test_only_scanned_documents_are_selected(records, repo):
    selected = [c.eo_id for c in select_candidates(records, repo_root=repo) if c.selected]
    assert selected == ["1974-EO-001"]


def test_an_unreadable_pdf_is_reported_never_queued(records, repo):
    """PyMuPDF cannot open it, and render_pdf_pages calls the same fitz.open, so
    OCR would fail identically. It is a harvest bug, not an OCR one."""
    bad = next(c for c in select_candidates(records, repo_root=repo)
               if c.eo_id == "1974-EO-004")
    assert not bad.selected
    assert bad.error


def test_every_record_is_returned_so_nothing_is_silently_dropped(records, repo):
    assert len(select_candidates(records, repo_root=repo)) == len(records)


def test_year_and_eo_id_filters(records, repo):
    assert select_candidates(records, repo_root=repo, years={1999}) == []
    only = select_candidates(records, repo_root=repo, eo_ids={"1974-EO-002"})
    assert [c.eo_id for c in only] == ["1974-EO-002"]


def test_limit_caps_selected_documents_not_probed_ones(records, repo):
    got = select_candidates(records, repo_root=repo, limit=1)
    assert len([c for c in got if c.selected]) == 1


def test_ordering_is_deterministic(records, repo):
    """A resumed run has to visit documents in the same order as the run it
    resumes."""
    a = [c.eo_id for c in select_candidates(records, repo_root=repo)]
    b = [c.eo_id for c in select_candidates(list(reversed(records)), repo_root=repo)]
    assert a == b == sorted(a)


# --------------------------------------------------------------------------- #
# Sharding                                                                      #
# --------------------------------------------------------------------------- #

def cands(*page_counts):
    return [
        Candidate(f"eo-{i:03d}", 1974, Path(f"/x/{i}.pdf"), STATUS_SCANNED, page_count=p)
        for i, p in enumerate(page_counts)
    ]


def test_shards_balance_by_page_count_not_document_count():
    shards = bin_pack(cands(10, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1), 2)
    loads = [sum(c.page_count for c in s) for s in shards]
    assert max(loads) - min(loads) <= 1
    assert sorted(len(s) for s in shards) == [1, 10]


def test_every_document_lands_in_exactly_one_shard():
    population = cands(*range(1, 40))
    shards = bin_pack(population, 7)
    placed = [c.eo_id for s in shards for c in s]
    assert sorted(placed) == sorted(c.eo_id for c in population)


def test_the_real_population_splits_within_a_fraction_of_a_percent():
    """1,086 documents, 1,799 pages, longest 21 — which is why the driver needs
    no work queue and no claim protocol."""
    import random

    rng = random.Random(0)
    population = cands(*[rng.choice([1] * 7 + [2, 3, 5]) for _ in range(1086)])
    for n in (4, 6, 8, 12):
        loads = [sum(c.page_count for c in s) for s in bin_pack(population, n)]
        assert max(loads) / (sum(loads) / n) < 1.01


def test_each_shard_is_walked_in_chronological_order():
    shards = bin_pack(cands(1, 1, 1, 1), 1)
    assert [c.eo_id for c in shards[0]] == sorted(c.eo_id for c in shards[0])


def test_more_shards_than_documents_is_allowed():
    shards = bin_pack(cands(1, 1), 5)
    assert len(shards) == 5
    assert sum(len(s) for s in shards) == 2


def test_zero_shards_is_refused():
    with pytest.raises(ValueError):
        bin_pack(cands(1), 0)


# --------------------------------------------------------------------------- #
# Resume                                                                        #
# --------------------------------------------------------------------------- #

def test_a_fully_recorded_document_is_done(tmp_path):
    c = Candidate("1974-EO-001", 1974, Path("/x.pdf"), STATUS_SCANNED, page_count=2)
    d = tmp_path / "1974" / "1974-EO-001"
    d.mkdir(parents=True)
    assert not driver.document_is_done(c, tmp_path)
    (d / "page_0001.json").write_text("{}")
    assert not driver.document_is_done(c, tmp_path)      # part-done resumes
    (d / "page_0002.json").write_text("{}")
    assert driver.document_is_done(c, tmp_path)


def test_a_document_with_no_directory_is_not_done(tmp_path):
    c = Candidate("1974-EO-009", 1974, Path("/x.pdf"), STATUS_SCANNED, page_count=1)
    assert not driver.document_is_done(c, tmp_path)


# --------------------------------------------------------------------------- #
# Scratch pruning                                                               #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "keep,flagged,survives",
    [("all", False, True), ("all", True, True),
     ("flagged", True, True), ("flagged", False, False),
     ("none", True, False), ("none", False, False)],
)
def test_keep_renders_policy(tmp_path, keep, flagged, survives):
    run_dir = tmp_path / "run"
    (run_dir / "raw").mkdir(parents=True)
    (run_dir / "raw" / "page_0001.png").write_bytes(b"x")
    driver.prune_renders(run_dir, keep, flagged)
    assert run_dir.exists() is survives


# --------------------------------------------------------------------------- #
# CLI paths that must never touch a backend                                     #
# --------------------------------------------------------------------------- #

def write_records(tmp_path, records):
    p = tmp_path / "eo.json"
    p.write_text(json.dumps(records))
    return p


def test_status_imports_no_backend(records, repo, monkeypatch, capsys):
    monkeypatch.setattr(driver, "REPO_ROOT", repo)
    rc = driver.main(["--status", "--records", str(write_records(repo, records)),
                      "--ocr-root", str(repo / "sources" / "ocr")])
    assert rc == 0
    assert "mlx" not in sys.modules
    assert "transformers" not in sys.modules
    out = capsys.readouterr().out
    assert "TOTAL" in out


def test_dry_run_imports_no_backend_and_prints_the_split(records, repo, monkeypatch, capsys):
    monkeypatch.setattr(driver, "REPO_ROOT", repo)
    rc = driver.main(["--dry-run", "--workers", "2",
                      "--records", str(write_records(repo, records)),
                      "--ocr-root", str(repo / "sources" / "ocr")])
    assert rc == 0
    assert "mlx" not in sys.modules
    assert "transformers" not in sys.modules
    assert "worker 0" in capsys.readouterr().out


def test_status_reports_the_unreadable_pdf_loudly(records, repo, monkeypatch, capsys):
    monkeypatch.setattr(driver, "REPO_ROOT", repo)
    driver.main(["--status", "--records", str(write_records(repo, records)),
                 "--ocr-root", str(repo / "sources" / "ocr")])
    out = capsys.readouterr().out
    assert "HARVEST bug" in out
    assert "1974-EO-004" in out


def test_nothing_to_do_exits_clean(records, repo, monkeypatch, capsys):
    monkeypatch.setattr(driver, "REPO_ROOT", repo)
    rc = driver.main(["--records", str(write_records(repo, records)),
                      "--year", "1999",
                      "--ocr-root", str(repo / "sources" / "ocr")])
    assert rc == 0
    assert "nothing to do" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Worker sizing                                                                 #
# --------------------------------------------------------------------------- #

def test_explicit_worker_count_is_taken_literally():
    assert driver.resolve_workers("6", "cuda") == 6
    assert driver.resolve_workers("1", "mlx") == 1


def test_auto_on_mlx_is_serial():
    """mlx shares one unified memory pool; replicas buy nothing there."""
    assert driver.resolve_workers("auto", "mlx") == 1


def test_zero_workers_is_refused():
    with pytest.raises(ValueError):
        driver.resolve_workers("0", "cuda")
