#!/usr/bin/env python3
"""Phase F stage 1 — OCR the post-1974 scans with the local dots.ocr model.

Drives `nyc_executive_orders.vlm_ocr` over the ~1,086 post-1974 executive orders
whose PDFs are image-only, writing each document's committed, diffable page
records to `sources/ocr/<year>/<eo_id>/`. Stage 2
(`scripts/run_parse.py --ocr-engine vlm`) reads those and needs no model at all.

Born-digital orders are NOT touched. A real text layer is already byte-faithful,
and re-OCR'ing it could only add error. Which is which is decided by re-probing
every PDF with textlayer.classify_pdf, not by trusting the text_source already in
the corpus — see vlm_corpus.select_candidates for why that matters.

WHY THIS EXISTS RATHER THAN run_volume_ocr.py. That script shells out to vlm_ocr
once per volume. Fourteen weight loads is nothing. One thousand and eighty-six
would be 9-18 hours before a single page is read. This driver calls
vlm_ocr.load_model() ONCE per worker and vlm_ocr.ocr_pdf() per document.

MANY WORKERS. A 3B-parameter model decoding one page at a time leaves a big GPU
almost idle, so --workers N runs N model replicas concurrently on the same card.
Work is split by greedy longest-first bin-packing over page counts, which lands
within 0.1% of a perfect split on the real population (1,086 documents, 1,799
pages, longest 21) — so there is no work queue, no claim protocol and no
inter-process coordination here, and there does not need to be. Budget ~10 GB of
VRAM per worker: 8 on an H100 or RTX 6000 Blackwell, 4 on an RTX 6000 Ada.

On a big card pass --quantization none. vlm_ocr defaults to 8bit on cuda, and the
repo's "4bit is ~3x faster than 8bit" finding is about bitsandbytes overhead when
memory is tight. When weights are not the constraint, bf16 is faster per worker
and still leaves room for many replicas.

NO AUTHORIZATION GATE. The human/operator flags on the harvest runners exist
because those scripts make live, rate-limited calls to nyc.gov, the Internet
Archive, or DORIS. This one is pure local compute over files already on disk: no
network (beyond the one-time Hugging Face weight download, which the parent does
once before spawning so N workers cannot race a cold cache), no third-party
service, nothing to be a bad citizen toward.

Calibrate, then OCR:
    python scripts/run_post1974_ocr.py --classify-blank-only --year 1974
    python scripts/run_post1974_ocr.py --year 1974
    python scripts/run_post1974_ocr.py --device cuda --quantization none --workers 8

See what is done and what is left:
    python scripts/run_post1974_ocr.py --status
    python scripts/run_post1974_ocr.py --dry-run --workers 8    # prints the shard split

Resuming: every stage checks its own output first and is safe to re-run. A
document whose pages are all recorded is skipped outright; a document killed
part-way resumes at its first missing page. So the way to grind through the
backlog is to keep re-running the script. --force redoes work that already has
output.

--status and --dry-run import no backend and load no model, so they run on any
machine, including CI.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import shutil
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders import vlm_corpus  # noqa: E402
from nyc_executive_orders.vlm_corpus import (  # noqa: E402
    STATUS_BORN_DIGITAL,
    STATUS_NO_PDF,
    STATUS_SCANNED,
    STATUS_UNREADABLE,
    Candidate,
    bin_pack,
    doc_ocr_dir,
    select_candidates,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECORDS = REPO_ROOT / "corpus" / "eo.json"
DEFAULT_OCR_ROOT = REPO_ROOT / "sources" / "ocr"
DEFAULT_RUNS_ROOT = REPO_ROOT / "vlm-ocr-runs" / "post1974"
DEFAULT_LOG_ROOT = REPO_ROOT / "vlm-logs" / "post1974"

# Enough VRAM for one dots.ocr replica in bf16 plus activations, KV cache and the
# process's own CUDA context. Deliberately generous: a worker that OOMs mid-run
# costs more than a worker that was never started.
VRAM_PER_WORKER_GB = 10.0
MAX_AUTO_WORKERS = 12

logger = logging.getLogger("nyc_executive_orders.run_post1974_ocr")


def _quiet_third_party_logs() -> None:
    """Keep the run's own progress readable.

    basicConfig(INFO) turns on httpx's per-request logging, so the one-time weight
    prefetch buries the ledger under HTTP lines. Nothing here is suppressed that
    would hide a failure: warnings and errors still come through.
    """
    for name in ("httpx", "httpcore", "huggingface_hub", "filelock", "urllib3"):
        logging.getLogger(name).setLevel(logging.WARNING)


def _quiet_mupdf() -> None:
    """Stop MuPDF writing C-level warnings straight to stderr.

    Probing 2,291 PDFs emits hundreds of "format error: No default Layer config"
    lines from inside MuPDF, which are not Python warnings and cannot be caught.
    They say nothing actionable about a scan and they bury the ledger. Scoped to
    this script, never to the library: textlayer must stay honest when something
    else imports it.
    """
    try:
        import fitz

        fitz.TOOLS.mupdf_display_errors(False)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


# --------------------------------------------------------------------------- #
# Per-document plumbing                                                         #
# --------------------------------------------------------------------------- #

@dataclass
class DocResult:
    """One document's outcome, as a worker reports it back to the parent."""

    eo_id: str
    year: int
    pages_rendered: int = 0
    pages_ocred: int = 0
    pages_skipped_blank: int = 0
    pages_resumed: int = 0
    parse_errors: int = 0
    truncated: int = 0
    low_coverage: int = 0
    no_measurable_ink: int = 0
    blank_override: bool = False
    flagged: bool = False
    seconds: float = 0.0
    error: str | None = None
    skipped_reason: str | None = None


