"""run_picture_clips.py: the model-space -> PDF-points chain, and the CLI around it.

The thing worth testing here is the coordinate chain. dots.ocr reports bboxes in a
smart_resize'd copy of the rendered page, NOT in the rendered page's own pixels, so a
script that crops the raw numbers produces something that looks almost right --
off by a fraction of a percent, which shaves the edge off a seal and is easy to miss
by eye. So the geometry tests build a PDF with a black block at a known place, work
backwards to the bbox the model WOULD have emitted for it, and then assert the crop
comes back black. If the smart_resize step is dropped or the DPI conversion inverted,
the crop drifts off the block and the assertion fails.

No network, no GPU, no model: PyMuPDF over a PDF this file draws itself.
"""

from __future__ import annotations

import json

import fitz
import pytest
from PIL import Image

from nyc_executive_orders.vlm_ocr import smart_resize
from run_picture_clips import (
    classify_picture,
    is_likely_seal,
    is_likely_signature,
    main,
    picture_signals,
)

OCR_DPI = 200
PAGE_PT = 400.0
# The black block, in PDF points. Deliberately off-centre and non-square so a
# transposed or mirrored mapping cannot pass by symmetry.
BLOCK = fitz.Rect(80, 120, 200, 190)
# Same block moved down into the signature band, for the _sig tests. BLOCK itself
# sits above it on purpose, so the geometry tests are unaffected by the heuristic.
SIG_BLOCK = fitz.Rect(80, 300, 200, 350)
# Seal-shaped: small and near-square (32x35pt on a 400pt page -> ar 0.91, 8% wide).
SEAL_BLOCK = fitz.Rect(150, 40, 182, 75)


def _make_pdf(path, pages=1, block=BLOCK):
    """A PDF with `pages` white pages, each carrying one filled black block."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=PAGE_PT, height=PAGE_PT)
        page.draw_rect(block, color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(path)
    doc.close()
    return path


def _model_bbox(pdf_path, rect, ocr_dpi=OCR_DPI):
    """The bbox dots.ocr would have emitted for `rect` -- the inverse of the chain
    under test: PDF points -> render pixels -> smart_resize space."""
    with fitz.open(pdf_path) as doc:
        page = doc[0]
        zoom = ocr_dpi / 72.0
        orig = (page.rect * fitz.Matrix(zoom, zoom)).irect
        resized_h, resized_w = smart_resize(orig.height, orig.width)
        sx = (orig.width / zoom) / resized_w * zoom  # points -> model x
        sy = (orig.height / zoom) / resized_h * zoom
        return [
            (rect.x0 - page.rect.x0) * zoom / sx,
            (rect.y0 - page.rect.y0) * zoom / sy,
            (rect.x1 - page.rect.x0) * zoom / sx,
            (rect.y1 - page.rect.y0) * zoom / sy,
        ]


def _write_page_json(ocr_dir, page_no, bboxes, skipped=False, extra_elements=(),
                     density=0.03):
    """`density` is sparse-stroke by default, i.e. NOT the thing that decides the
    signature verdict here -- position is. Pass a seal-like 0.2 to test that clause."""
    ocr_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "page": page_no,
        "source_image": f"page_{page_no:04d}.png",
        "elements": [
            *extra_elements,
            *({"bbox": b, "category": "Picture", "ink": {"density": density}}
              for b in bboxes),
        ],
    }
    if skipped:
        record["skipped"] = "blank_or_bleedthrough"
        record["elements"] = []
    (ocr_dir / f"page_{page_no:04d}.json").write_text(json.dumps(record), encoding="utf-8")


def _volumes_json(path, entries):
    """A volumes.json the real load_volumes() accepts.

    local_paths hold ABSOLUTE tmp paths: the script joins them onto REPO_ROOT, and
    joining an absolute path wins, so the fixture resolves wherever the repo lives
    (same trick as test_run_parse.py). The filename must still carry the
    <start>_<end>_ date prefix or load_volumes drops the entry on the floor.
    """
    path.write_text(json.dumps({"volumes": [
        {"gpp_id": f"test{i}", "fileset_id": f"fs{i}", "local_paths": [str(p)],
         "description": "", "download_urls": []}
        for i, p in enumerate(entries)
    ]}), encoding="utf-8")
    return path


@pytest.fixture
def env(tmp_path):
    """One volume, one page, one Picture covering the black block."""
    pdf = _make_pdf(tmp_path / "1946-01-07_1950-10-04_Test_Volume.pdf")
    stem = pdf.stem
    _write_page_json(tmp_path / "ocr" / stem, 1, [_model_bbox(pdf, BLOCK)])
    return {
        "pdf": pdf, "stem": stem,
        "volumes_json": _volumes_json(tmp_path / "volumes.json", [pdf]),
        "ocr_root": tmp_path / "ocr", "out": tmp_path / "out",
    }


def _args(env, *extra):
    return [
        "--volumes-json", str(env["volumes_json"]),
        "--ocr-root", str(env["ocr_root"]),
        "--out-dir", str(env["out"]),
        "--ocr-dpi", str(OCR_DPI),
        *extra,
    ]


def _manifest(env):
    return json.loads((env["out"] / "manifest.json").read_text())["pictures"]


def test_crop_lands_on_the_block(env):
    """The end-to-end geometry assertion: crop the model's bbox, get the block."""
    assert main(_args(env, "--pad", "0")) == 0

    crop = env["out"] / env["stem"] / "page_0001_pic_00.png"
    assert crop.is_file()
    with Image.open(crop) as img:
        gray = img.convert("L")
        # Every pixel black -- the crop is inside the block, nowhere near the
        # white margin it would slide into if the chain were wrong.
        assert max(gray.getdata()) < 40, "crop drifted off the black block"
        # And it is the right SHAPE: the block is 120x70pt, so at 300 DPI...
        assert gray.width == pytest.approx(120 * 300 / 72, abs=2)
        assert gray.height == pytest.approx(70 * 300 / 72, abs=2)


