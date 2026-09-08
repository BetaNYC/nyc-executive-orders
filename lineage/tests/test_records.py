"""Offline tests for :mod:`lineage.records`.

The no-text stub is the trap worth a test of its own: 67 corpus records carry the
literal string ``"_No text available_"`` rather than an empty string, and a scanner
that counts them as scanned reports a clean result where there is a gap.
"""

from __future__ import annotations

import json

import pytest
from lineage import records


def _rec(eo_id, full_text):
    return {"eo_id": eo_id, "full_text": full_text}


def test_the_no_text_placeholder_is_skipped_not_read():
    recs = [_rec("A", "real text"), _rec("B", records.NO_TEXT_STUB)]
    keep, skipped = records.with_text(recs)
    assert [r["eo_id"] for r in keep] == ["A"]
    assert skipped == 1


@pytest.mark.parametrize("text", ["", None])
def test_an_empty_or_missing_body_is_skipped(text):
    keep, skipped = records.with_text([{"eo_id": "A", "full_text": text}])
    assert keep == []
    assert skipped == 1


def test_load_corpus_joins_the_files_in_order(tmp_path):
    """The two corpus files share one schema and have no eo_id collisions."""
    a = tmp_path / "eo.json"
    a.write_text(json.dumps([_rec("2022-EO-003", "x")]))
    b = tmp_path / "eo_pre1974.json"
    b.write_text(json.dumps([_rec("1946-EO-010", "y")]))
    assert [r["eo_id"] for r in records.load_corpus([a, b])] == [
        "2022-EO-003", "1946-EO-010"]


def test_a_missing_corpus_file_reports_a_remediation_not_a_traceback(tmp_path):
    with pytest.raises(SystemExit, match="corpus file not found"):
        records.load_corpus([tmp_path / "absent.json"])


def test_a_corpus_file_that_is_not_a_list_is_refused(tmp_path):
    p = tmp_path / "eo.json"
    p.write_text(json.dumps({"records": []}))
    with pytest.raises(SystemExit, match="not a JSON list"):
        records.load_corpus([p])