def document_is_done(candidate: Candidate, ocr_root: Path) -> bool:
    """Has every page of this document already been recorded?

    Compares against the page count textlayer measured, so a document that was
    killed part-way reads as not-done and resumes at its first missing page.
    """
    directory = doc_ocr_dir(ocr_root, candidate.year, candidate.eo_id)
    if not directory.is_dir():
        return False
    return len(list(directory.glob("page_*.json"))) >= max(candidate.page_count, 1)


def prune_renders(run_dir: Path, keep: str, flagged: bool) -> None:
    """Drop a finished document's page PNGs per --keep-renders.

    At 200 DPI a letter page is ~1-2 MB, twice over with overlays, so a
    1,799-page run would leave 2-5 GB of scratch. `flagged` keeps exactly the
    documents a human will open in the viewer.
    """
    if keep == "all":
        return
    if keep == "flagged" and flagged:
        return
    shutil.rmtree(run_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# The worker                                                                    #
# --------------------------------------------------------------------------- #

def _worker(worker_id: int, shard: list[dict], config: dict, queue) -> None:
    """One process: load the model once, then walk this shard's documents.

    `shard` and `config` arrive as plain dicts because they cross a spawn
    boundary. Results go back over `queue` one document at a time, so the parent
    can report progress while the run is still going.
    """
    # Imported inside the worker: the parent must be able to run --status and
    # --dry-run without importing a backend at all.
    from nyc_executive_orders import vlm_ocr

    log_root = Path(config["log_root"])
    log_root.mkdir(parents=True, exist_ok=True)
    log_path = log_root / f"worker-{worker_id}.log"

    if config.get("gpu") is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(config["gpu"])

    with log_path.open("a", encoding="utf-8") as log_file:
        def log(*parts) -> None:
            log_file.write(" ".join(str(p) for p in parts) + "\n")
            log_file.flush()

        opts = vlm_ocr.OcrOptions(**config["opts"])
        try:
            loaded = vlm_ocr.load_model(
                config["device"], config["model_id"], config["quantization"],
                config["attn_implementation"], opts.max_tokens, log=log,
            )
        except Exception as exc:                      # noqa: BLE001 - reported, not raised
            log(traceback.format_exc())
            queue.put(("fatal", worker_id, f"{type(exc).__name__}: {exc}"))
            return

        consecutive = 0
        for doc in shard:
            candidate = Candidate(**{**doc, "pdf_path": Path(doc["pdf_path"])})
            result = DocResult(eo_id=candidate.eo_id, year=candidate.year)
            run_dir = Path(config["runs_root"]) / str(candidate.year) / candidate.eo_id
            json_dir = doc_ocr_dir(config["ocr_root"], candidate.year, candidate.eo_id)
            json_dir.mkdir(parents=True, exist_ok=True)
            log(f"\n=== {candidate.eo_id} ({candidate.page_count} page(s)) ===")
            try:
                counts = vlm_ocr.ocr_pdf(
                    candidate.pdf_path,
                    loaded=loaded,
                    opts=opts,
                    raw_dir=run_dir / "raw",
                    json_dir=json_dir,
                    overlay_dir=(run_dir / "overlays") if opts.write_overlays else None,
                    skip_existing=not config["force"],
                    log=log,
                )
                for field_name in (
                    "pages_rendered", "pages_ocred", "pages_skipped_blank",
                    "pages_resumed", "parse_errors", "truncated", "low_coverage",
                    "no_measurable_ink", "blank_override", "seconds",
                ):
                    setattr(result, field_name, getattr(counts, field_name))
                result.flagged = counts.flagged
                prune_renders(run_dir, config["keep_renders"], counts.flagged)
                consecutive = 0
            except KeyboardInterrupt:
                raise                                  # never swallow the operator
            except Exception as exc:                   # noqa: BLE001 - one bad PDF
                log(traceback.format_exc())
                result.error = f"{type(exc).__name__}: {exc}"
                consecutive += 1
                if _is_cuda_oom(exc):
                    _empty_cuda_cache(loaded)
            queue.put(("doc", worker_id, asdict(result)))

            if consecutive >= config["max_consecutive_failures"]:
                queue.put((
                    "fatal", worker_id,
                    f"{consecutive} consecutive failures — this looks systemic, not "
                    f"per-document; see {log_path}",
                ))
                break

    queue.put(("done", worker_id, None))


def _is_cuda_oom(exc: Exception) -> bool:
    return "out of memory" in str(exc).lower()


def _empty_cuda_cache(loaded) -> None:
    """Give a worker a chance to survive one OOM. Best effort, never raises."""
    try:
        if getattr(loaded.be, "kind", None) == "cuda":
            loaded.be.torch.cuda.empty_cache()
    except Exception:                                  # noqa: BLE001 - best effort
        pass


# --------------------------------------------------------------------------- #
# Parent-side helpers                                                           #
# --------------------------------------------------------------------------- #

def resolve_workers(requested: str, device: str) -> int:
    """`--workers auto` sizes from free VRAM; anything else is taken literally."""
    if requested != "auto":
        n = int(requested)
        if n < 1:
            raise ValueError(f"--workers must be >= 1, got {n}")
        return n
    if device != "cuda":
        return 1                                       # mlx has one unified pool
    try:
        import torch

        free_bytes, _total = torch.cuda.mem_get_info()
        n = int(free_bytes / (VRAM_PER_WORKER_GB * 1024 ** 3))
    except Exception:                                  # noqa: BLE001 - fall back to serial
        return 1
    return max(1, min(n, MAX_AUTO_WORKERS))


def prefetch_weights(model_id: str, *, log=print) -> None:
    """Download the checkpoint once, in the parent, before any worker spawns.

    N workers racing a cold Hugging Face cache for a multi-gigabyte checkout is a
    real failure, not a theoretical one. This is also where the cuda path's
    trust_remote_code decision is taken once instead of N times.
    """
    try:
        from huggingface_hub import snapshot_download
        from huggingface_hub.utils import disable_progress_bars
    except ImportError:
        log("      (huggingface_hub not importable; skipping weight prefetch)")
        return
    log(f"[0/2] prefetching {model_id} into the local cache ...")
    disable_progress_bars()
    snapshot_download(model_id)


def parse_years(args) -> set[int] | None:
    if args.year:
        return set(args.year)
    if args.since_year is None and args.until_year is None:
        return None
    lo = args.since_year if args.since_year is not None else 0
    hi = args.until_year if args.until_year is not None else 9999
    return set(range(lo, hi + 1))


def load_records(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Reporting                                                                     #
# --------------------------------------------------------------------------- #

def print_status(candidates: list[Candidate], ocr_root: Path) -> None:
    """Per-year ledger of what is selected, done, part-done and left."""
    years: dict[int, dict[str, int]] = {}
    for c in candidates:
        row = years.setdefault(c.year, dict(
            selected=0, done=0, partial=0, pages=0, pages_done=0,
            born_digital=0, no_pdf=0, unreadable=0,
        ))
        if c.status == STATUS_BORN_DIGITAL:
            row["born_digital"] += 1
            continue
        if c.status == STATUS_NO_PDF:
            row["no_pdf"] += 1
            continue
        if c.status == STATUS_UNREADABLE:
            row["unreadable"] += 1
            continue
        row["selected"] += 1
        row["pages"] += c.page_count
        directory = doc_ocr_dir(ocr_root, c.year, c.eo_id)
        n = len(list(directory.glob("page_*.json"))) if directory.is_dir() else 0
        row["pages_done"] += min(n, c.page_count)
        if n >= max(c.page_count, 1):
            row["done"] += 1
        elif n:
            row["partial"] += 1

    print(f"{'year':>6s} {'docs':>6s} {'done':>6s} {'part':>6s} {'pages':>7s} "
          f"{'ocr''d':>7s}  {'born-dig':>8s} {'no-pdf':>7s} {'unread':>7s}")
    totals = dict(selected=0, done=0, partial=0, pages=0, pages_done=0,
                  born_digital=0, no_pdf=0, unreadable=0)
    for year in sorted(years):
        r = years[year]
        for k in totals:
            totals[k] += r[k]
        if not r["selected"] and not r["unreadable"] and not r["no_pdf"]:
            continue
        print(f"{year:>6d} {r['selected']:>6d} {r['done']:>6d} {r['partial']:>6d} "
              f"{r['pages']:>7d} {r['pages_done']:>7d}  {r['born_digital']:>8d} "
              f"{r['no_pdf']:>7d} {r['unreadable']:>7d}")
    print(f"{'TOTAL':>6s} {totals['selected']:>6d} {totals['done']:>6d} "
          f"{totals['partial']:>6d} {totals['pages']:>7d} {totals['pages_done']:>7d}  "
          f"{totals['born_digital']:>8d} {totals['no_pdf']:>7d} {totals['unreadable']:>7d}")
    if totals["unreadable"]:
        print("\nWARNING: PDFs that cannot be opened at all are a HARVEST bug, not an "
              "OCR one — they are not queued:")
        for c in candidates:
            if c.status == STATUS_UNREADABLE:
                print(f"  {c.eo_id}: {c.error}")


def format_eta(pages_left: int, seconds_per_page: float) -> str:
    if seconds_per_page <= 0 or pages_left <= 0:
        return "?"
    total = int(pages_left * seconds_per_page)
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--year", type=int, action="append", default=None,
                   help="Restrict to this signing year (repeatable).")
    p.add_argument("--since-year", type=int, default=None)
    p.add_argument("--until-year", type=int, default=None)
    p.add_argument("--eo-id", action="append", default=None,
                   help="Run one document (repeatable). The re-run knob.")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap the number of SELECTED documents this run.")
    p.add_argument("--status", action="store_true",
                   help="Print the per-year ledger and exit. Loads no model.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the worklist and shard split, then exit. Loads no model.")
    p.add_argument("--classify-blank-only", action="store_true",
                   help="Render and score pages blank/keep WITHOUT loading the model, "
                        "writing classify_report.json per document. Do this before "
                        "OCR'ing a body of scans you have not run before: a false skip "
                        "silently drops real content, and 764 of the 1,086 targets are "
                        "a single page, where that would drop the entire order.")
    p.add_argument("--force", action="store_true",
                   help="Redo documents that already have page records.")
    p.add_argument("--workers", default="1",
                   help="Parallel worker processes, or 'auto' to size from free VRAM "
                        f"at ~{VRAM_PER_WORKER_GB:.0f} GB each (default: 1).")
    p.add_argument("--gpus", default=None,
                   help="Comma-separated CUDA device ids to spread workers over "
                        "(e.g. 0,1). Default: whatever CUDA_VISIBLE_DEVICES already says.")
    p.add_argument("--device", choices=["auto", "mlx", "cuda"], default="auto")
    p.add_argument("--model", default=None)
    p.add_argument("--quantization", choices=["none", "8bit", "4bit"], default=None,
                   help="cuda only. Pass 'none' on a big card: the 8bit default and the "
                        "'4bit is faster' finding both come from a small card where "
                        "bitsandbytes overhead dominates.")
    p.add_argument("--attn-implementation", default=None)
    p.add_argument("--dpi", type=int, default=None)
    p.add_argument("--rotate", default=None)
    p.add_argument("--max-tokens", type=int, default=None)
    p.add_argument("--prompt-file", default=None)
    p.add_argument("--ink-roi-margin", type=float, default=None,
                   help="Fraction of each edge cropped before ink stats. The 0.12 default "
                        "exists to crop a book binding and scanner bed; loose letterhead "
                        "has neither, and 0.12 of a letter page is the inch where the "
                        "letterhead and signature sit. Calibrate before changing it.")
    p.add_argument("--min-dark-fraction", type=float, default=None)
    p.add_argument("--min-contrast-std", type=float, default=None)
    p.add_argument("--overlays", action=argparse.BooleanOptionalAction, default=False,
                   help="Write bbox overlay PNGs (default: off — N workers contend for "
                        "that I/O and a full run is 2-5 GB of them).")
    p.add_argument("--keep-renders", choices=["all", "flagged", "none"], default="flagged",
                   help="Which documents keep their rendered page PNGs (default: flagged, "
                        "i.e. exactly the ones a human will open in the viewer).")
    p.add_argument("--progress-every", type=int, default=25,
                   help="Terminal progress line every N documents (default: 25).")
    p.add_argument("--max-consecutive-failures", type=int, default=10,
                   help="Abort a worker after this many failures in a row (default: 10). "
                        "That many in a row is systemic, not per-document.")
    p.add_argument("--records", default=str(DEFAULT_RECORDS),
                   help="Worklist source (default: corpus/eo.json — committed, present "
                        "in a fresh clone, and never a partial harvest artifact).")
    p.add_argument("--ocr-root", default=str(DEFAULT_OCR_ROOT))
    p.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    p.add_argument("--log-root", default=str(DEFAULT_LOG_ROOT))
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    _quiet_mupdf()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    if not args.verbose:
        _quiet_third_party_logs()

    ocr_root = Path(args.ocr_root)
    records = load_records(Path(args.records))
    candidates = select_candidates(
        records,
        repo_root=REPO_ROOT,
        years=parse_years(args),
        eo_ids=set(args.eo_id) if args.eo_id else None,
        limit=args.limit,
    )

    if args.status:
        print_status(candidates, ocr_root)
        return 0

    selected = [c for c in candidates if c.selected]
    todo = selected if args.force else [
        c for c in selected if not document_is_done(c, ocr_root)
    ]
    n_done = len(selected) - len(todo)
    pages_todo = sum(c.page_count for c in todo)

    unreadable = [c for c in candidates if c.status == STATUS_UNREADABLE]
    for c in unreadable:
        print(f"SKIP {c.eo_id}: PDF cannot be opened ({c.error}) — harvest bug, not OCR",
              file=sys.stderr)

    print(f"POST-1974 VLM OCR: {len(selected)} scanned doc(s) selected, "
          f"{n_done} already recorded, {len(todo)} to do ({pages_todo} page(s))")
    if not todo:
        print("nothing to do.")
        return 0

    n_workers = resolve_workers(args.workers, args.device)
    n_workers = min(n_workers, len(todo))
    shards = bin_pack(todo, n_workers)

    if args.dry_run:
        for i, shard in enumerate(shards):
            print(f"\nworker {i}: {len(shard)} doc(s), "
                  f"{sum(c.page_count for c in shard)} page(s)")
            for c in shard[:5]:
                print(f"    {c.eo_id:>18s}  {c.page_count:>3d}p  {c.pdf_path}")
            if len(shard) > 5:
                print(f"    ... and {len(shard) - 5} more")
        return 0

    return _run(args, shards, todo, pages_todo, n_workers)


def _run(args, shards, todo, pages_todo, n_workers) -> int:
    """Spawn the workers and report until they are all finished."""
    # Deferred so --status/--dry-run never import a backend.
    from nyc_executive_orders import vlm_ocr

    device = vlm_ocr.resolve_device(args.device)
    model_id = args.model or (
        vlm_ocr.DEFAULT_MODEL if device == "mlx" else vlm_ocr.DEFAULT_MODEL_CUDA
    )
    quantization = args.quantization or ("8bit" if device == "cuda" else "none")

    prompt_path = Path(args.prompt_file) if args.prompt_file else vlm_ocr.DEFAULT_PROMPT_PATH
    prompt_text = "" if args.classify_blank_only else prompt_path.read_text().strip()
    if not args.classify_blank_only and not prompt_text:
        print(f"error: prompt file is empty: {prompt_path}", file=sys.stderr)
        return 2

    opt_kwargs = dict(
        prompt_text=prompt_text,
        write_overlays=bool(args.overlays),
        # A false blank on a one-page order erases the whole order. Always on here.
        never_skip_every_page=True,
    )
    for name, value in (
        ("dpi", args.dpi), ("rotate", args.rotate), ("max_tokens", args.max_tokens),
        ("ink_roi_margin", args.ink_roi_margin),
        ("min_dark_fraction", args.min_dark_fraction),
        ("min_contrast_std", args.min_contrast_std),
    ):
        if value is not None:
            opt_kwargs[name] = value
    opts = vlm_ocr.OcrOptions(**opt_kwargs)

    if args.classify_blank_only:
        return _classify_only(args, todo, opts, vlm_ocr)

    try:
        vlm_ocr.backend(device)
    except vlm_ocr.VlmBackendUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    prefetch_weights(model_id)

    gpus = [int(g) for g in args.gpus.split(",")] if args.gpus else None
    config_base = dict(
        device=device, model_id=model_id, quantization=quantization,
        attn_implementation=args.attn_implementation,
        opts={k: v for k, v in vars(opts).items()},
        ocr_root=str(args.ocr_root), runs_root=str(args.runs_root),
        log_root=str(args.log_root), keep_renders=args.keep_renders,
        force=args.force, max_consecutive_failures=args.max_consecutive_failures,
    )

    print(f"[1/2] starting {n_workers} worker(s) on {device}"
          f"{f' ({quantization})' if device == 'cuda' else ''}; "
          f"per-worker logs under {args.log_root}/")

    ctx = multiprocessing.get_context("spawn")   # a forked CUDA context is invalid
    queue = ctx.Queue()
    procs = []
    for i, shard in enumerate(shards):
        config = dict(config_base)
        config["gpu"] = gpus[i % len(gpus)] if gpus else None
        payload = [c.as_dict() | {"page_count": c.page_count} for c in shard]
        payload = [{k: v for k, v in d.items()} for d in payload]
        proc = ctx.Process(target=_worker, args=(i, payload, config, queue), daemon=False)
        proc.start()
        procs.append(proc)

    results, failures, fatals = _collect(queue, len(procs), len(todo), pages_todo,
                                         args.progress_every)
    for proc in procs:
        proc.join()

    return _report(results, failures, fatals, args)


def _collect(queue, n_workers, n_docs, pages_todo, progress_every):
    """Drain the result queue, printing progress, until every worker says done."""
    results: list[dict] = []
    failures: list[dict] = []
    fatals: list[tuple[int, str]] = []
    live = n_workers
    started = time.time()
    pages_seen = 0

    while live:
        kind, worker_id, payload = queue.get()
        if kind == "done":
            live -= 1
            continue
        if kind == "fatal":
            fatals.append((worker_id, payload))
            print(f"\nWORKER {worker_id} ABORTED: {payload}", file=sys.stderr)
            continue
        results.append(payload)
        if payload.get("error"):
            failures.append(payload)
            print(f"  FAILED {payload['eo_id']}: {payload['error']}", file=sys.stderr)
        pages_seen += payload["pages_ocred"] + payload["pages_skipped_blank"]
        if len(results) % progress_every == 0 or len(results) == n_docs:
            elapsed = time.time() - started
            per_page = elapsed / pages_seen if pages_seen else 0
            flagged = sum(1 for r in results if r.get("flagged"))
            print(f"  {len(results)}/{n_docs} docs · {pages_seen}/{pages_todo} pages · "
                  f"{n_workers} workers · {per_page:.1f}s/page · "
                  f"~{format_eta(pages_todo - pages_seen, per_page)} left  "
                  f"({flagged} flagged)")
    return results, failures, fatals


def _report(results, failures, fatals, args) -> int:
    """Print the run summary. Non-zero exit if anything failed."""
    total = lambda k: sum(r[k] for r in results)  # noqa: E731
    print("\ndone.")
    print(f"  documents:      {len(results)}")
    print(f"  pages OCR'd:    {total('pages_ocred')}")
    print(f"  pages resumed:  {total('pages_resumed')}")
    print(f"  pages blank:    {total('pages_skipped_blank')}")
    print(f"  parse errors:   {total('parse_errors')}")
    print(f"  truncated:      {total('truncated')}")
    print(f"  low coverage:   {total('low_coverage')}")
    print(f"  no ink:         {total('no_measurable_ink')}")

    overrides = [r for r in results if r.get("blank_override")]
    if overrides:
        print(f"\n  {len(overrides)} document(s) had EVERY page classified blank and were "
              f"OCR'd anyway (the threshold is wrong for these, review them):")
        for r in overrides:
            print(f"    {r['eo_id']}")

    if failures:
        print(f"\n{len(failures)} document(s) FAILED:", file=sys.stderr)
        for r in failures:
            print(f"  {r['eo_id']}: {r['error']}", file=sys.stderr)
        print(f"re-run them with: --force " + " ".join(f"--eo-id {r['eo_id']}" for r in failures),
              file=sys.stderr)

    print(f"\n  records:  {args.ocr_root}/<year>/<eo_id>/page_XXXX.json")
    print(f"  logs:     {args.log_root}/worker-N.log")
    print(f"  build:    python scripts/run_parse.py --ocr-engine vlm")
    return 1 if (failures or fatals) else 0


def _classify_only(args, todo, opts, vlm_ocr) -> int:
    """Stage 0: render and score every page blank/keep. No model, one process."""
    print(f"[classify-blank-only] scoring {len(todo)} document(s) — no model will be loaded")
    n_blank = n_pages = 0
    for candidate in todo:
        run_dir = Path(args.runs_root) / str(candidate.year) / candidate.eo_id
        json_dir = doc_ocr_dir(args.ocr_root, candidate.year, candidate.eo_id)
        json_dir.mkdir(parents=True, exist_ok=True)
        rendered = vlm_ocr.render_for_run(
            candidate.pdf_path, run_dir / "raw", opts, None, 1, log=lambda *a: None
        )
        if not rendered:
            print(f"  {candidate.eo_id}: no pages rendered", file=sys.stderr)
            continue
        report = vlm_ocr.classify_pages(rendered, opts, json_dir, 1, log=lambda *a: None)
        blanks = [p["page"] for p in report["pages"] if p["blank"]]
        n_pages += len(report["pages"])
        n_blank += len(blanks)
        if blanks:
            marker = "  *** ALL PAGES ***" if len(blanks) == len(report["pages"]) else ""
            print(f"  {candidate.eo_id}: {len(blanks)}/{len(report['pages'])} page(s) "
                  f"would be skipped: {blanks}{marker}")
        prune_renders(run_dir, args.keep_renders, bool(blanks))
    print(f"\n{n_blank}/{n_pages} page(s) across {len(todo)} document(s) classify as blank.")
    print("Gate: a page classified blank inside a document that ALREADY has Tesseract "
          "text is a proven false blank. Check that before OCR'ing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
