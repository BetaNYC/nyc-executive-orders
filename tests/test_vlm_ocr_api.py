"""vlm_ocr's reusable API — one model load, any number of PDFs.

The contract this file exists to hold: the refactor that made ocr_pdf callable
in a loop must not have changed a single byte of what a page record contains.
2,936 pre-1974 records are committed; a reshuffled key order would rewrite all
of them as a spurious diff.

Offline: run_page is monkeypatched, so no model is ever loaded and no backend is
imported. The PDF is the committed scanned fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from nyc_executive_orders import vlm_ocr
from nyc_executive_orders.vlm_ocr import (
    BLANK_OVERRIDE_REASON,
    LoadedModel,
    OcrOptions,
    PdfOcrResult,
    ocr_pdf,
)

FIXTURES = Path(__file__).parent / "fixtures"

# The exact key order main() has always written. Guarded, not derived.
CLEAN_PAGE_KEYS = [
    "page",
    "source_image",
    "elements",
    "debug_decode_matches_stream",
    "finish_reason",
    "logprobs",
    "page_stats",
    "rotation",
    "ink_coverage",
]

MODEL_JSON = json.dumps(
    {
        "elements": [
            {"bbox": [10, 10, 200, 40], "category": "Page-header", "text": "THE CITY OF NEW YORK"},
            {"bbox": [10, 60, 400, 300], "category": "Text", "text": "WHEREAS, a thing happened;"},
        ]
    }
)


class FakeTokenizer:
    def decode(self, ids):
        return ""


def fake_loaded(**overrides):
    """A LoadedModel whose backend is a stand-in — never touched by these tests."""
    defaults = dict(
        be=SimpleNamespace(kind="mlx"),
        model=object(),
        processor=SimpleNamespace(tokenizer=FakeTokenizer()),
        config=None,
        recorder=None,
        model_id="fake/model",
        device="mlx",
        quantization="none",
        max_tokens=12000,
        baseline_gb=0.0,
    )
    defaults.update(overrides)
    return LoadedModel(**defaults)


@pytest.fixture
def patched_run_page(monkeypatch):
    """Replace generation with a canned result; count how often it is called."""
    calls = []

    def _run_page(be, model, processor, config, prompt_text, image_path, *a, **kw):
        calls.append(Path(image_path).name)
        return vlm_ocr.PageGeneration(
            text=MODEL_JSON,
            debug_text=MODEL_JSON,
            finish_reason="stop",
            token_ids=[],
            token_logprobs=[],
            segments=[],
        )

    monkeypatch.setattr(vlm_ocr, "run_page", _run_page)
    return calls


@pytest.fixture
def dirs(tmp_path):
    raw, jsn, ovl = tmp_path / "raw", tmp_path / "json", tmp_path / "overlays"
    for d in (raw, jsn, ovl):
        d.mkdir()
    return SimpleNamespace(raw=raw, json=jsn, overlays=ovl)


def run(pdf, dirs, opts=None, **kw):
    return ocr_pdf(
        pdf,
        loaded=kw.pop("loaded", None) or fake_loaded(),
        opts=opts or OcrOptions(prompt_text="p", skip_blank_pages=False, write_overlays=False),
        raw_dir=dirs.raw,
        json_dir=dirs.json,
        overlay_dir=dirs.overlays,
        log=lambda *a, **k: None,
        **kw,
    )


# --------------------------------------------------------------------------- #
# The byte-identity contract                                                    #
# --------------------------------------------------------------------------- #

def test_page_record_key_order_is_unchanged(scanned_pdf, dirs, patched_run_page):
    """json.dumps preserves dict order, so this IS the on-disk byte order."""
    run(scanned_pdf, dirs)
    record = json.loads((dirs.json / "page_0001.json").read_text())
    assert list(record.keys()) == CLEAN_PAGE_KEYS


def test_page_record_carries_the_parsed_elements(scanned_pdf, dirs, patched_run_page):
    run(scanned_pdf, dirs)
    record = json.loads((dirs.json / "page_0001.json").read_text())
    assert [e["category"] for e in record["elements"]] == ["Page-header", "Text"]
    assert record["source_image"] == "page_0001.png"
    assert record["finish_reason"] == "stop"


def test_unparseable_output_is_recorded_not_raised(scanned_pdf, dirs, monkeypatch):
    def _bad(*a, **kw):
        return vlm_ocr.PageGeneration(
            text="not json at all", debug_text="not json at all",
            finish_reason="stop", token_ids=[], token_logprobs=[], segments=[],
        )

    monkeypatch.setattr(vlm_ocr, "run_page", _bad)
    result = run(scanned_pdf, dirs)
    record = json.loads((dirs.json / "page_0001.json").read_text())
    assert record["parse_error"]
    assert record["raw_text"] == "not json at all"
    assert record["elements"] == []
    assert result.parse_errors == 1


# --------------------------------------------------------------------------- #
# One model, many PDFs                                                          #
# --------------------------------------------------------------------------- #

def test_one_loaded_model_serves_many_pdfs(scanned_pdf, born_digital_pdf, tmp_path,
                                           patched_run_page):
    """The whole point of the refactor: 1,086 documents must not mean 1,086
    weight loads."""
    loaded = fake_loaded()
    for name, pdf in (("a", scanned_pdf), ("b", born_digital_pdf)):
        d = SimpleNamespace(
            raw=tmp_path / name / "raw",
            json=tmp_path / name / "json",
            overlays=tmp_path / name / "ovl",
        )
        for p in (d.raw, d.json, d.overlays):
            p.mkdir(parents=True)
        result = run(pdf, d, loaded=loaded)
        assert result.pages_rendered >= 1
        assert (d.json / "page_0001.json").exists()
    assert len(patched_run_page) >= 2


def test_result_counts_pages_and_time(scanned_pdf, dirs, patched_run_page):
    result = run(scanned_pdf, dirs)
    assert isinstance(result, PdfOcrResult)
    assert result.pages_rendered == result.pages_ocred >= 1
    assert result.pages_resumed == 0
    assert result.seconds >= 0


# --------------------------------------------------------------------------- #
# Resume                                                                        #
# --------------------------------------------------------------------------- #

def test_skip_existing_leaves_a_finished_page_untouched(scanned_pdf, dirs, patched_run_page):
    run(scanned_pdf, dirs)
    original = (dirs.json / "page_0001.json").read_bytes()
    patched_run_page.clear()

    result = run(scanned_pdf, dirs, skip_existing=True)
    assert result.pages_resumed >= 1
    assert result.pages_ocred == 0
    assert patched_run_page == []                       # the model was never asked
    assert (dirs.json / "page_0001.json").read_bytes() == original


def test_without_skip_existing_the_page_is_redone(scanned_pdf, dirs, patched_run_page):
    run(scanned_pdf, dirs)
    patched_run_page.clear()
    result = run(scanned_pdf, dirs)
    assert result.pages_resumed == 0
    assert patched_run_page != []


# --------------------------------------------------------------------------- #
# Overlays                                                                      #
# --------------------------------------------------------------------------- #

def test_overlays_are_written_when_asked(scanned_pdf, dirs, patched_run_page):
    run(scanned_pdf, dirs, opts=OcrOptions(prompt_text="p", skip_blank_pages=False,
                                           write_overlays=True))
    assert list(dirs.overlays.glob("*.png"))


def test_overlays_are_skipped_for_a_bulk_run(scanned_pdf, dirs, patched_run_page):
    run(scanned_pdf, dirs)                              # write_overlays=False
    assert list(dirs.overlays.glob("*.png")) == []


def test_overlay_dir_may_be_none(scanned_pdf, dirs, patched_run_page):
    result = ocr_pdf(
        scanned_pdf,
        loaded=fake_loaded(),
        opts=OcrOptions(prompt_text="p", skip_blank_pages=False, write_overlays=False),
        raw_dir=dirs.raw,
        json_dir=dirs.json,
        overlay_dir=None,
        log=lambda *a, **k: None,
    )
    assert result.pages_ocred >= 1


# --------------------------------------------------------------------------- #
# The whole-document blank guard                                                #
# --------------------------------------------------------------------------- #

def _always_blank(monkeypatch):
    monkeypatch.setattr(vlm_ocr, "is_blank_or_bleedthrough", lambda *a, **kw: True)


def test_an_all_blank_document_is_skipped_by_default(scanned_pdf, dirs, patched_run_page,
                                                     monkeypatch):
    _always_blank(monkeypatch)
    result = run(scanned_pdf, dirs, opts=OcrOptions(prompt_text="p", write_overlays=False))
    assert result.pages_skipped_blank >= 1
    assert result.pages_ocred == 0
    record = json.loads((dirs.json / "page_0001.json").read_text())
    assert record["skipped"] == "blank_or_bleedthrough"
    assert "blank_override" not in record


def test_never_skip_every_page_rescues_a_whole_document(scanned_pdf, dirs, patched_run_page,
                                                        monkeypatch):
    """764 of the 1,086 post-1974 targets are one page: a false blank there loses
    the entire executive order, not one page of a volume."""
    _always_blank(monkeypatch)
    result = run(scanned_pdf, dirs, opts=OcrOptions(
        prompt_text="p", write_overlays=False, never_skip_every_page=True,
    ))
    assert result.blank_override
    assert result.pages_skipped_blank == 0
    assert result.pages_ocred >= 1
    record = json.loads((dirs.json / "page_0001.json").read_text())
    assert record["blank_override"] is True
    assert record["blank_override_reason"] == BLANK_OVERRIDE_REASON


def test_the_guard_does_nothing_when_some_page_has_ink(scanned_pdf, dirs, patched_run_page):
    """It only fires on a UNANIMOUS blank verdict, which is evidence against the
    threshold. A document with any inked page is scored normally."""
    result = run(scanned_pdf, dirs, opts=OcrOptions(
        prompt_text="p", write_overlays=False, never_skip_every_page=True,
    ))
    assert not result.blank_override
    record = json.loads((dirs.json / "page_0001.json").read_text())
    assert "blank_override" not in record


# --------------------------------------------------------------------------- #
# Guardrails                                                                    #
# --------------------------------------------------------------------------- #

def test_max_tokens_above_the_recorders_size_is_refused(scanned_pdf, dirs, patched_run_page):
    """The cuda recorder is sized at load time; asking for more would truncate
    logprobs silently."""
    loaded = fake_loaded(recorder=object(), max_tokens=100)
    with pytest.raises(ValueError, match="logprob recorder"):
        run(scanned_pdf, dirs,
            opts=OcrOptions(prompt_text="p", max_tokens=200, write_overlays=False),
            loaded=loaded)


def test_start_page_past_the_end_returns_nothing_rather_than_raising(scanned_pdf, dirs,
                                                                    patched_run_page):
    result = run(scanned_pdf, dirs, start_page=9999)
    assert result.pages_rendered == 0
    assert list(dirs.json.glob("*.json")) == []


def test_flagged_is_what_keep_renders_uses():
    assert not PdfOcrResult(pdf_path=Path("x"), json_dir=Path("y")).flagged
    assert PdfOcrResult(pdf_path=Path("x"), json_dir=Path("y"), truncated=1).flagged
    assert PdfOcrResult(pdf_path=Path("x"), json_dir=Path("y"), parse_errors=1).flagged
    assert PdfOcrResult(pdf_path=Path("x"), json_dir=Path("y"), blank_override=True).flagged
