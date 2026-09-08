#!/usr/bin/env python3
"""Phase E repair — stamp the `rotation` key onto page JSON written before
`vlm_ocr` grew rotation handling.

Every page record a current `vlm_ocr` run writes carries::

    "rotation": {
      "applied_cw": 0,
      "source": "auto-aspect",
      "pdf_page_size": [612.0, 792.0]
    }

`applied_cw: 0` is recorded rather than omitted precisely so a consumer can tell
"checked, already upright" from "this JSON predates rotation handling" — an
absent key is the only way to say the latter (see
`vlm_ocr.render_pdf_pages`). Every page JSON on disk right now is in that second
state: all fourteen volumes were OCR'd before the fix landed. Twelve of them
were rendered upright anyway, because their pages already are, so for those the
missing key is purely a labelling gap and this script closes it in place.

The other two volumes are NOT backfillable and this script refuses them:

    1968-01-10_1969-12-29_Lindsay_Orders    389/389 pages landscape
    1970-01-01_1971-12-09_Lindsay_Orders    422/422 pages landscape

They were scanned sideways, their pre-fix OCR ran on sideways renders, and no
edit to a JSON file changes that — they have to be re-OCR'd (the pipeline's
`--rotate auto` now turns them 90° clockwise) and gain the key from the run
itself. Writing `applied_cw: 0` onto them would assert something false, so the
guard below re-derives every page's orientation from the PDF with
`vlm_ocr.detect_page_rotation` and skips any volume that is not fully upright.
That check is the authority here, not the two names above; the names are what
it currently reports.

`source` is written as "auto-aspect-backfill", not "auto-aspect": these pages
were rendered by a build with no rotation code at all, and the 0 is this
script's after-the-fact finding about the PDF rather than something the run
observed. Nothing downstream branches on `source` — `run_picture_clips.py`, the
one consumer, reads only `applied_cw` — so the distinction costs nothing and
keeps the record honest. `pdf_page_size` is read per page from the PDF, the
same [width, height] at 2dp a real run records.

Touched, for each upright volume: `sources/gpp/volumes/ocr/<stem>/page_NNNN.json`,
the one place page records live.

Idempotent and offline: a record that already has `rotation` is left exactly as
it is, so re-running after a volume is re-OCR'd is a no-op on the new pages. No
GPU and no model — safe to run while a `run_full_vlm_pre_1974.sh` job is going,
though a volume it is actively writing is better done after it finishes.

    python scripts/backfill_page_rotation.py --dry-run
    python scripts/backfill_page_rotation.py
    python scripts/backfill_page_rotation.py --volume Wagner_Orders
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import fitz  # PyMuPDF

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders.build_pre1974 import load_volumes  # noqa: E402
from nyc_executive_orders.vlm_ocr import detect_page_rotation  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VOLUMES_JSON = REPO_ROOT / "sources" / "gpp" / "volumes.json"
DEFAULT_OCR_ROOT = REPO_ROOT / "sources" / "gpp" / "volumes" / "ocr"

# See the module docstring: deliberately distinct from the "auto-aspect" a live
# run writes, because this 0 is a later finding about the PDF, not a run's.
BACKFILL_SOURCE = "auto-aspect-backfill"

# `rotation` is set right after `page_stats` and before `ink_coverage` in a real
# run's page record. Matching that keeps a backfilled file byte-comparable with
# a re-OCR'd one and keeps the git diff to a single added block.
ROTATION_AFTER = "page_stats"


def page_sizes(pdf_path: Path) -> tuple[list[tuple[float, float]], list[int]]:
    """Per-page ``(width, height)`` at 2dp and the rotation each page needs.

    The rotation comes from `vlm_ocr.detect_page_rotation`, the same function
    the pipeline renders by, so this script's verdict on a volume cannot drift
    from what a real run would do to it.
    """
    doc = fitz.open(pdf_path)
    try:
        sizes = [(round(p.rect.width, 2), round(p.rect.height, 2)) for p in doc]
        needed = [detect_page_rotation(p) for p in doc]
        return sizes, needed
    finally:
        doc.close()


def stamp(record: dict, size: tuple[float, float]) -> bool:
    """Add `rotation` to one page record in place. True if it changed.

    Rebuilds the dict rather than assigning, to place the key where a live run
    puts it. A record that already carries the key is left untouched — including
    one carrying a nonzero `applied_cw`, which is a re-OCR'd page and none of
    this script's business.
    """
    if "rotation" in record:
        return False
    rotation = {
        "applied_cw": 0,
        "source": BACKFILL_SOURCE,
        "pdf_page_size": [size[0], size[1]],
    }
    rebuilt: dict = {}
    for key, value in record.items():
        rebuilt[key] = value
        if key == ROTATION_AFTER:
            rebuilt["rotation"] = rotation
    if "rotation" not in rebuilt:  # no page_stats (older/failed page): append
        rebuilt["rotation"] = rotation
    record.clear()
    record.update(rebuilt)
    return True


def page_number(record: dict, path: Path) -> int | None:
    """The absolute 1-based PDF page a record describes.

    `page` is authoritative — page numbering is absolute across resumed runs —
    with the filename as the fallback for a record that somehow lacks it.
    """
    page_no = record.get("page")
    if isinstance(page_no, int):
        return page_no
    stem = path.stem.removeprefix("page_")
    return int(stem) if stem.isdigit() else None


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"  {path}: unreadable ({exc}); skipped", file=sys.stderr)
        return None


def write(path: Path, payload) -> None:
    # indent=2, ensure_ascii=False, no trailing newline -- exactly how vlm_ocr
    # writes these, so an untouched neighbour file stays diff-identical.
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def backfill_page_files(directory: Path, sizes: list[tuple[float, float]],
                        stats: dict, dry_run: bool) -> None:
    """Stamp every ``page_NNNN.json`` in one directory."""
    for path in sorted(directory.glob("page_*.json")):
        record = load(path)
        if not isinstance(record, dict):
            continue
        stats["pages"] += 1
        if "rotation" in record:
            stats["already"] += 1
            continue
        page_no = page_number(record, path)
        if page_no is None or not 1 <= page_no <= len(sizes):
            print(f"  {path}: page {page_no!r} outside the PDF "
                  f"({len(sizes)} pages); skipped", file=sys.stderr)
            stats["bad"] += 1
            continue
        stamp(record, sizes[page_no - 1])
        stats["stamped"] += 1
        if not dry_run:
            write(path, record)


def backfill_volume(volume, args) -> dict:
    """One volume's page records."""
    stats = {"pages": 0, "stamped": 0, "already": 0, "bad": 0,
             "needs_rotation": 0, "targets": 0}

    pdf_path = REPO_ROOT / volume.pdf_relpath
    if not pdf_path.exists():
        print(f"SKIP {volume.stem}: PDF not on disk at {pdf_path}", file=sys.stderr)
        stats["nopdf"] = True
        return stats

    sizes, needed = page_sizes(pdf_path)
    n_rotated = sum(1 for deg in needed if deg)
    if n_rotated:
        # The whole point of the guard: this volume's committed OCR was produced
        # from sideways renders, so its pages are not "0 rotation, key missing" --
        # they are wrong until re-OCR'd, and a 0 here would hide that.
        angles = "/".join(str(a) for a in sorted({deg for deg in needed if deg}))
        print(f"SKIP {volume.stem}: {n_rotated}/{len(needed)} page(s) need "
              f"{angles}° clockwise -- scanned sideways, so its OCR predates the "
              f"fix AND ran on the wrong orientation. Re-OCR it "
              f"(run_full_vlm_pre_1974.sh --volume {volume.stem} --force); "
              f"the run writes the key itself.", file=sys.stderr)
        stats["needs_rotation"] = n_rotated
        return stats

    directory = Path(args.ocr_root) / volume.stem
    if directory.is_dir():
        stats["targets"] += 1
        backfill_page_files(directory, sizes, stats, args.dry_run)

    if not stats["targets"]:
        print(f"SKIP {volume.stem}: no page JSON found under "
              f"{Path(args.ocr_root) / volume.stem} (volume not OCR'd yet)",
              file=sys.stderr)
    return stats


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--volumes-json", default=str(DEFAULT_VOLUMES_JSON))
    p.add_argument("--ocr-root", default=str(DEFAULT_OCR_ROOT),
                   help="Per-volume page records (default: %(default)s)")
    p.add_argument("--volume", default=None,
                   help="Substring of the volume stem; default is all fourteen")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would change and write nothing")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    volumes = load_volumes(args.volumes_json)
    if args.volume:
        volumes = [v for v in volumes if args.volume in v.stem]
        if not volumes:
            print(f"no volume matching {args.volume!r}", file=sys.stderr)
            return 1

    mode = "DRY RUN -- nothing written" if args.dry_run else "writing in place"
    print(f"backfilling rotation={{applied_cw: 0, source: {BACKFILL_SOURCE!r}}} "
          f"over {len(volumes)} volume(s) -- {mode}\n")

    totals = {"pages": 0, "stamped": 0, "already": 0, "bad": 0}
    skipped = []
    for volume in sorted(volumes, key=lambda v: v.stem):
        stats = backfill_volume(volume, args)
        for key in totals:
            totals[key] += stats.get(key, 0)
        if stats.get("needs_rotation") or stats.get("nopdf") or not stats["targets"]:
            skipped.append(volume.stem)
            continue
        print(f"{volume.stem:55s} {stats['stamped']:5d} stamped, "
              f"{stats['already']:5d} already had it, {stats['bad']:3d} bad")

    print(f"\n{totals['stamped']} record(s) stamped, {totals['already']} already "
          f"carried the key, {totals['bad']} unmappable, out of {totals['pages']} seen.")
    if skipped:
        print(f"{len(skipped)} volume(s) skipped (see stderr): {', '.join(skipped)}")
    return 1 if totals["bad"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
