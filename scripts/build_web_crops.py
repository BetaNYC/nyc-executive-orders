#!/usr/bin/env python3
"""Re-encode the Phase E picture crops as JPEGs, for the public explorer to serve.

`run_picture_clips.py` writes lossless PNGs at 300 dpi under `vlm-pics/`, which is
the right format for the source of truth and the wrong one for a web page: 2,147
crops come to 472 MB, and the gallery view paints 120 of them at once. The same
crops at JPEG q85 come to about 72 MB -- 15% of the bytes, on photographs of a
bound book where the loss is invisible -- which is small enough for the explorer to
ship them as ordinary files in its build.

So this is a PUBLISHING step, not a pipeline stage. It reads `vlm-pics/` and writes
a parallel tree under `vlm-pics-web/`:

    <volume-stem>/page_NNNN_pic_KK[_sig|_seal].jpg   one JPEG per PNG
    manifest.json                                    vlm-pics/manifest.json, with
                                                     every `image` renamed to .jpg

`vlm-pics/` stays gitignored -- it is regenerable pixels and always has been. THIS
tree is committed, as plain git rather than LFS, so the explorer's Pages build can
check this repo out without `lfs: true` and never spend LFS bandwidth on images.
The manifest is copied rather than pointed at for the same reason: the published
tree has to stand on its own in a checkout where `vlm-pics/` does not exist.

Re-running is a no-op. Like the clipper (run_picture_clips.py:395), an existing
output is left alone unless --force, so a routine re-run writes nothing and adds no
git objects. The encoder is fixed-parameter and JPEG carries no timestamp, so a
crop that IS re-encoded comes out byte-identical to the last one.

--max-edge exists for a tail of bad bboxes. A handful of crops in the Lindsay
memoranda are most of a page rather than a seal -- dots.ocr called the whole scan a
`Picture` -- and they dominate the byte count out of all proportion to their number.
Downscaling the long edge caps that without touching the 99% that are already small.

    python scripts/build_web_crops.py                     # all volumes
    python scripts/build_web_crops.py --volume Wagner     # one, by substring
    python scripts/build_web_crops.py --force -v          # re-encode everything
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC_DIR = REPO_ROOT / "vlm-pics"
DEFAULT_OUT_DIR = REPO_ROOT / "vlm-pics-web"
DEFAULT_QUALITY = 85
# 2000px on the long edge is still ~4x a seal's on-screen size at 300 dpi, so the
# cap only ever bites on the full-page misdetections it is here for.
DEFAULT_MAX_EDGE = 2000


def encode_one(src: Path, dst: Path, quality: int, max_edge: int) -> tuple[int, int]:
    """Write `src` to `dst` as a JPEG. Returns the output's (width, height)."""
    with Image.open(src) as im:
        # PNG crops come out of PyMuPDF as RGB already, but a paletted or grayscale
        # one would raise on JPEG save rather than convert itself.
        im = im.convert("RGB")
        if max_edge and max(im.size) > max_edge:
            im.thumbnail((max_edge, max_edge), Image.LANCZOS)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # progressive, because these load over a slow connection in a grid of 120;
        # optimize, because it is a second Huffman pass for free at this scale.
        im.save(dst, "JPEG", quality=quality, optimize=True, progressive=True)
        return im.size


