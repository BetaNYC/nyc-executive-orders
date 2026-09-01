#!/usr/bin/env python3
"""Recompute `ink_coverage` in page records that already exist.

The ink metric is the one QA signal in a page record that needs no model. It
reconciles the ink on the rendered page against the bboxes the model returned,
and both of its inputs survive the run: the bboxes are in the record, and the
page raster re-renders from the committed PDF deterministically (same DPI, same
rotation, both recorded). So when the metric itself changes, the records can be
brought up to date without a single token of inference.

What changed, and why a backfill was needed:

  * Coverage now measures the FULL PAGE HEIGHT (roi_bounds(vertical=False)).
    The old central ROI cropped 12% off the top and bottom, which is exactly
    where a page header and footer live -- so their ink was in neither the
    numerator nor the denominator, and an element out there recorded no ink at
    all. The side crop stays: the binding and the scanner-bed edge that motivate
    it run down the sides.

  * RULE INK is cut out of the mask before regions are grouped, and out of the
    coverage measurement. A masthead line or a column border is ink the model
    was right not to transcribe. Cutting it early also stops it chaining
    distant ink into one group.

  * A REPORTED BOX now hugs its group's own ink instead of the 24px cell grid,
    so opening a flag no longer shows a page of text the model did box.

Both directions are rewritten -- `ink_coverage` and each element's `ink` -- so
the record stays internally consistent. Nothing else in the record is touched:
the text, the token logprobs, and the rotation are the run's output and are not
this script's to revise.

Usage:

    uv run python scripts/recompute_ink_coverage.py --dry-run
    uv run python scripts/recompute_ink_coverage.py
    uv run python scripts/recompute_ink_coverage.py --collection post1974
    uv run python scripts/recompute_ink_coverage.py --unit 1974-EO-001
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from nyc_executive_orders import vlm_ocr  # noqa: E402

PAGE_JSON = "page_{:04d}.json"


@dataclass
class Unit:
    """One document or one volume: a directory of page records and the PDF they
    were made from."""

    collection: str
    name: str
    ocr_dir: Path
    pdf: Path

    def page_records(self) -> list[tuple[int, Path]]:
        pages = []
        for path in sorted(self.ocr_dir.glob("page_*.json")):
            try:
                pages.append((int(path.stem.split("_")[1]), path))
            except (IndexError, ValueError):
                continue
        return pages


def post1974_units(records_json: Path, ocr_root: Path) -> list[Unit]:
    """Documents that have at least one page record. The worklist comes from
    corpus/eo.json, the same place run_post1974_ocr.py takes it from."""
    records = json.loads(records_json.read_text())
    units = []
    for rec in records:
        eo_id, pdf_path = rec.get("eo_id"), rec.get("pdf_path")
        if not eo_id or not pdf_path:
            continue
        ocr_dir = ocr_root / str(rec.get("year")) / eo_id
        if not ocr_dir.is_dir():
            continue
        units.append(Unit("post1974", eo_id, ocr_dir, REPO_ROOT / pdf_path))
    return units


def pre1974_units(volumes_json: Path, ocr_root: Path) -> list[Unit]:
    """The bound volumes. A volume's OCR directory is named for its PDF's
    stem -- see sources/gpp/volumes/README.md."""
    volumes = json.loads(volumes_json.read_text()).get("volumes", [])
    units = []
    for vol in volumes:
        for rel in vol.get("local_paths") or []:
            pdf = REPO_ROOT / rel
            ocr_dir = ocr_root / pdf.stem
            if ocr_dir.is_dir():
                units.append(Unit("pre1974", pdf.stem, ocr_dir, pdf))
    return units


def recompute_page(gray, record: dict, opts) -> dict:
    """The new ink_coverage for one page, with each element's `ink` rewritten in
    place.

    An empty element list is measured, not skipped. A page whose JSON failed to
    parse returned no boxes at all, so its coverage is 0 and every group of ink
    on it is uncovered -- which is exactly what the metric should say, and what
    the run itself recorded for those pages.
    """
    elements = record.get("elements") or []
    h, w = gray.shape
    resized_h, resized_w = vlm_ocr.smart_resize(h, w)
    return vlm_ocr.ink_coverage(
        gray,
        elements,
        resized_w,
        resized_h,
        opts.dark_pixel_threshold,
        opts.roi_margin,
        opts.cell_px,
        opts.min_region_ink,
    )


def process_unit(unit: Unit, opts) -> dict:
    """Re-render every recorded page of one unit and rewrite its ink metrics.

    Pages are rendered one at a time into a scratch directory that is removed
    when the unit is done: a 200 DPI page is ~7 megapixels, and there is no
    reason to hold a whole volume of them.
    """
    out = {
        "unit": unit.name,
        "collection": unit.collection,
        "updated": 0,
        "skipped": 0,
        "regions_before": 0,
        "regions_after": 0,
        "rules": 0,
        "errors": [],
    }
    pages = unit.page_records()
    if not pages:
        return out
    if not unit.pdf.exists():
        out["errors"].append(f"{unit.name}: no PDF at {unit.pdf}")
        out["skipped"] += len(pages)
        return out

    with tempfile.TemporaryDirectory(prefix="ink-recompute-") as tmp:
        tmp_dir = Path(tmp)
        for page_no, json_path in pages:
            try:
                record = json.loads(json_path.read_text())
            except json.JSONDecodeError as exc:
                out["errors"].append(f"{unit.name} p{page_no}: unreadable record ({exc})")
                out["skipped"] += 1
                continue
            if record.get("skipped") or (
                not record.get("elements") and not record.get("ink_coverage")
            ):
                # The blank/bleed-through classifier caught this page before any
                # model ran, or the run measured no ink on it. Either way there
                # is no ink_coverage block to bring up to date.
                out["skipped"] += 1
                continue

            # Force the rotation the run recorded rather than re-detecting it.
            # detect_page_rotation is an aspect-ratio heuristic; re-running it
            # would usually agree, and "usually" is not good enough when every
            # bbox in the record is relative to the raster it produced.
            applied = int((record.get("rotation") or {}).get("applied_cw", 0))
            rendered = vlm_ocr.render_pdf_pages(
                unit.pdf, tmp_dir, opts.dpi, 1, start_page=page_no, rotate=str(applied)
            )
            if not rendered:
                out["errors"].append(f"{unit.name} p{page_no}: page not in PDF")
                out["skipped"] += 1
                continue
            image_path = rendered[0][0]
            try:
                gray = vlm_ocr.load_gray(image_path)
                coverage = recompute_page(gray, record, opts)
            finally:
                image_path.unlink(missing_ok=True)
            before = len((record.get("ink_coverage") or {}).get("uncovered_regions") or [])
            out["regions_before"] += before
            out["regions_after"] += len(coverage["uncovered_regions"])
            out["rules"] += coverage["rule_px"]
            record["ink_coverage"] = coverage
            if not opts.dry_run:
                json_path.write_text(json.dumps(record, indent=2) + "\n")
            out["updated"] += 1
    return out


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Recompute ink_coverage in existing page records. No model is loaded.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--collection",
        choices=["post1974", "pre1974", "all"],
        default="all",
        help="Which OCR run to rewrite (default: all).",
    )
    p.add_argument("--unit", action="append", default=[],
                   help="Only this document or volume, by eo-id or volume stem (repeatable).")
    p.add_argument("--limit", type=int, default=None, help="Stop after this many units.")
    p.add_argument("--dry-run", action="store_true",
                   help="Measure and report, but write nothing back.")
    p.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 4) // 2),
                   help="Units to process in parallel. Rendering is the cost here.")
    p.add_argument("--dpi", type=int, default=vlm_ocr.DEFAULT_DPI,
                   help=f"The DPI the run rendered at (default: {vlm_ocr.DEFAULT_DPI}). "
                        "Every bbox in the records is relative to a raster of this "
                        "resolution -- changing it invalidates the whole measurement.")
    p.add_argument("--dark-pixel-threshold", type=int,
                   default=vlm_ocr.DEFAULT_DARK_PIXEL_THRESHOLD)
    p.add_argument("--roi-margin", type=float, default=vlm_ocr.DEFAULT_INK_ROI_MARGIN,
                   help="Side crop fraction. Applied to the sides only now; the full "
                        "page height is always measured.")
    p.add_argument("--cell-px", type=int, default=vlm_ocr.DEFAULT_UNCOVERED_CELL_PX)
    p.add_argument("--min-region-ink", type=int, default=vlm_ocr.DEFAULT_MIN_UNCOVERED_INK)
    p.add_argument("--post1974-ocr-dir", type=Path, default=REPO_ROOT / "sources/ocr")
    p.add_argument("--pre1974-ocr-dir", type=Path,
                   default=REPO_ROOT / "sources/gpp/volumes/ocr")
    p.add_argument("--eo-records", type=Path, default=REPO_ROOT / "corpus/eo.json")
    p.add_argument("--volumes-json", type=Path,
                   default=REPO_ROOT / "sources/gpp/volumes.json")
    return p


def main() -> None:
    args = build_arg_parser().parse_args()

    units: list[Unit] = []
    if args.collection in ("post1974", "all"):
        units += post1974_units(args.eo_records, args.post1974_ocr_dir)
    if args.collection in ("pre1974", "all"):
        units += pre1974_units(args.volumes_json, args.pre1974_ocr_dir)
    if args.unit:
        wanted = set(args.unit)
        units = [u for u in units if u.name in wanted]
        missing = wanted - {u.name for u in units}
        if missing:
            sys.exit(f"error: no page records for: {', '.join(sorted(missing))}")
    if args.limit:
        units = units[: args.limit]
    if not units:
        sys.exit("error: nothing selected")

    total_pages = sum(len(u.page_records()) for u in units)
    mode = "DRY RUN -- nothing will be written" if args.dry_run else "rewriting records"
    print(f"ink_coverage recompute: {len(units)} unit(s), {total_pages} page record(s); {mode}")

    totals = {"updated": 0, "skipped": 0, "regions_before": 0, "regions_after": 0,
              "rules": 0}
    errors: list[str] = []
    done = 0
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        futures = {pool.submit(process_unit, u, args): u for u in units}
        for future in as_completed(futures):
            result = future.result()
            for key in totals:
                totals[key] += result[key]
            errors += result["errors"]
            done += 1
            if done % 50 == 0 or done == len(units):
                print(f"  {done}/{len(units)} units · {totals['updated']} pages rewritten",
                      flush=True)

    print("\ndone.")
    print(f"  pages rewritten:      {totals['updated']}")
    print(f"  pages skipped:        {totals['skipped']}  (blank, or no elements)")
    print(f"  uncovered regions:    {totals['regions_before']} -> {totals['regions_after']}")
    print(f"  rule ink removed:     {totals['rules']} px  (masthead lines, column borders)")
    if errors:
        print(f"\n{len(errors)} error(s):")
        for line in errors[:20]:
            print(f"  {line}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more")
        sys.exit(1)


if __name__ == "__main__":
    main()
