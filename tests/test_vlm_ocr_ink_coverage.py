"""The ink metric: what it measures over, and what it declines to report.

Two behaviours are pinned here, both of which produced false `uncovered_ink`
flags before:

  * Coverage measures the full page HEIGHT. The blank/bleed-through statistics
    keep the central crop -- their thresholds were swept against it -- so the
    two must not be collapsed back into one call.

  * Rule ink is CUT OUT of the mask before grouping. These rules are scanned
    and wander, so the test is local; and an intact rule chains distant ink into
    one group whose box spans the page.

  * A reported box hugs its group's own ink rather than the cell grid, so a
    reviewer opening the flag is not shown a page of text the model did box.
"""

from __future__ import annotations

import numpy as np

from nyc_executive_orders import vlm_ocr


def blank_page(h=400, w=300):
    """A white page. 255 everywhere, so any ink below is deliberate."""
    return np.full((h, w), 255, dtype=np.uint8)


def ink(gray, x1, y1, x2, y2):
    gray[y1:y2, x1:x2] = 0


def coverage(gray, elements, **overrides):
    """ink_coverage with the shipped defaults, and bboxes given in page space:
    passing the page's own dimensions as the resized ones makes rescale_bbox the
    identity, so a test says where a box is without restating smart_resize."""
    h, w = gray.shape
    opts = dict(
        dark_pixel_threshold=vlm_ocr.DEFAULT_DARK_PIXEL_THRESHOLD,
        roi_margin=vlm_ocr.DEFAULT_INK_ROI_MARGIN,
        cell_px=vlm_ocr.DEFAULT_UNCOVERED_CELL_PX,
        min_region_ink=vlm_ocr.DEFAULT_MIN_UNCOVERED_INK,
    )
    opts.update(overrides)
    return vlm_ocr.ink_coverage(gray, elements, w, h, **opts)


def test_roi_bounds_crops_all_four_sides_by_default():
    assert vlm_ocr.roi_bounds((1000, 500), 0.1) == (50, 100, 450, 900)


def test_roi_bounds_vertical_false_keeps_the_full_height():
    """Cropping the top and bottom by a fixed fraction removed the header and the
    footer, which are content. The scanner bed shows at the top and bottom too,
    but it is cut by measurement instead -- see bed_border_rows below."""
    assert vlm_ocr.roi_bounds((1000, 500), 0.1, vertical=False) == (50, 0, 450, 1000)


def test_page_stats_still_use_the_central_crop():
    """The blank thresholds were swept against the full crop. Ink placed only in
    the top band must stay invisible to page_ink_stats even though coverage now
    sees it."""
    gray = blank_page(h=1000, w=500)
    ink(gray, 100, 10, 400, 60)
    stats = vlm_ocr.page_ink_stats(gray, vlm_ocr.DEFAULT_DARK_PIXEL_THRESHOLD, 0.1)
    assert stats["dark_fraction"] == 0.0


def test_header_ink_counts_toward_coverage():
    """A page-header the model DID box now measures as covered instead of
    recording no ink at all."""
    gray = blank_page(h=1000, w=500)
    ink(gray, 100, 10, 400, 60)          # header band, above the old ROI top
    ink(gray, 100, 400, 400, 500)        # body
    result = coverage(gray, [
        {"bbox": [90, 5, 410, 65], "category": "Page-header", "text": "THE CITY OF NEW YORK"},
        {"bbox": [90, 395, 410, 505], "category": "Text", "text": "WHEREAS, a thing happened;"},
    ])
    assert result["roi"][1] == 0
    assert result["roi"][3] == 1000
    assert result["covered_fraction"] == 1.0
    header = [e for e in result["uncovered_regions"]]
    assert header == []


def test_header_element_gets_measurable_ink():
    """Before, an element out in the cropped band recorded `ink: None` and was
    excluded from the per-element density checks entirely."""
    gray = blank_page(h=1000, w=500)
    ink(gray, 100, 10, 400, 60)
    elements = [{"bbox": [90, 5, 410, 65], "category": "Page-header", "text": "CITY OF NEW YORK"}]
    coverage(gray, elements)
    assert elements[0]["ink"] is not None
    assert elements[0]["ink"]["ink_px"] > 0