def test_clip_rect_round_trips_to_the_block_in_points(env):
    """The manifest's clip rect must land back on the block it came from."""
    assert main(_args(env, "--pad", "0")) == 0
    entry, = _manifest(env)
    assert entry["clip_pdf_pt"] == pytest.approx([BLOCK.x0, BLOCK.y0, BLOCK.x1, BLOCK.y1],
                                                 abs=0.5)
    assert entry["page"] == 1
    assert entry["picture_index"] == 0
    assert entry["ocr_dpi"] == OCR_DPI


def test_raw_bbox_without_smart_resize_would_be_wrong(env):
    """Guards the reason this script is not a three-liner: the model bbox is not in
    render-pixel space, so the two differ. If this ever stops holding, the
    smart_resize step has become a no-op and the tests above stopped proving anything."""
    assert main(_args(env, "--pad", "0")) == 0
    entry, = _manifest(env)
    assert entry["bbox_model"] != entry["bbox_render_px"]


def test_output_dpi_scales_the_crop(env):
    assert main(_args(env, "--pad", "0", "--dpi", "600")) == 0
    with Image.open(env["out"] / env["stem"] / "page_0001_pic_00.png") as img:
        assert img.width == pytest.approx(120 * 600 / 72, abs=2)


def test_padding_grows_the_crop_and_clamps_to_the_page(tmp_path):
    """A bbox flush against the page edge must not produce a rect off the page."""
    pdf = _make_pdf(tmp_path / "1946-01-07_1950-10-04_Edge_Volume.pdf",
                    block=fitz.Rect(0, 0, 100, 100))
    env = {"pdf": pdf, "stem": pdf.stem,
           "volumes_json": _volumes_json(tmp_path / "volumes.json", [pdf]),
           "ocr_root": tmp_path / "ocr", "out": tmp_path / "out"}
    _write_page_json(tmp_path / "ocr" / pdf.stem, 1,
                     [_model_bbox(pdf, fitz.Rect(0, 0, 100, 100))])

    assert main(_args(env, "--pad", "25")) == 0
    entry, = _manifest(env)
    x0, y0, x1, y1 = entry["clip_pdf_pt"]
    assert x0 >= 0 and y0 >= 0                      # clamped, not negative
    assert x1 <= PAGE_PT and y1 <= PAGE_PT
    assert x1 > 100                                 # but it did grow on the free side


