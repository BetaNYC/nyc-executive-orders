#!/usr/bin/env python3
"""Phase E side-car — clip every OCR'd `Picture` region out of the source volume PDFs.

dots.ocr tags part of each page as `Picture` (city seals, mayoral signatures, the
occasional real figure), and everything downstream throws those regions away:
`volume_split.py` drops them from reading order and `vlm_ocr.element_to_markdown`
renders them as a `*[Picture - bbox ...]*` placeholder. This script turns each of
those bboxes back into an actual image, rendered fresh from the original PDF at a
DPI you choose, so the picture layer can be looked at instead of inferred.

Reads `sources/gpp/volumes/ocr/<volume-stem>/page_*.json` -- the one place page records
live, and the same input stage 2 consumes. A volume with no records there is reported
and skipped rather than silently contributing nothing; one that is mid-run contributes
the pages it has so far. (Point --ocr-root at a directory of the same shape to clip
from something else.)

Writes, under --out-dir (default `vlm-pics/`, gitignored):

    <volume-stem>/page_NNNN_pic_KK.png        one crop per Picture element
    <volume-stem>/page_NNNN_pic_KK_sig.png    ... one that looks like a signature
    <volume-stem>/page_NNNN_pic_KK_seal.png   ... one that looks like the city seal
    manifest.json                             every crop, joinable back to the page JSON

NNNN is the absolute 1-based PDF page; KK is the index among Picture elements on
that page, in element order. manifest.json is merge-updated -- a --volume run
rewrites only its own volume's entries and leaves the other thirteen alone.

The `_sig` and `_seal` suffixes are HEURISTICS, not model output. dots.ocr has one
`Picture` category and no more, so the signature at the foot of an order, the city
seal in the letterhead, the library's date stamp, a scanner-bed artifact and a
printed ornament all arrive indistinguishable. is_likely_signature() and
is_likely_seal() below separate the two worth naming, on shape, size, position, ink
density and place in reading order; anything they do not recognise stays untagged
rather than being forced into a bucket. On the O'Dwyer volume: 38 signatures, 56
seals, 15 untagged, out of 109 pictures.

Every measurement behind a tag is written to the manifest beside it, so a wrong call
is visible rather than baked in. Treat the tags as a filter for a human, not as
ground truth -- and re-check the thresholds on a volume whose scans look different,
the same caution the blank-page thresholds in `vlm_ocr` carry.

NO AUTHORIZATION GATE, and no GPU: this is PyMuPDF reading files already on disk,
so it is safe to run while a `run_full_vlm_pre_1974.sh` job is grinding.

    python scripts/run_picture_clips.py --dry-run
    python scripts/run_picture_clips.py --volume ODwyer
    python scripts/run_picture_clips.py --volume ODwyer --dpi 600 --pad 5

--ocr-dpi MUST match the DPI the OCR run rendered at (default 200, `vlm_ocr`'s own
default). It is not the output resolution -- it is how the model's coordinates are
interpreted. Get it wrong and every crop lands somewhere else on the page.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders.build_pre1974 import load_page_records, load_volumes  # noqa: E402
from nyc_executive_orders.vlm_ocr import DEFAULT_DPI, rescale_bbox, smart_resize  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VOLUMES_JSON = REPO_ROOT / "sources" / "gpp" / "volumes.json"
DEFAULT_OCR_ROOT = REPO_ROOT / "sources" / "gpp" / "volumes" / "ocr"
DEFAULT_OUT_DIR = REPO_ROOT / "vlm-pics"
DEFAULT_CROP_DPI = 300
DEFAULT_PAD_PCT = 2.0

# --------------------------------------------------------------------------- #
# Tag thresholds -- see is_likely_signature() / is_likely_seal() for what these  #
# mean and how they were calibrated.                                            #
# --------------------------------------------------------------------------- #
SIG_MIN_Y_CENTER = 0.40
SIG_MIN_ASPECT = 1.2
SIG_MIN_WIDTH_FRAC = 0.12
SIG_MAX_INK_DENSITY = 0.15
SIG_MIN_TAIL_FRAC = 0.6

SEAL_MIN_ASPECT = 0.75
SEAL_MAX_ASPECT = 1.05
SEAL_MIN_WIDTH_FRAC = 0.04
SEAL_MAX_WIDTH_FRAC = 0.15
SEAL_MIN_HEIGHT_FRAC = 0.03


def page_rotation(record: dict) -> int:
    """Degrees clockwise `vlm_ocr` rotated this page before the model saw it.

    Absent on page JSON written before rotation handling existed, which is the
    same thing as 0 for those volumes: the twelve portrait volumes were never
    rotated, and the two landscape ones have to be re-OCR'd to gain the key at
    all. Anything other than a right angle is treated as 0 rather than trusted.
    """
    rot = (record.get("rotation") or {}).get("applied_cw", 0)
    return rot if rot in (90, 180, 270) else 0


def page_geometry(page: fitz.Page, ocr_dpi: int, rotation_cw: int = 0) -> tuple[int, int, int, int, float]:
    """``(render_w, render_h, model_w, model_h, zoom)`` for one page.

    The render size is what `vlm_ocr.render_pdf_pages` would have produced at this
    DPI -- computed, not rendered: the same arithmetic PyMuPDF does inside
    ``get_pixmap(dpi=...)``, so it reproduces the raw PNG's size exactly. The model
    size is the smart_resize'd copy dots.ocr actually saw, which is the space its
    coordinates live in.

    On a rotated page every one of those is measured in the ROTATED frame, because
    that is the image the model was given: a quarter turn swaps the render's width
    and height, and smart_resize then rounds and area-caps the swapped shape, which
    is not the same as swapping its unrotated result. Getting this wrong scales
    every bbox by the page's aspect ratio -- a visibly wrong crop, not a subtle one.
    """
    zoom = ocr_dpi / 72.0
    irect = (page.rect * fitz.Matrix(zoom, zoom)).irect
    render_w, render_h = irect.width, irect.height
    if rotation_cw in (90, 270):
        render_w, render_h = render_h, render_w
    # Argument-order trap: smart_resize is (height, width) both ways round,
    # rescale_bbox is (width, height). They are not interchangeable.
    model_h, model_w = smart_resize(render_h, render_w)
    return render_w, render_h, model_w, model_h, zoom


def unrotate_px(
    x1: float, y1: float, x2: float, y2: float, rotation_cw: int, render_w: int, render_h: int
) -> tuple[float, float, float, float]:
    """Map a rect from the rotated render frame back to the unrotated one.

    (render_w, render_h) is the ROTATED image's size -- the frame the input rect
    lives in. Rotating an image D degrees clockwise sends its top-left corner to
    the top-right (D=90), bottom-right (180), or bottom-left (270); this inverts
    that. Corners are re-min/maxed afterwards because each of these mappings flips
    at least one axis, which turns an ordered rect into an unordered one.
    """
    if rotation_cw == 90:
        # x0 = y1, y0 = render_w - x1
        a, b = (y1, render_w - x1), (y2, render_w - x2)
    elif rotation_cw == 180:
        a, b = (render_w - x1, render_h - y1), (render_w - x2, render_h - y2)
    elif rotation_cw == 270:
        # x0 = render_h - y1, y0 = x1
        a, b = (render_h - y1, x1), (render_h - y2, x2)
    else:
        return x1, y1, x2, y2
    return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])


def picture_signals(bbox: list[float], el: dict, index: int, n_elements: int,
                    model_w: int, model_h: int) -> dict:
    """The measurements the tag rules judge, all normalized to the page."""
    x1, y1, x2, y2 = bbox
    width, height = abs(x2 - x1), abs(y2 - y1)
    return {
        "y_center_frac": round((min(y1, y2) + height / 2) / model_h, 4),
        "aspect_ratio": round(width / height, 3) if height else 0.0,
        "width_frac": round(width / model_w, 4),
        "height_frac": round(height / model_h, 4),
        "tail_frac": round(index / (n_elements - 1), 4) if n_elements > 1 else 1.0,
        "ink_density": (el.get("ink") or {}).get("density"),
    }


def is_likely_signature(sig: dict) -> bool:
    """Does this Picture look like the signature at the foot of an order?

    dots.ocr has no signature category, so signatures land in `Picture` alongside
    the letterhead seal, the library's date stamp, and scanner-bed artifacts. What
    separates them, measured over the 109 Pictures in the O'Dwyer volume and
    checked against the crops by eye:

      * WHERE it sits. A signature is under the body text; the seal, the date stamp
        and the bed artifacts all sit up in the letterhead band. This is the single
        strongest signal -- it alone splits the volume almost cleanly.
      * SHAPE. Signature strokes run horizontally: they are wider than tall. Seals
        are square (~0.9), stamps roughly so (~1.1).
      * SIZE. Excludes the odd printed ornament, which is tiny.
      * INK. An engraved seal is dense (0.18-0.38); a signature is sparse strokes.
        Note there is deliberately NO ink FLOOR: faint pencil and light ink measure
        0.0 against the true-black threshold `ink` uses, and three of this volume's
        signatures do exactly that. A floor would drop them.
      * WHERE IN READING ORDER. The user-visible fact that motivates all this: a
        signature comes at the END of an order, so it is near the end of the page's
        element list.

    Deliberately a conjunction, not a score: every clause is doing real work here,
    and "likely" should mean each cheap check agreed, not that one strong signal
    outvoted two weak ones. Thresholds are calibrated on one volume's scans, like
    the blank-page ones in `vlm_ocr` -- re-check them on a volume that looks
    different before trusting the flag there.
    """
    density = sig["ink_density"]
    return (
        sig["y_center_frac"] >= SIG_MIN_Y_CENTER
        and sig["aspect_ratio"] >= SIG_MIN_ASPECT
        and sig["width_frac"] >= SIG_MIN_WIDTH_FRAC
        and (density is None or density <= SIG_MAX_INK_DENSITY)
        and sig["tail_frac"] >= SIG_MIN_TAIL_FRAC
    )


def is_likely_seal(sig: dict) -> bool:
    """Does this Picture look like the city seal in the letterhead?

    The seal is the most stereotyped thing in these volumes -- the same engraving
    reproduced on page after page -- and it shows up in the numbers. Across the
    O'Dwyer volume's 56 seals the aspect ratio spans 0.89-0.94 and the width spans
    7.5-11% of the page. So this rule is shape and size, and nothing else:

      * NEAR-SQUARE. Signatures are wide (>=1.2), the binding fragments that get
        caught at the page edge are tall and narrow (~0.44). The seal sits between.
      * SMALL. This is what separates a seal from the library's date stamp and the
        scanner-bed artifacts, which are near-square too (1.02-1.19) but more than
        twice as wide -- 19-24% of the page against the seal's 7.5-11%.
      * NOT A SLIVER. The floor excludes the printed ornaments, which are ~3% wide
        and ~1% tall.

    Deliberately NOT used, though both looked tempting:

      * Ink density. Seals run 0.0025-0.38 here -- the faint end is a light print or
        bleed-through of the same engraving, still plainly a seal in the crop. Any
        floor that excludes the artifacts also excludes those.
      * Position. Nearly every seal is up in the letterhead at the head of reading
        order, but not all: one sits mid-page at y=0.48, two-thirds of the way down
        the element list, and is a perfectly ordinary seal.

    Disjoint from is_likely_signature() by construction -- nothing can be both
    wider than 1.2 and narrower than 1.05 -- which classify_picture() relies on.
    """
    return (
        SEAL_MIN_ASPECT <= sig["aspect_ratio"] <= SEAL_MAX_ASPECT
        and SEAL_MIN_WIDTH_FRAC <= sig["width_frac"] <= SEAL_MAX_WIDTH_FRAC
        and sig["height_frac"] >= SEAL_MIN_HEIGHT_FRAC
    )


def classify_picture(sig: dict) -> str | None:
    """``"sig"``, ``"seal"``, or None -- the filename suffix and manifest tag.

    One tag per picture: the two rules are mutually exclusive on aspect ratio, and
    checking in order keeps that true by construction even if a threshold is later
    edited into an overlap. Everything else -- date stamps, scanner-bed artifacts,
    binding fragments, ornaments -- stays untagged rather than being forced into a
    bucket. On the O'Dwyer volume that is 38 signatures, 56 seals, 15 untagged.
    """
    if is_likely_signature(sig):
        return "sig"
    if is_likely_seal(sig):
        return "seal"
    return None


def clip_rect(
    bbox: list[float],
    page: fitz.Page,
    geom: tuple[int, int, int, int, float],
    pad_pct: float,
    rotation_cw: int = 0,
) -> tuple[fitz.Rect, tuple[float, ...], tuple[float, ...]]:
    """Map one model bbox onto the PDF page, padded and clamped.

    Returns ``(padded_clip_rect, bbox_in_render_px, bbox_in_pdf_points)``.

    The bbox is NOT in the rendered PNG's pixel space: dots.ocr sees a smart_resize'd
    copy of the page (dimensions rounded to multiples of 28, area capped) and reports
    coordinates in THAT space. Cropping the raw numbers is wrong by a fraction of a
    percent -- small, but it shaves the edge off a seal. So the chain is
    model space -> render pixels -> PDF points, exactly as `draw_overlay` does it
    for the QA overlays.

    On a rotated page there is one more link: the model's coordinates are relative
    to the upright image `vlm_ocr` rendered, and the PDF page is still sideways. So
    the chain becomes model space -> ROTATED render pixels -> unrotated render
    pixels -> PDF points. The returned render_px is in the unrotated frame, which
    is the one that lines up with the PDF.
    """
    orig_w, orig_h, resized_w, resized_h, zoom = geom
    x1, y1, x2, y2 = rescale_bbox(bbox, orig_w, orig_h, resized_w, resized_h)
    # The model sometimes hands back the corners the other way round.
    x1, x2 = min(x1, x2), max(x1, x2)
    y1, y2 = min(y1, y2), max(y1, y2)
    x1, y1, x2, y2 = unrotate_px(x1, y1, x2, y2, rotation_cw, orig_w, orig_h)
    render_px = (x1, y1, x2, y2)

    # The pixmap is measured from page.rect's top-left, which is not always (0, 0).
    px1 = page.rect.x0 + x1 / zoom
    py1 = page.rect.y0 + y1 / zoom
    px2 = page.rect.x0 + x2 / zoom
    py2 = page.rect.y0 + y2 / zoom
    pdf_pt = (px1, py1, px2, py2)

    pad_x = (px2 - px1) * pad_pct / 100.0
    pad_y = (py2 - py1) * pad_pct / 100.0
    rect = fitz.Rect(px1 - pad_x, py1 - pad_y, px2 + pad_x, py2 + pad_y) & page.rect
    return rect, render_px, pdf_pt


def pictures_in(record: dict) -> list[tuple[int, dict]]:
    """Every Picture element in a page record, as (index within `elements`, element).

    Blank/bleed-through pages carry `"skipped": "blank_or_bleedthrough"` and an empty
    element list; they are simply nothing to clip.
    """
    return [
        (i, el)
        for i, el in enumerate(record.get("elements") or [])
        if el.get("category") == "Picture"
    ]


def clip_volume(volume, args, out_root: Path) -> tuple[list[dict], dict]:
    """Clip one volume. Returns (manifest entries, per-volume counters)."""
    stats = {"pages": 0, "pictures": 0, "written": 0, "skipped": 0, "bad": 0,
             "signatures": 0, "seals": 0}
    ocr_dir = Path(args.ocr_root) / volume.stem
    if not ocr_dir.is_dir() or not any(ocr_dir.glob("page_*.json")):
        print(f"SKIP {volume.stem}: no page records at {ocr_dir} "
              f"(volume not OCR'd yet)", file=sys.stderr)
        stats["no_records"] = True
        return [], stats

    pdf_path = REPO_ROOT / volume.pdf_relpath
    if not pdf_path.exists():
        print(f"SKIP {volume.stem}: PDF not on disk at {pdf_path}", file=sys.stderr)
        stats["nopdf"] = True
        return [], stats

    records = [r for r in load_page_records(ocr_dir) if pictures_in(r)]
    stats["pages"] = len(records)
    stats["pictures"] = sum(len(pictures_in(r)) for r in records)
    if not stats["pictures"]:
        return [], stats

    # A dry run still walks the whole geometry -- it is arithmetic over a PDF page
    # tree, no rendering -- so the census it prints can include the signature count.
    vol_dir = out_root / volume.stem
    if not args.dry_run:
        vol_dir.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    doc = fitz.open(pdf_path)
    try:
        for record in records:
            page_no = record.get("page")
            if not isinstance(page_no, int) or not 1 <= page_no <= doc.page_count:
                print(f"  {volume.stem} page {page_no!r}: outside the PDF "
                      f"({doc.page_count} pages); skipped", file=sys.stderr)
                stats["bad"] += len(pictures_in(record))
                continue
            page = doc[page_no - 1]
            rotation_cw = page_rotation(record)
            geom = page_geometry(page, args.ocr_dpi, rotation_cw)
            n_elements = len(record.get("elements") or [])

            for k, (el_index, el) in enumerate(pictures_in(record)):
                bbox = el.get("bbox")
                if not bbox or len(bbox) != 4:
                    print(f"  {volume.stem} page {page_no} pic {k}: "
                          f"unusable bbox {bbox!r}; skipped", file=sys.stderr)
                    stats["bad"] += 1
                    continue

                rect, render_px, pdf_pt = clip_rect(bbox, page, geom, args.pad, rotation_cw)
                if rect.is_empty or rect.width <= 0 or rect.height <= 0:
                    print(f"  {volume.stem} page {page_no} pic {k}: bbox {bbox} maps to an "
                          f"empty rect on the page; skipped", file=sys.stderr)
                    stats["bad"] += 1
                    continue

                signals = picture_signals(bbox, el, el_index, n_elements, geom[2], geom[3])
                tag = classify_picture(signals)
                stats["signatures"] += tag == "sig"
                stats["seals"] += tag == "seal"
                if args.dry_run:
                    continue

                stem = f"page_{page_no:04d}_pic_{k:02d}"
                out_png = vol_dir / f"{stem}{'_' + tag if tag else ''}.png"
                # The same picture under a different tag, left behind by an earlier run
                # under different thresholds. Two files for one bbox would double-count
                # the volume, so drop them -- crops are regenerable scratch, and this
                # only ever touches names this script itself produces.
                for name in (f"{stem}.png", f"{stem}_sig.png", f"{stem}_seal.png"):
                    stale = vol_dir / name
                    if name != out_png.name and stale.exists():
                        stale.unlink()
                        if args.verbose:
                            print(f"  removed stale {name} (tag changed)")

                if out_png.exists() and not args.force:
                    stats["skipped"] += 1
                    with Image.open(out_png) as img:  # what is actually on disk
                        crop_px = list(img.size)
                else:
                    # Rotated the same way the OCR render was, so a signature
                    # crop off a sideways-scanned volume comes out upright like
                    # every other crop in vlm-pics/ -- the clip rect is in PDF
                    # space (still sideways), the saved PNG should not be.
                    matrix = fitz.Matrix(args.dpi / 72.0, args.dpi / 72.0)
                    if rotation_cw:
                        matrix = matrix * fitz.Matrix(rotation_cw)
                    pix = page.get_pixmap(matrix=matrix, clip=rect)
                    pix.save(out_png)
                    crop_px = [pix.width, pix.height]
                    stats["written"] += 1
                    if args.verbose:
                        print(f"  {out_png.relative_to(out_root)}  "
                              f"{pix.width}x{pix.height}px")

                entries.append({
                    "volume": volume.stem,
                    "pdf_path": volume.pdf_relpath,
                    "page": page_no,
                    "picture_index": k,
                    "element_index": el_index,
                    "image": f"{volume.stem}/{out_png.name}",
                    "tag": tag,
                    "shape_signals": signals,
                    "bbox_model": bbox,
                    "bbox_render_px": [round(v, 2) for v in render_px],
                    "bbox_pdf_pt": [round(v, 3) for v in pdf_pt],
                    "clip_pdf_pt": [round(v, 3) for v in (rect.x0, rect.y0, rect.x1, rect.y1)],
                    "crop_px": [int(v) for v in crop_px],
                    "ocr_dpi": args.ocr_dpi,
                    "dpi": args.dpi,
                    "pad_pct": args.pad,
                    "ink": el.get("ink"),
                    "logprob_stats": el.get("logprob_stats"),
                })
    finally:
        doc.close()

    return entries, stats


def merge_manifest(path: Path, entries: list[dict], touched: set[str]) -> int:
    """Rewrite `path` with this run's entries replacing only the volumes it touched.

    A `--volume Wagner_Orders` run must not drop the other thirteen volumes' crops
    from the manifest just because it did not look at them.
    """
    existing: list[dict] = []
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8")).get("pictures", [])
        except (json.JSONDecodeError, OSError) as exc:
            print(f"warning: {path} unreadable ({exc}); rewriting it from this run alone",
                  file=sys.stderr)
            existing = []

    kept = [e for e in existing if e.get("volume") not in touched]
    merged = sorted(kept + entries,
                    key=lambda e: (e.get("volume", ""), e.get("page", 0),
                                   e.get("picture_index", 0)))
    path.write_text(
        json.dumps({"pictures": merged}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return len(merged)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--volume", default=None,
                   help="Substring of the volume filename to clip (default: all).")
    p.add_argument("--volumes-json", default=str(DEFAULT_VOLUMES_JSON))
    p.add_argument("--ocr-root", default=str(DEFAULT_OCR_ROOT),
                   help="Where per-page JSON lives, one directory per volume stem.")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                   help="Where crops and manifest.json are written.")
    p.add_argument("--ocr-dpi", type=int, default=DEFAULT_DPI,
                   help=f"DPI the OCR run rendered at; must match it (default: {DEFAULT_DPI}).")
    p.add_argument("--dpi", type=int, default=DEFAULT_CROP_DPI,
                   help=f"Output resolution of the crops (default: {DEFAULT_CROP_DPI}).")
    p.add_argument("--pad", type=float, default=DEFAULT_PAD_PCT,
                   help=f"Padding per side, percent of bbox size "
                        f"(default: {DEFAULT_PAD_PCT}; 0 = the exact bbox).")
    p.add_argument("--force", action="store_true",
                   help="Re-render crops that already exist (default: leave them be).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report the Picture census per volume; render nothing.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Print a line per crop.")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    for flag, value in (("--ocr-dpi", args.ocr_dpi), ("--dpi", args.dpi)):
        if value <= 0:
            print(f"error: {flag} wants a positive integer, got {value}", file=sys.stderr)
            return 2
    if args.pad < 0:
        print(f"error: --pad cannot be negative, got {args.pad}", file=sys.stderr)
        return 2

    volumes = load_volumes(args.volumes_json)
    if args.volume:
        volumes = [v for v in volumes if args.volume.lower() in v.filename.lower()]
        if not volumes:
            print(f"error: no volume matches {args.volume!r}", file=sys.stderr)
            return 2

    out_root = Path(args.out_dir)
    if not args.dry_run:
        out_root.mkdir(parents=True, exist_ok=True)

    all_entries: list[dict] = []
    touched: set[str] = set()
    totals = {"pages": 0, "pictures": 0, "written": 0, "skipped": 0, "bad": 0,
              "signatures": 0, "seals": 0}
    no_records, nopdf = [], []

    for volume in volumes:
        entries, stats = clip_volume(volume, args, out_root)
        if stats.pop("no_records", False):
            no_records.append(volume.stem)
            continue
        if stats.pop("nopdf", False):
            nopdf.append(volume.stem)
            continue

        touched.add(volume.stem)
        all_entries.extend(entries)
        for key in totals:
            totals[key] += stats[key]
        print(f"{volume.stem}: {stats['pictures']} picture(s) on {stats['pages']} page(s)"
              + f", {stats['signatures']} signature(s), {stats['seals']} seal(s)"
              + (f", {stats['written']} written" if not args.dry_run else "")
              + (f", {stats['skipped']} already present" if stats["skipped"] else "")
              + (f", {stats['bad']} unusable" if stats["bad"] else ""))

    print()
    if args.dry_run:
        print(f"dry run: {totals['pictures']} picture(s) across {totals['pages']} page(s) "
              f"in {len(touched)} volume(s) would be clipped to {out_root}, "
              f"{totals['signatures']} tagged as likely signatures, "
              f"{totals['seals']} as likely seals")
    else:
        if touched:
            total = merge_manifest(out_root / "manifest.json", all_entries, touched)
            print(f"{totals['written']} crop(s) written"
                  + (f", {totals['skipped']} already present" if totals["skipped"] else "")
                  + f" -> {out_root}")
            print(f"of {totals['pictures']} picture(s): {totals['signatures']} likely "
                  f"signature(s) (*_sig.png), {totals['seals']} likely seal(s) "
                  f"(*_seal.png), "
                  f"{totals['pictures'] - totals['signatures'] - totals['seals']} untagged")
            print(f"manifest: {out_root / 'manifest.json'} ({total} entries)")
        else:
            print(f"nothing clipped: no volume had page records under {args.ocr_root}")

    if no_records:
        print(f"{len(no_records)} volume(s) not OCR'd yet: {', '.join(no_records)}",
              file=sys.stderr)
    if nopdf:
        print(f"{len(nopdf)} volume(s) missing their PDF: {', '.join(nopdf)}", file=sys.stderr)
    if totals["bad"]:
        print(f"{totals['bad']} picture(s) had unusable geometry (see above)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