def test_uncovered_header_ink_is_reported():
    """The other direction: ink in the header the model returned NO box for is a
    real miss, and used to be invisible."""
    gray = blank_page(h=1000, w=500)
    ink(gray, 100, 10, 400, 60)
    result = coverage(gray, [{"bbox": [0, 0, 1, 1], "category": "Text", "text": "x"}])
    assert result["uncovered_regions"]
    assert result["uncovered_regions"][0]["bbox"][1] < 100


def rule(gray, x1, y1, x2, thickness=3):
    """A printed horizontal rule: long, and only a few pixels thick."""
    gray[y1:y1 + thickness, x1:x2] = 0


def text_line(gray, x1, y1, x2, height=28, stride=18, glyph=11):
    """A line of type: strokes twice as tall as a rule, with gaps between."""
    for x in range(x1, x2, stride):
        gray[y1:y1 + height, x:x + glyph] = 0


def test_a_horizontal_rule_is_not_reported():
    """The line under a masthead. The model was right not to transcribe it."""
    gray = blank_page(h=1000, w=900)
    ink(gray, 300, 400, 600, 460)        # a title the model DID box
    rule(gray, 60, 300, 840)             # the rule, well clear of it
    result = coverage(gray, [
        {"bbox": [295, 395, 605, 465], "category": "Title", "text": "OFFICE OF THE MAYOR"},
    ])
    assert result["uncovered_regions"] == []
    assert result["rule_px"] > 0


def test_a_vertical_rule_is_not_reported():
    """A column border down the side of a City Record entry."""
    gray = blank_page(h=1000, w=900)
    gray[200:800, 120:123] = 0
    result = coverage(gray, [])
    assert result["uncovered_regions"] == []
    assert result["rule_px"] > 0


def test_a_wandering_rule_is_still_removed():
    """The case that broke the previous, whole-group test: these rules are
    scanned, so they dip and wave. Detection is local for exactly this."""
    gray = blank_page(h=1000, w=900)
    for x in range(60, 840):
        drift = int(18 * np.sin(x / 260))
        gray[400 + drift:403 + drift, x] = 0
    result = coverage(gray, [])
    assert result["uncovered_regions"] == []
    assert result["rule_px"] > 0


def test_a_dropped_line_of_text_is_still_reported():
    """The whole point of the metric. Type has gaps between words and strokes
    twice as tall as a rule, and must survive the filter."""
    gray = blank_page(h=1000, w=900)
    text_line(gray, 100, 400, 800)
    result = coverage(gray, [])
    assert len(result["uncovered_regions"]) == 1
    assert result["rule_px"] == 0


def test_a_rule_does_not_chain_distant_ink_into_one_box():
    """The reported-box bug this replaced. A rule running the page width used to
    connect ink at one end to ink at the other, and the group's box then covered
    everything between -- text the model had boxed included."""
    gray = blank_page(h=1000, w=900)
    rule(gray, 60, 500, 840)
    text_line(gray, 80, 300, 260)        # a dropped line at the left
    text_line(gray, 640, 700, 820)       # and another, far away at the right
    result = coverage(gray, [])
    boxes = result["uncovered_regions"]
    assert len(boxes) == 2
    # Neither box spans the gap the rule used to bridge.
    assert all(b["bbox"][2] - b["bbox"][0] < 400 for b in boxes)


def test_a_box_hugs_its_ink_rather_than_the_cell_grid():
    """A box is reported to a human as 'the model missed this'. It has to be
    the ink, not the 24px cells the ink happened to land in."""
    gray = blank_page(h=1000, w=900)
    text_line(gray, 205, 401, 500)
    box = coverage(gray, [])["uncovered_regions"][0]["bbox"]
    assert box[0] >= 205 and box[1] >= 401
    assert box[2] <= 512 and box[3] <= 430


def test_rule_ink_leaves_the_coverage_measurement():
    """Page furniture is neither returned nor dropped content, so it must not
    depress covered_fraction for a page whose text is complete."""
    gray = blank_page(h=1000, w=900)
    ink(gray, 300, 400, 600, 460)
    rule(gray, 60, 300, 840)
    result = coverage(gray, [
        {"bbox": [295, 395, 605, 465], "category": "Title", "text": "OFFICE OF THE MAYOR"},
    ])
    # Without the deduction the rule's ink would sit in the denominator and
    # drag this complete page below the flag threshold. A few pixels at the very
    # ends of the rule fall outside any whole window and survive, so the score
    # lands just short of 1 rather than exactly on it.
    assert result["covered_fraction"] > 0.99
    assert result["rule_px"] > 0
    assert result["ink_px"] == result["rule_px"] + result["covered_px"] + result["uncovered_px"]