def rewrite_manifest(src_manifest: Path, out_path: Path, published: set[str]) -> int:
    """Copy the clipper's manifest with `image` renamed .png -> .jpg.

    Entries whose crop was not published (a volume this run skipped, or a PNG that
    has gone missing) are dropped rather than carried: the explorer joins on this
    file, and an entry with no image behind it is a broken tile.
    """
    data = json.loads(src_manifest.read_text(encoding="utf-8"))
    out = []
    for entry in data.get("pictures", []):
        image = entry.get("image", "")
        jpg = image[:-4] + ".jpg" if image.endswith(".png") else image
        if jpg in published:
            out.append({**entry, "image": jpg})
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps({"pictures": out}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return len(out)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--volume", default=None,
                   help="Substring of the volume directory to encode (default: all).")
    p.add_argument("--src-dir", default=str(DEFAULT_SRC_DIR),
                   help="The clipper's output tree to read.")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                   help="Where the published JPEGs and manifest are written.")
    p.add_argument("--quality", type=int, default=DEFAULT_QUALITY,
                   help=f"JPEG quality (default: {DEFAULT_QUALITY}).")
    p.add_argument("--max-edge", type=int, default=DEFAULT_MAX_EDGE,
                   help=f"Downscale crops longer than this on their long edge "
                        f"(default: {DEFAULT_MAX_EDGE}; 0 = never).")
    p.add_argument("--force", action="store_true",
                   help="Re-encode crops that already exist (default: leave them be).")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would be encoded; write nothing.")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Print a line per crop.")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not 1 <= args.quality <= 95:
        print(f"error: --quality wants 1-95, got {args.quality}", file=sys.stderr)
        return 2
    if args.max_edge < 0:
        print(f"error: --max-edge cannot be negative, got {args.max_edge}", file=sys.stderr)
        return 2

    src_root = Path(args.src_dir)
    out_root = Path(args.out_dir)
    src_manifest = src_root / "manifest.json"
    if not src_manifest.is_file():
        print(f"error: no crop manifest at {src_manifest}; run "
              f"scripts/run_picture_clips.py first", file=sys.stderr)
        return 2

    pngs = sorted(p for p in src_root.rglob("*.png"))
    if args.volume:
        needle = args.volume.lower()
        pngs = [p for p in pngs if needle in p.relative_to(src_root).parts[0].lower()]
        if not pngs:
            print(f"error: no volume matches {args.volume!r}", file=sys.stderr)
            return 2

    written = skipped = 0
    src_bytes = out_bytes = 0
    shrunk: list[tuple[str, int]] = []
    # Every crop that ends up on disk, whether this run wrote it or found it, so
    # a --volume run still emits a manifest covering the whole published tree.
    published: set[str] = set()

    for png in pngs:
        rel = png.relative_to(src_root)
        jpg = out_root / rel.with_suffix(".jpg")
        src_bytes += png.stat().st_size

        if args.dry_run:
            published.add(str(rel.with_suffix(".jpg")))
            continue

        if jpg.exists() and not args.force:
            skipped += 1
        else:
            with Image.open(png) as probe:
                was = max(probe.size)
            size = encode_one(png, jpg, args.quality, args.max_edge)
            written += 1
            if args.max_edge and was > args.max_edge:
                shrunk.append((str(rel), was))
            if args.verbose:
                print(f"  {rel.with_suffix('.jpg')}  {size[0]}x{size[1]}px  "
                      f"{jpg.stat().st_size / 1024:.0f} KB")

        out_bytes += jpg.stat().st_size
        published.add(str(rel.with_suffix(".jpg")))

    if args.dry_run:
        print(f"dry run: {len(pngs)} crop(s), {src_bytes / 1e6:.1f} MB of PNG, "
              f"would be encoded to {out_root}")
        return 0

    # Any crop already published under a volume this run did not touch stays in the
    # manifest -- same rule as the clipper's merge_manifest().
    for jpg in out_root.rglob("*.jpg"):
        published.add(str(jpg.relative_to(out_root)))

    n = rewrite_manifest(src_manifest, out_root / "manifest.json", published)

    print(f"{written} crop(s) encoded"
          + (f", {skipped} already present" if skipped else "")
          + f" -> {out_root}")
    print(f"{src_bytes / 1e6:.1f} MB of PNG -> {out_bytes / 1e6:.1f} MB of JPEG "
          f"({100 * out_bytes / src_bytes:.1f}%) at q{args.quality}")
    print(f"manifest: {out_root / 'manifest.json'} ({n} entries)")
    if shrunk:
        print(f"{len(shrunk)} crop(s) over --max-edge {args.max_edge}px were downscaled:",
              file=sys.stderr)
        for rel, was in sorted(shrunk, key=lambda s: -s[1])[:10]:
            print(f"  {was}px  {rel}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
