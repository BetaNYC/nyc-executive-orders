"""vlm_ocr page rotation: the shape heuristic, and what the renderer records.

Two of the fourteen bound volumes were scanned sideways and nothing in the files
says so -- `/Rotate` is 0 on every page of every volume, and dots.ocr reports no
orientation of its own. Page shape is the only signal available, so these tests
pin down both halves of acting on it: that a landscape page is turned a quarter
turn clockwise before the model sees it, and that what was done is written into
the page record, since the raw PNG no longer matches the PDF page's geometry and
`run_picture_clips` has to undo it.

No GPU and no model: rendering is PyMuPDF over a PDF this file draws itself.
"""

from __future__ import annotations

import fitz
import pytest
from PIL import Image

from nyc_executive_orders.vlm_ocr import (
    DEFAULT_ROTATE,
    ROTATE_CHOICES,
    detect_page_rotation,
    render_pdf_pages,
)

DPI = 200
# Deliberately not square, so a rotation cannot pass by symmetry, and far enough
# from square that smart_resize's rounding can't blur the two orientations.
PORTRAIT = (400.0, 620.0)
LANDSCAPE = (620.0, 400.0)
# Off-centre and non-square: a mirrored or transposed render still moves it.
BLOCK = fitz.Rect(40, 60, 160, 130)


def _make_pdf(path, sizes):
    """A PDF whose pages have the given (width, height) point sizes, each with a
    black block in its top-left quadrant."""
    doc = fitz.open()
    for w, h in sizes:
        page = doc.new_page(width=w, height=h)
        page.draw_rect(BLOCK, color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(path)
    doc.close()
    return path


def _render(tmp_path, sizes, **kwargs):
    tmp_path.mkdir(parents=True, exist_ok=True)
    pdf = _make_pdf(tmp_path / "vol.pdf", sizes)
    out = tmp_path / "raw"
    out.mkdir(exist_ok=True)
    return render_pdf_pages(pdf, out, DPI, None, 1, **kwargs)


def _block_centre_frac(page_w, page_h):
    """Where BLOCK's centre sits on the unrotated page, as (x, y) fractions."""
    return ((BLOCK.x0 + BLOCK.x1) / 2 / page_w, (BLOCK.y0 + BLOCK.y1) / 2 / page_h)


def _at(path, fx, fy):
    """The grey level at a fractional position in a rendered page."""
    with Image.open(path) as img:
        gray = img.convert("L")
        return gray.getpixel((int(fx * gray.width), int(fy * gray.height)))


# --------------------------------------------------------------------------- #
# the heuristic
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (PORTRAIT, 0),
        (LANDSCAPE, 90),
        # Square is not landscape, so it is left alone. Arbitrary but it has to
        # go somewhere, and "don't touch it" is the safe direction: a wrong
        # rotation is worse than none.
        ((400.0, 400.0), 0),
    ],
)
def test_detect_page_rotation_reads_page_shape(tmp_path, size, expected):
    pdf = _make_pdf(tmp_path / "vol.pdf", [size])
    with fitz.open(pdf) as doc:
        assert detect_page_rotation(doc[0]) == expected


def test_detect_ignores_pdf_rotate_since_it_is_zero_on_every_real_page(tmp_path):
    """The volumes' own /Rotate is 0 even on the sideways pages, which is exactly
    why shape has to be the signal. Guard that we key off shape, not /Rotate."""
    pdf = _make_pdf(tmp_path / "vol.pdf", [PORTRAIT])
    with fitz.open(pdf) as doc:
        doc[0].set_rotation(90)  # page.rect is now landscape
        assert doc[0].rotation == 90
        # rect follows the /Rotate, so the shape test still fires on the shape
        # the renderer would actually produce -- which is the correct behaviour.
        assert detect_page_rotation(doc[0]) == 90


# --------------------------------------------------------------------------- #
# what actually gets rendered
# --------------------------------------------------------------------------- #
def test_landscape_page_is_rendered_upright(tmp_path):
    (_, rot), = _render(tmp_path, [LANDSCAPE])
    assert rot["applied_cw"] == 90


def test_rendered_png_is_portrait_shaped_after_rotation(tmp_path):
    (path, _), = _render(tmp_path, [LANDSCAPE])
    with Image.open(path) as img:
        w, h = img.size
    assert h > w, "a landscape page should come off the renderer portrait-shaped"
    # And the dimensions are the unrotated ones, swapped.
    zoom = DPI / 72.0
    assert (w, h) == pytest.approx((LANDSCAPE[1] * zoom, LANDSCAPE[0] * zoom), abs=2)


def test_portrait_page_is_left_alone(tmp_path):
    (path, rot), = _render(tmp_path, [PORTRAIT])
    assert rot["applied_cw"] == 0
    with Image.open(path) as img:
        assert img.height > img.width