def test_rules_inside_a_returned_box_are_left_alone():
    """rule_pixels only ever sees ink outside every box, so a rule under a title
    the model boxed stays counted as the covered ink it is."""
    gray = blank_page(h=1000, w=900)
    rule(gray, 100, 420, 800)
    result = coverage(gray, [
        {"bbox": [90, 410, 810, 440], "category": "Title", "text": "OFFICE OF THE MAYOR"},
    ])
    assert result["rule_px"] == 0
    assert result["covered_fraction"] == 1.0


def test_rule_thresholds_sit_between_the_two_populations():
    """Pinned because the whole filter rests on this gap, measured on real
    pages at 200 DPI: a rule is inked across 100% of its window with a 2-5px
    stroke, a line of type across 65-94% with a 5-9px stroke."""
    assert vlm_ocr.LINE_MIN_COLUMN_SHARE == 0.9
    assert vlm_ocr.LINE_MAX_STROKE_PX == 5.0
    assert vlm_ocr.LINE_WINDOW_PX == 144
    assert vlm_ocr.LINE_BAND_PX == 12


# --------------------------------------------------------------------------- #
# The scanner-bed band                                                          #
# --------------------------------------------------------------------------- #

def test_bed_border_rows_reads_zero_on_a_page_with_no_bed():
    """Every born-digital page, and most loose-sheet scans."""
    mask = np.zeros((1000, 500), dtype=bool)
    mask[400:500, 100:400] = True
    assert vlm_ocr.bed_border_rows(mask) == (0, 0)


def test_bed_border_rows_measures_each_edge_independently():
    """The band's depth differs head to foot -- on the Wagner memoranda it runs
    about 80 rows at the head and 110 at the foot -- so each edge is measured."""
    mask = np.zeros((1000, 500), dtype=bool)
    mask[:40] = True
    mask[-110:] = True
    assert vlm_ocr.bed_border_rows(mask) == (40, 110)


def test_the_bed_trim_is_capped_so_a_black_page_fails_loudly():
    """A page that really is mostly black must fail the coverage check, not have
    its content cropped away one row at a time until it passes."""
    mask = np.ones((1000, 500), dtype=bool)
    cap = int(1000 * vlm_ocr.BED_MAX_TRIM_FRACTION)
    assert vlm_ocr.bed_border_rows(mask) == (cap, cap)


def test_the_scanner_bed_band_leaves_the_coverage_measurement():
    """The regression this guards.

    Keeping the full page height let the bed into the denominator: on page 12 of
    the Wagner memoranda it tripled ink_px (111,240 -> 391,326) while covered_px
    barely moved, and covered_fraction fell from 1.00 to 0.33. Every pre-1974
    record then built as needs-review.
    """
    gray = blank_page(h=1000, w=500)
    ink(gray, 0, 0, 500, 60)          # scanner bed, head
    ink(gray, 0, 940, 500, 1000)      # scanner bed, foot
    ink(gray, 100, 400, 400, 500)     # the body, which the model boxed
    result = coverage(gray, [
        {"bbox": [90, 395, 410, 505], "category": "Text", "text": "WHEREAS, a thing happened;"},
    ])
    assert result["roi"][1] == 60      # the ROI moved in past the band
    assert result["roi"][3] == 940
    assert result["covered_fraction"] == 1.0
    assert result["uncovered_regions"] == []


def test_a_dark_band_inside_the_page_is_not_trimmed():
    """The trim scans inward from each edge and stops at the first row that is
    not saturated, so a plate in the middle of a page stays in the measurement
    and reports as uncovered ink rather than disappearing."""
    gray = blank_page(h=1000, w=500)
    ink(gray, 0, 400, 500, 500)
    result = coverage(gray, [])
    assert result["roi"][1] == 0
    assert result["roi"][3] == 1000
    assert result["covered_fraction"] == 0.0
