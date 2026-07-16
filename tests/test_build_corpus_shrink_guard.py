"""The shrink guard: build_corpus must refuse to overwrite a larger on-disk
corpus with fewer docs unless allow_shrink is set. This is the data-loss footgun
from issue #8 (a partial index would silently delete orders from corpus/eo.json).

Self-contained: the guard fires before any PDF parsing, so these records need no
fixtures — every one is a no-PDF stub (pdf_path=None), built under --no-ocr.
"""

from __future__ import annotations

import json

import pytest

from nyc_executive_orders.build_corpus import CorpusShrinkError, build_corpus


def _stub_records(n: int) -> list[dict]:
    """n no-PDF index rows — enough to drive build_corpus without any fixtures."""
    return [
        {
            "eo_id": f"2003-EO-{i:03d}", "number": f"{i:03d}", "year": 2003,
            "is_emergency": False, "date_signed": None, "title": "",
            "source": "wayback", "source_pdf_url": "", "pdf_path": None,
        }
        for i in range(1, n + 1)
    ]


def _build(tmp_path, records, **over):
    kw = dict(repo_root=tmp_path, corpus_dir=tmp_path / "corpus",
              index_dir=tmp_path / "index", do_ocr=False)
    kw.update(over)
    return build_corpus(records, **kw)


def test_refuses_to_shrink_without_flag(tmp_path):
    _build(tmp_path, _stub_records(3))          # seed a 3-record corpus
    with pytest.raises(CorpusShrinkError) as exc:
        _build(tmp_path, _stub_records(1))      # rebuild from a 1-record slice
    assert "from 3 to 1" in str(exc.value)      # message names before/after

    # The existing corpus was NOT clobbered — still 3 records on disk.
    bulk = json.loads((tmp_path / "corpus" / "eo.json").read_text())
    assert len(bulk) == 3


def test_allow_shrink_permits_it(tmp_path):
    _build(tmp_path, _stub_records(3))
    result = _build(tmp_path, _stub_records(1), allow_shrink=True)
    assert result.total == 1
    assert len(json.loads((tmp_path / "corpus" / "eo.json").read_text())) == 1


def test_first_build_into_fresh_dir_is_never_blocked(tmp_path):
    # No existing eo.json → nothing to protect → 1-record build is fine.
    result = _build(tmp_path, _stub_records(1))
    assert result.total == 1


def test_equal_and_growing_builds_pass(tmp_path):
    _build(tmp_path, _stub_records(1))          # seed 1
    _build(tmp_path, _stub_records(1))          # equal (idempotent) — allowed
    result = _build(tmp_path, _stub_records(3))  # grow 1 -> 3 — allowed
    assert result.total == 3