def test_reversed_corners_give_the_same_crop(env):
    """The model sometimes returns x2 < x1; that must not produce an empty crop."""
    assert main(_args(env, "--pad", "0")) == 0
    forward = (env["out"] / env["stem"] / "page_0001_pic_00.png").read_bytes()

    x1, y1, x2, y2 = _model_bbox(env["pdf"], BLOCK)
    _write_page_json(env["ocr_root"] / env["stem"], 1, [[x2, y2, x1, y1]])
    assert main(_args(env, "--pad", "0", "--force")) == 0
    assert (env["out"] / env["stem"] / "page_0001_pic_00.png").read_bytes() == forward


def test_non_picture_elements_are_ignored(env):
    _write_page_json(env["ocr_root"] / env["stem"], 1, [_model_bbox(env["pdf"], BLOCK)],
                     extra_elements=[{"bbox": [0, 0, 10, 10], "category": "Text",
                                      "text": "CITY OF NEW YORK"}])
    assert main(_args(env)) == 0
    entry, = _manifest(env)
    # The Text element sits at index 0, so the Picture is element 1 but picture 0.
    assert entry["element_index"] == 1
    assert entry["picture_index"] == 0


def test_blank_page_contributes_nothing(env):
    _write_page_json(env["ocr_root"] / env["stem"], 1, [], skipped=True)
    assert main(_args(env)) == 0
    assert not (env["out"] / env["stem"]).exists()


def test_unusable_bbox_is_reported_not_fatal(env, capsys):
    _write_page_json(env["ocr_root"] / env["stem"], 1,
                     [[1, 2, 3], _model_bbox(env["pdf"], BLOCK)])
    assert main(_args(env)) == 0
    assert "unusable bbox" in capsys.readouterr().err
    # The good one still got clipped.
    assert (env["out"] / env["stem"] / "page_0001_pic_01.png").is_file()


def test_dry_run_writes_nothing(env, capsys):
    assert main(_args(env, "--dry-run")) == 0
    assert not env["out"].exists()
    assert "1 picture(s)" in capsys.readouterr().out


def test_rerun_skips_existing_and_force_rewrites(env, capsys):
    assert main(_args(env)) == 0
    assert "1 written" in capsys.readouterr().out

    assert main(_args(env)) == 0
    out = capsys.readouterr().out
    assert "0 written" in out and "1 already present" in out
    # Still a complete manifest -- a skipped crop is not a missing one.
    assert len(_manifest(env)) == 1

    assert main(_args(env, "--force")) == 0
    assert "1 written" in capsys.readouterr().out


def test_manifest_merges_across_volumes(tmp_path):
    """Clipping volume B must not evict volume A's entries."""
    pdfs = [_make_pdf(tmp_path / f"1946-01-07_1950-10-04_Vol{i}.pdf") for i in ("A", "B")]
    env = {"volumes_json": _volumes_json(tmp_path / "volumes.json", pdfs),
           "ocr_root": tmp_path / "ocr", "out": tmp_path / "out"}
    for pdf in pdfs:
        _write_page_json(tmp_path / "ocr" / pdf.stem, 1, [_model_bbox(pdf, BLOCK)])

    assert main(_args(env, "--volume", "VolA")) == 0
    assert {e["volume"] for e in _manifest(env)} == {pdfs[0].stem}

    assert main(_args(env, "--volume", "VolB")) == 0
    assert {e["volume"] for e in _manifest(env)} == {pdfs[0].stem, pdfs[1].stem}


def test_volume_without_records_is_named_not_silent(tmp_path, capsys):
    pdf = _make_pdf(tmp_path / "1946-01-07_1950-10-04_NotOcrd.pdf")
    env = {"volumes_json": _volumes_json(tmp_path / "volumes.json", [pdf]),
           "ocr_root": tmp_path / "ocr", "out": tmp_path / "out"}

    # Not an error: a volume that has not been OCR'd is a state, not a fault.
    assert main(_args(env)) == 0
    err = capsys.readouterr().err
    assert "no page records" in err and pdf.stem in err


