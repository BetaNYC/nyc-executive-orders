#!/usr/bin/env python3
"""Build a volume's whole-document view from its page records, on demand.

`vlm_ocr` used to store two of these beside the page JSON — `document.md` (every
page's content stitched into one Markdown file) and `raw_output.json` (every page
record in one array). Both were pure functions of the `page_XXXX.json` files next
to them, rebuilt from disk on every run, and a stored copy of derived data is one
more thing that can go stale or be mistaken for a source of truth. So they are
built here instead, when someone actually wants to read one:

    python scripts/render_volume_document.py --volume Wagner_Orders            # md, stdout
    python scripts/render_volume_document.py --volume 1962-01-23 --format json
    python scripts/render_volume_document.py --volume 1962-01-23 -o /tmp/vol.md
    python scripts/render_volume_document.py --json-dir vlm-ocr-runs/foo/json  # any dir

Default output is stdout, which is the point: the normal use is to page through
a volume or grep it, not to leave a file behind. `--out` writes one anyway, for
when you want to hand it to something that needs a path — put it somewhere
scratch rather than back beside the page records.

Reads `sources/gpp/volumes/ocr/<volume-stem>/` — the one place page records live
(see `vlm_ocr --json-dir`). Offline, no model, no PDF: it is a re-render of JSON
already on disk, so it is instant and safe to run mid-OCR. A volume being OCR'd
right now renders as far as it has got.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders.build_pre1974 import load_volumes  # noqa: E402
from nyc_executive_orders.vlm_ocr import (  # noqa: E402
    collect_page_records,
    records_to_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VOLUMES_JSON = REPO_ROOT / "sources" / "gpp" / "volumes.json"
DEFAULT_OCR_ROOT = REPO_ROOT / "sources" / "gpp" / "volumes" / "ocr"


def resolve_json_dir(args) -> Path:
    """The directory of page records to render, from --json-dir or --volume."""
    if args.json_dir:
        return Path(args.json_dir)
    volumes = [v for v in load_volumes(args.volumes_json)
               if args.volume.lower() in v.filename.lower()]
    if not volumes:
        sys.exit(f"error: no volume matches {args.volume!r}")
    if len(volumes) > 1:
        names = ", ".join(v.stem for v in volumes)
        sys.exit(f"error: {args.volume!r} matches {len(volumes)} volumes ({names}); "
                 f"one document at a time")
    return Path(args.ocr_root) / volumes[0].stem


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument("--volume", help="Substring of the volume filename.")
    target.add_argument("--json-dir", help="A directory of page_XXXX.json files.")
    p.add_argument("--format", choices=["md", "json"], default="md",
                   help="md: pages stitched into Markdown (the old document.md). "
                        "json: every page record in one array (the old raw_output.json). "
                        "Default: %(default)s.")
    p.add_argument("-o", "--out", default=None,
                   help="Write to this path instead of stdout.")
    p.add_argument("--volumes-json", default=str(DEFAULT_VOLUMES_JSON))
    p.add_argument("--ocr-root", default=str(DEFAULT_OCR_ROOT))
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    json_dir = resolve_json_dir(args)
    if not json_dir.is_dir():
        sys.exit(f"error: no page records at {json_dir} (volume not OCR'd yet?)")

    records = collect_page_records(json_dir)
    if not records:
        sys.exit(f"error: {json_dir} holds no page_XXXX.json")

    if args.format == "md":
        text = records_to_markdown(records)
    else:
        text = json.dumps(records, indent=2, ensure_ascii=False)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"{len(records)} page(s) -> {args.out}", file=sys.stderr)
        return 0

    try:
        sys.stdout.write(text)
    except BrokenPipeError:
        # `| head` closed the pipe. That is a normal way to use this, not an
        # error -- but python would still print a BrokenPipeError traceback at
        # shutdown when it flushes. Redirect the fd to devnull so it can't.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