def test_rotation_moves_the_block_where_a_clockwise_turn_would(tmp_path):
    """Direction, not just "something rotated". A quarter turn CLOCKWISE sends a
    point at (x, y) to (1 - y, x); 270 would send it to (y, 1 - x), the mirror.
    Sampling the block's computed centre distinguishes the two."""
    (path, _), = _render(tmp_path, [LANDSCAPE])
    cx, cy = _block_centre_frac(*LANDSCAPE)
    assert _at(path, 1 - cy, cx) < 40, "block did not land where a 90 CW turn puts it"
    assert _at(path, cy, 1 - cx) > 200, "block landed where a 270 turn would put it"


def test_mixed_volume_rotates_per_page(tmp_path):
    """auto is per page, not per volume -- a volume is not assumed uniform."""
    rendered = _render(tmp_path, [LANDSCAPE, PORTRAIT, LANDSCAPE])
    assert [rot["applied_cw"] for _, rot in rendered] == [90, 0, 90]


# --------------------------------------------------------------------------- #
# the record written into the page JSON
# --------------------------------------------------------------------------- #
def test_rotation_is_recorded_even_when_nothing_was_rotated(tmp_path):
    """0 is recorded rather than omitted, so a consumer can tell "checked, was
    already upright" from "this JSON predates rotation handling"."""
    (_, rot), = _render(tmp_path, [PORTRAIT])
    assert rot == {
        "applied_cw": 0,
        "source": "auto-aspect",
        "pdf_page_size": [PORTRAIT[0], PORTRAIT[1]],
    }


def test_record_carries_the_unrotated_pdf_page_size(tmp_path):
    """The PDF's own geometry, not the rendered PNG's -- that is what a consumer
    needs to map back onto the page."""
    (_, rot), = _render(tmp_path, [LANDSCAPE])
    assert rot["pdf_page_size"] == [LANDSCAPE[0], LANDSCAPE[1]]


# --------------------------------------------------------------------------- #
# the explicit-angle escape hatch
# --------------------------------------------------------------------------- #
def test_forced_angle_overrides_the_heuristic(tmp_path):
    """--rotate 180 is the way out for a volume shape reads wrong: upside-down
    pages are the same shape as upright ones, so nothing automatic can see them."""
    (path, rot), = _render(tmp_path, [PORTRAIT], rotate="180")
    assert rot["applied_cw"] == 180
    assert rot["source"] == "forced"
    cx, cy = _block_centre_frac(*PORTRAIT)
    # Turned 180, the block's centre lands at the diagonally opposite point.
    assert _at(path, 1 - cx, 1 - cy) < 40
    assert _at(path, cx, cy) > 200


def test_forced_zero_disables_rotation_on_a_landscape_page(tmp_path):
    (path, rot), = _render(tmp_path, [LANDSCAPE], rotate="0")
    assert rot == {"applied_cw": 0, "source": "forced", "pdf_page_size": list(LANDSCAPE)}
    with Image.open(path) as img:
        assert img.width > img.height, "--rotate 0 should leave the page sideways"


def test_forced_270_is_the_inverse_of_90(tmp_path):
    """90 then a half turn is 270. Compared with a tolerance, not byte-for-byte:
    rasterizing a rotated page samples subpixels slightly differently than
    rotating the raster, so a handful of edge pixels legitimately differ."""
    a = _render(tmp_path / "a", [LANDSCAPE], rotate="90")
    b = _render(tmp_path / "b", [LANDSCAPE], rotate="270")
    with Image.open(a[0][0]) as ia, Image.open(b[0][0]) as ib:
        assert ia.size == ib.size
        ga = ia.convert("L").rotate(180)
        gb = ib.convert("L")
        diff = [abs(x - y) for x, y in zip(ga.getdata(), gb.getdata())]
        mean_abs = sum(diff) / len(diff)
        assert mean_abs < 1.0, f"270 is not the half-turn of 90 (mean abs diff {mean_abs:.2f})"
        # And they really are different renders, not the same image twice.
        assert ia.convert("L").tobytes() != gb.tobytes()


def test_rotate_choices_and_default_are_consistent(tmp_path):
    assert DEFAULT_ROTATE in ROTATE_CHOICES
    assert ROTATE_CHOICES == ["auto", "0", "90", "180", "270"]


def test_start_page_and_limit_still_apply(tmp_path):
    """Rotation is layered onto the existing slice/resume behaviour, not instead
    of it: filenames stay absolute so a --start-page resume extends a run."""
    pdf = _make_pdf(tmp_path / "vol.pdf", [PORTRAIT, LANDSCAPE, LANDSCAPE, PORTRAIT])
    out = tmp_path / "raw"
    out.mkdir()
    rendered = render_pdf_pages(pdf, out, DPI, 2, 2)
    assert [p.name for p, _ in rendered] == ["page_0002.png", "page_0003.png"]
    assert [rot["applied_cw"] for _, rot in rendered] == [90, 90]


def test_start_page_past_the_end_returns_nothing(tmp_path):
    pdf = _make_pdf(tmp_path / "vol.pdf", [PORTRAIT])
    out = tmp_path / "raw"
    out.mkdir()
    assert render_pdf_pages(pdf, out, DPI, None, 99) == []