def test_missing_pdf_is_named_not_fatal(tmp_path, capsys):
    pdf = tmp_path / "1946-01-07_1950-10-04_Gone.pdf"
    env = {"volumes_json": _volumes_json(tmp_path / "volumes.json", [pdf]),
           "ocr_root": tmp_path / "ocr", "out": tmp_path / "out"}
    _write_page_json(tmp_path / "ocr" / pdf.stem, 1, [[1, 1, 2, 2]])

    assert main(_args(env)) == 0
    assert "PDF not on disk" in capsys.readouterr().err


def test_page_outside_the_pdf_is_skipped(env, capsys):
    _write_page_json(env["ocr_root"] / env["stem"], 99, [_model_bbox(env["pdf"], BLOCK)])
    assert main(_args(env)) == 0
    assert "outside the PDF" in capsys.readouterr().err


@pytest.mark.parametrize("bad", [["--dpi", "0"], ["--ocr-dpi", "-1"], ["--pad", "-2"]])
def test_bad_usage_returns_2(env, bad):
    assert main(_args(env, *bad)) == 2


def test_unmatched_volume_returns_2(env):
    assert main(_args(env, "--volume", "no-such-volume")) == 2


# --------------------------------------------------------------------------- #
# The _sig / _seal heuristics                                                   #
# --------------------------------------------------------------------------- #

# Real signals, computed off the O'Dwyer volume by picture_signals() itself -- one
# row per thing a `Picture` turns out to be in these scans, each checked against its
# own crop by eye. This table IS the calibration: move a threshold and a row here
# should flip. The three None rows matter as much as the tagged ones -- they are
# what the rules have to keep OUT.
CALIBRATION = [
    # (what it is, expected tag, signals)
    ("mayoral signature", "sig",
     dict(y_center_frac=0.689, aspect_ratio=4.6, width_frac=0.3286,
          height_frac=0.0534, tail_frac=0.8182, ink_density=0.03215)),
    ("faint signature, zero measured ink", "sig",
     dict(y_center_frac=0.6219, aspect_ratio=3.246, width_frac=0.3531,
          height_frac=0.084, tail_frac=0.875, ink_density=0.0)),
    ("small signature over a printed 'Mayor'", "sig",
     dict(y_center_frac=0.8346, aspect_ratio=1.395, width_frac=0.15,
          height_frac=0.0842, tail_frac=0.9231, ink_density=0.02171)),
    ("another mayor's signature", "sig",
     dict(y_center_frac=0.6207, aspect_ratio=5.643, width_frac=0.3202,
          height_frac=0.0444, tail_frac=0.9286, ink_density=0.05032)),
    ("city seal in the letterhead", "seal",
     dict(y_center_frac=0.118, aspect_ratio=0.927, width_frac=0.0812,
          height_frac=0.0655, tail_frac=0.0, ink_density=0.21431)),
    ("city seal, later volume style", "seal",
     dict(y_center_frac=0.1113, aspect_ratio=0.889, width_frac=0.0794,
          height_frac=0.0699, tail_frac=0.0, ink_density=0.07863)),
    ("faint seal, near-zero measured ink", "seal",
     dict(y_center_frac=0.1086, aspect_ratio=0.92, width_frac=0.0814,
          height_frac=0.0679, tail_frac=0.0, ink_density=0.00252)),
    ("seal sitting mid-page", "seal",
     dict(y_center_frac=0.477, aspect_ratio=0.919, width_frac=0.1112,
          height_frac=0.0925, tail_frac=0.6667, ink_density=0.11253)),
    ("library received stamp", None,
     dict(y_center_frac=0.1363, aspect_ratio=1.118, width_frac=0.2444,
          height_frac=0.1578, tail_frac=1.0, ink_density=0.00047)),
    ("scanner-bed artifact", None,
     dict(y_center_frac=0.1253, aspect_ratio=1.093, width_frac=0.2334,
          height_frac=0.1648, tail_frac=1.0, ink_density=6e-05)),
    ("printed ornament", None,
     dict(y_center_frac=0.7631, aspect_ratio=2.265, width_frac=0.0353,
          height_frac=0.012, tail_frac=0.9545, ink_density=None)),
    ("binding fragment at the page edge", None,
     dict(y_center_frac=0.1245, aspect_ratio=0.444, width_frac=0.0565,
          height_frac=0.0967, tail_frac=1.0, ink_density=None)),
]


@pytest.mark.parametrize("what,expected,signals",
                         CALIBRATION, ids=[c[0] for c in CALIBRATION])
def test_tags_match_the_eyeballed_verdicts(what, expected, signals):
    assert classify_picture(signals) == expected, what


@pytest.mark.parametrize("what,expected,signals",
                         CALIBRATION, ids=[c[0] for c in CALIBRATION])
def test_no_picture_is_both_a_signature_and_a_seal(what, expected, signals):
    """classify_picture() checks in order, so an overlap would silently resolve to
    "sig" instead of failing. Assert the two rules are actually disjoint."""
    assert not (is_likely_signature(signals) and is_likely_seal(signals)), what


def test_seal_rule_ignores_ink_and_position():
    """The two signals it would be natural to reach for, and why they are absent:
    a seal can be faint enough to measure ~0 ink, and can sit mid-page rather than
    up in the letterhead. Both cases are real in the O'Dwyer volume."""
    faint_and_low = dict(y_center_frac=0.62, aspect_ratio=0.92, width_frac=0.09,
                         height_frac=0.075, tail_frac=0.8, ink_density=0.0)
    assert is_likely_seal(faint_and_low)


def test_seal_size_ceiling_separates_it_from_the_date_stamp():
    """Shape alone would not do it -- the library's stamp is near-square too. Size is
    the clause that splits them, so pin it: same shape, stamp-sized."""
    stamp_sized = dict(y_center_frac=0.14, aspect_ratio=0.95, width_frac=0.24,
                       height_frac=0.16, tail_frac=1.0, ink_density=0.0005)
    assert not is_likely_seal(stamp_sized)


def test_no_ink_floor():
    """Explicitly pinned, because it is the counter-intuitive half of the rule:
    faint ink measures 0.0 against the true-black threshold, and real signatures do
    that. Anyone adding a minimum-ink clause should fail here first."""
    faint = dict(y_center_frac=0.7, aspect_ratio=3.0, width_frac=0.3,
                 height_frac=0.08, tail_frac=0.9, ink_density=0.0)
    assert is_likely_signature(faint)


def test_picture_signals_are_normalized():
    el = {"bbox": [100, 800, 500, 900], "ink": {"density": 0.04}}
    sig = picture_signals(el["bbox"], el, 8, 10, model_w=1000, model_h=2000)
    assert sig["y_center_frac"] == pytest.approx(0.425)   # (800+900)/2 / 2000
    assert sig["aspect_ratio"] == pytest.approx(4.0)      # 400 wide / 100 tall
    assert sig["width_frac"] == pytest.approx(0.4)
    assert sig["height_frac"] == pytest.approx(0.05)     # 100 tall / 2000
    assert sig["tail_frac"] == pytest.approx(8 / 9, abs=1e-4)   # stored rounded
    assert sig["ink_density"] == 0.04


def test_lone_element_counts_as_the_end_of_the_order():
    """A page whose only element is the Picture: index 0 of 1 is the end, not the
    start. Without this a one-element page could never be flagged."""
    assert picture_signals([0, 0, 1, 1], {}, 0, 1, 10, 10)["tail_frac"] == 1.0


@pytest.fixture
def sig_env(tmp_path):
    """One volume whose single Picture sits in the signature band."""
    pdf = _make_pdf(tmp_path / "1946-01-07_1950-10-04_Sig_Volume.pdf", block=SIG_BLOCK)
    _write_page_json(tmp_path / "ocr" / pdf.stem, 1, [_model_bbox(pdf, SIG_BLOCK)])
    return {"pdf": pdf, "stem": pdf.stem,
            "volumes_json": _volumes_json(tmp_path / "volumes.json", [pdf]),
            "ocr_root": tmp_path / "ocr", "out": tmp_path / "out"}


def test_signature_crop_gets_the_sig_suffix(sig_env, capsys):
    assert main(_args(sig_env)) == 0
    assert (sig_env["out"] / sig_env["stem"] / "page_0001_pic_00_sig.png").is_file()
    assert not (sig_env["out"] / sig_env["stem"] / "page_0001_pic_00.png").exists()

    entry, = _manifest(sig_env)
    assert entry["tag"] == "sig"
    assert entry["image"].endswith("_sig.png")
    assert "1 likely signature(s)" in capsys.readouterr().out


def test_non_signature_crop_keeps_the_plain_name(env):
    """The default fixture's block sits above the signature band."""
    assert main(_args(env)) == 0
    assert (env["out"] / env["stem"] / "page_0001_pic_00.png").is_file()
    entry, = _manifest(env)
    assert entry["tag"] is None
    assert entry["shape_signals"]["y_center_frac"] < 0.40


def test_dense_ink_in_the_signature_band_is_not_flagged(sig_env):
    """A seal low on the page is still a seal: the density ceiling, end to end."""
    _write_page_json(sig_env["ocr_root"] / sig_env["stem"], 1,
                     [_model_bbox(sig_env["pdf"], SIG_BLOCK)], density=0.25)
    assert main(_args(sig_env)) == 0
    entry, = _manifest(sig_env)
    assert entry["tag"] is None
    assert (sig_env["out"] / sig_env["stem"] / "page_0001_pic_00.png").is_file()


@pytest.fixture
def seal_env(tmp_path):
    """One volume whose single Picture is seal-shaped: small and near-square."""
    pdf = _make_pdf(tmp_path / "1946-01-07_1950-10-04_Seal_Volume.pdf", block=SEAL_BLOCK)
    _write_page_json(tmp_path / "ocr" / pdf.stem, 1, [_model_bbox(pdf, SEAL_BLOCK)],
                     density=0.22)
    return {"pdf": pdf, "stem": pdf.stem,
            "volumes_json": _volumes_json(tmp_path / "volumes.json", [pdf]),
            "ocr_root": tmp_path / "ocr", "out": tmp_path / "out"}


def test_seal_crop_gets_the_seal_suffix(seal_env, capsys):
    assert main(_args(seal_env)) == 0
    assert (seal_env["out"] / seal_env["stem"] / "page_0001_pic_00_seal.png").is_file()

    entry, = _manifest(seal_env)
    assert entry["tag"] == "seal"
    assert entry["image"].endswith("_seal.png")
    assert "1 likely seal(s)" in capsys.readouterr().out


def test_a_wide_picture_is_never_tagged_a_seal(env):
    """The default fixture's block is seal-sized-ish but 1.7x wider than tall."""
    assert main(_args(env)) == 0
    entry, = _manifest(env)
    assert entry["tag"] is None
    assert entry["shape_signals"]["aspect_ratio"] > 1.05


def test_flipped_tag_does_not_leave_two_crops_of_one_picture(sig_env):
    """Re-running after the tag changes must replace the crop, not duplicate it:
    two files for one bbox would double-count the volume. Goes _sig -> _seal, so
    both suffixed names are exercised."""
    assert main(_args(sig_env)) == 0
    vol_dir = sig_env["out"] / sig_env["stem"]
    assert (vol_dir / "page_0001_pic_00_sig.png").is_file()

    # Re-clip the same picture as something seal-shaped instead.
    _write_page_json(sig_env["ocr_root"] / sig_env["stem"], 1,
                     [_model_bbox(sig_env["pdf"], SEAL_BLOCK)], density=0.22)
    assert main(_args(sig_env)) == 0

    assert sorted(f.name for f in vol_dir.glob("*.png")) == ["page_0001_pic_00_seal.png"]
    assert len(_manifest(sig_env)) == 1


# --------------------------------------------------------------------------- #
# rotated pages
#
# Two volumes were scanned sideways, so `vlm_ocr` renders them a quarter turn
# clockwise and records `rotation.applied_cw` on each page. That makes the model's
# coordinates relative to an UPRIGHT image while the PDF page is still sideways,
# and this script has to undo the turn to land the crop in the right place. The
# failure mode if it doesn't is not subtle -- the crop lands on unrelated content
# -- but it is silent, so it gets the same black-block treatment as the
# unrotated chain above.
# --------------------------------------------------------------------------- #
LANDSCAPE_PT = (620.0, 400.0)
# Off-centre, non-square, and clear of the signature band so the tag heuristics
# don't come into it.
ROT_BLOCK = fitz.Rect(60, 40, 180, 110)


def _make_landscape_pdf(path, block=ROT_BLOCK):
    doc = fitz.open()
    page = doc.new_page(width=LANDSCAPE_PT[0], height=LANDSCAPE_PT[1])
    page.draw_rect(block, color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(path)
    doc.close()
    return path


def _model_bbox_rotated(pdf_path, rect, rotation_cw=90, ocr_dpi=OCR_DPI):
    """The bbox dots.ocr would emit for `rect` having been shown the page rotated.

    The inverse of the chain under test, one link longer than `_model_bbox`:
    PDF points -> unrotated render px -> ROTATED render px -> smart_resize space.
    """
    with fitz.open(pdf_path) as doc:
        page = doc[0]
        zoom = ocr_dpi / 72.0
        orig = (page.rect * fitz.Matrix(zoom, zoom)).irect
        rw, rh = orig.width, orig.height
        corners = [
            ((rect.x0 - page.rect.x0) * zoom, (rect.y0 - page.rect.y0) * zoom),
            ((rect.x1 - page.rect.x0) * zoom, (rect.y1 - page.rect.y0) * zoom),
        ]
        # Forward rotation, clockwise, in render-pixel space.
        if rotation_cw == 90:
            turned = [(rh - y, x) for x, y in corners]
        elif rotation_cw == 180:
            turned = [(rw - x, rh - y) for x, y in corners]
        elif rotation_cw == 270:
            turned = [(y, rw - x) for x, y in corners]
        else:
            turned = corners
        rot_w, rot_h = (rh, rw) if rotation_cw in (90, 270) else (rw, rh)
        model_h, model_w = smart_resize(rot_h, rot_w)
        xs = sorted(p[0] for p in turned)
        ys = sorted(p[1] for p in turned)
        return [
            xs[0] * model_w / rot_w, ys[0] * model_h / rot_h,
            xs[1] * model_w / rot_w, ys[1] * model_h / rot_h,
        ]


@pytest.fixture
def rot_env(tmp_path):
    """A sideways volume: landscape PDF, page JSON stamped rotation.applied_cw 90."""
    pdf = _make_landscape_pdf(tmp_path / "1968-01-10_1969-12-29_Test_Sideways.pdf")
    stem = pdf.stem
    ocr_dir = tmp_path / "ocr" / stem
    _write_page_json(ocr_dir, 1, [_model_bbox_rotated(pdf, ROT_BLOCK)])
    # Stamp the rotation the way vlm_ocr now does.
    rec_path = ocr_dir / "page_0001.json"
    rec = json.loads(rec_path.read_text())
    rec["rotation"] = {"applied_cw": 90, "source": "auto-aspect",
                       "pdf_page_size": list(LANDSCAPE_PT)}
    rec_path.write_text(json.dumps(rec), encoding="utf-8")
    return {
        "pdf": pdf, "stem": stem,
        "volumes_json": _volumes_json(tmp_path / "volumes.json", [pdf]),
        "ocr_root": tmp_path / "ocr", "out": tmp_path / "out",
    }


def test_rotated_crop_lands_on_the_block(rot_env):
    """The whole point: a bbox from a sideways-scanned page still crops the block."""
    assert main(_args(rot_env, "--pad", "0")) == 0
    crop = rot_env["out"] / rot_env["stem"] / "page_0001_pic_00.png"
    assert crop.is_file()
    with Image.open(crop) as img:
        gray = img.convert("L")
        assert max(gray.getdata()) < 40, "crop drifted off the block on a rotated page"


def test_rotated_crop_comes_out_upright(rot_env):
    """The saved PNG is turned the same way the OCR render was, so crops off a
    sideways volume sit the same way up as every other crop in vlm-pics/.

    ROT_BLOCK is 120x70pt landscape; a quarter turn makes it 70x120 portrait."""
    assert main(_args(rot_env, "--pad", "0")) == 0
    crop = rot_env["out"] / rot_env["stem"] / "page_0001_pic_00.png"
    with Image.open(crop) as img:
        assert img.height > img.width, "rotated crop should come out portrait"
        assert img.width == pytest.approx(70 * 300 / 72, abs=3)
        assert img.height == pytest.approx(120 * 300 / 72, abs=3)


def test_ignoring_the_rotation_key_would_miss_the_block(rot_env):
    """Guards the guard: strip `rotation` and the same bbox lands somewhere else,
    so the passing test above is really exercising the un-rotation."""
    rec_path = rot_env["ocr_root"] / rot_env["stem"] / "page_0001.json"
    rec = json.loads(rec_path.read_text())
    del rec["rotation"]
    rec_path.write_text(json.dumps(rec), encoding="utf-8")

    assert main(_args(rot_env, "--pad", "0")) == 0
    crop = rot_env["out"] / rot_env["stem"] / "page_0001_pic_00.png"
    with Image.open(crop) as img:
        gray = img.convert("L")
        assert max(gray.getdata()) > 200, (
            "without the rotation key the crop should have drifted off the block; "
            "if this fails the rotated-crop test proves nothing"
        )


@pytest.mark.parametrize("rotation_cw", [90, 180, 270])
def test_every_right_angle_round_trips(tmp_path, rotation_cw):
    """All three angles, not just the 90 the two real volumes need.

    The crop is looked up by prefix because the tag suffix legitimately varies
    with the angle: turning the page moves the block relative to the page, and at
    180 it lands in the signature band and picks up `_sig`. That is the heuristic
    doing its job on a rotated page, not a geometry failure -- what is under test
    here is only that the crop still lands on the block.
    """
    pdf = _make_landscape_pdf(tmp_path / "1968-01-10_1969-12-29_Test_Sideways.pdf")
    stem = pdf.stem
    ocr_dir = tmp_path / "ocr" / stem
    _write_page_json(ocr_dir, 1, [_model_bbox_rotated(pdf, ROT_BLOCK, rotation_cw)])
    rec_path = ocr_dir / "page_0001.json"
    rec = json.loads(rec_path.read_text())
    rec["rotation"] = {"applied_cw": rotation_cw, "source": "forced",
                       "pdf_page_size": list(LANDSCAPE_PT)}
    rec_path.write_text(json.dumps(rec), encoding="utf-8")

    env = {"pdf": pdf, "stem": stem,
           "volumes_json": _volumes_json(tmp_path / "volumes.json", [pdf]),
           "ocr_root": tmp_path / "ocr", "out": tmp_path / "out"}
    assert main(_args(env, "--pad", "0")) == 0
    crops = sorted((env["out"] / stem).glob("page_0001_pic_00*.png"))
    assert len(crops) == 1, f"expected exactly one crop, got {[c.name for c in crops]}"
    with Image.open(crops[0]) as img:
        assert max(img.convert("L").getdata()) < 40, f"{rotation_cw} deg crop missed the block"


def test_page_json_without_rotation_is_treated_as_unrotated(env):
    """Every committed page record predates this key. They must keep working
    exactly as before -- which the twelve portrait volumes' crops depend on."""
    rec_path = env["ocr_root"] / env["stem"] / "page_0001.json"
    assert "rotation" not in json.loads(rec_path.read_text())
    assert main(_args(env, "--pad", "0")) == 0
    with Image.open(env["out"] / env["stem"] / "page_0001_pic_00.png") as img:
        assert max(img.convert("L").getdata()) < 40


def test_a_nonsense_rotation_value_is_ignored_rather_than_trusted(env):
    """A non-right-angle can only be corruption; falling back to 0 keeps the
    twelve good volumes croppable instead of throwing."""
    rec_path = env["ocr_root"] / env["stem"] / "page_0001.json"
    rec = json.loads(rec_path.read_text())
    rec["rotation"] = {"applied_cw": 45}
    rec_path.write_text(json.dumps(rec), encoding="utf-8")
    assert main(_args(env, "--pad", "0")) == 0
    with Image.open(env["out"] / env["stem"] / "page_0001_pic_00.png") as img:
        assert max(img.convert("L").getdata()) < 40
