#!/usr/bin/env python3
"""Local dots.ocr layout+OCR pipeline (MLX or CUDA) — Phase E.

Used to read the 14 pre-1974 bound volumes under ``sources/gpp/volumes/``, which
are pure image scans with no text layer. This is the SECOND OCR backend in the
project, alongside :mod:`ocr` (ocrmypdf/Tesseract); records it produces are
stamped ``text_source: ocr-vlm`` so the two stay auditable apart.

Two interchangeable compute backends run the same dots.ocr model locally:

  mlx    Apple Silicon, via mlx + mlx-vlm against mlx-community's pre-quantized
         weights (the ``vlm`` extra). The original backend; still the default
         on macOS arm64.
  cuda   NVIDIA GPU, via torch + transformers against the upstream
         rednote-hilab/dots.ocr checkpoint, optionally bitsandbytes-quantized
         (the ``vlm-cuda`` extra).

Pick one with ``--device {mlx,cuda}``, or leave ``--device auto`` (the
default) to choose mlx on Apple Silicon when the ``vlm`` extra is importable,
cuda otherwise.

    HARD RULE (engineering-standards §7 — data egress, two-forces gate):
    THIS MODULE MAKES NO NETWORK CALLS AT INFERENCE TIME AND HAS NO CLOUD
    FALLBACK. EVER. The model runs on-machine -- via MLX or via torch/CUDA --
    against locally cached weights. If a page fails we record the failure and
    flag it — we NEVER auto-escalate to Google Document AI, AWS Textract, or
    any external service. (The one network touch is the first-run Hugging
    Face weight download into ~/.cache/huggingface; after that the pipeline
    is fully offline.)

    The cuda backend loads the upstream rednote-hilab/dots.ocr checkpoint with
    ``trust_remote_code=True``, which runs that repo's own modeling code on
    load -- a materially different trust boundary than the mlx backend, whose
    mlx-community weights run entirely inside mlx-vlm's own model code. Know
    that before pointing --device cuda at an unfamiliar model repo.

Determinism: temperature is 0.0 by default, which both backends treat as pure
greedy (argmax) decoding rather than sampling -- see the cuda backend's
comment on why that has to be enforced explicitly rather than just passed
through. That makes a given (backend, model, quantization, prompt, DPI)
reproducible run over run. It does NOT make mlx and cuda read a page
identically to each other, or even necessarily reproduce bit-for-bit across
different GPUs/drivers/torch builds on the cuda side -- only within one
backend on one machine is byte-identical output guaranteed.

``mlx``/``mlx_vlm`` (the ``vlm`` extra) and ``torch``/``transformers``/
``bitsandbytes`` (the ``vlm-cuda`` extra) are OPTIONAL dependencies, each
imported lazily so the rest of the package — and the render-only
``--classify-blank-only`` calibration path — installs and runs without either,
mirroring how ``ocrmypdf`` (the ``ocr`` extra) and Playwright (the ``live``
extra) are handled.

Pages are rendered upright: two of the volumes were scanned sideways and dots.ocr
reports no orientation of its own, so ``--rotate auto`` (the default) turns
landscape-shaped pages a quarter turn clockwise before the model sees them and
records what it did under each page record's ``rotation`` key. See
:func:`detect_page_rotation`.

Renders pages of a PDF, runs dots.ocr (via mlx-vlm) with the prompt in
``data/dots_ocr_prompt.txt``, and writes:

  json/page_XXXX.json      one record per page -- the layout JSON plus the QA
                           signals below. THE output; everything else here is
                           either scratch or derived from it. ``--json-dir``
                           puts it somewhere other than under --output-dir.
  json/classify_report.json  the blank-page verdicts (--classify-blank-only)

and, under --output-dir, scratch that is regenerable from the PDF:

  raw/page_XXXX.png        the rendered page, as the model saw it
  overlays/page_XXXX.png   the same with bbox + category overlays drawn on
  memory_profile.json      per-stage memory high-water marks (--profile-memory)

There is deliberately no combined ``raw_output.json`` or stitched
``document.md`` on disk. Both are functions of the page records -- they were
rebuilt from them on every run -- so they are generated on demand instead, by
``scripts/render_volume_document.py``. One copy of a fact, one place to fix it.

Because the model emits no confidence of its own, each page record also carries
three QA signals that can be computed without ground truth (see the viewer's QA
tab, which reads them):

  finish_reason   "stop" if the model ended on its own, "length" if it hit
                  --max-tokens -- i.e. the page's JSON is truncated and content
                  is missing even when it still parsed.
  logprobs        the decoder's own log-probability for each token it chose,
                  attributed back to each element field and summarised per page
                  in three scopes: "content" (element text -- what the page
                  says), "layout" (bbox and category values -- where the model
                  put each region and what it called it), and "all". Keep them
                  apart: layout is routinely far less certain than text and
                  usually harmlessly so, and an unscoped summary is nothing but
                  coordinate digits. Not calibrated, but low values concentrate.
  ink_coverage    how much of the page's ink actually falls inside a returned
                  bbox, plus bounding boxes for the ink that doesn't. This is the
                  only one of the three that catches *dropped* content, which is
                  otherwise invisible: the JSON parses, it just says less than
                  the page does.

Setup (one time):
    Apple Silicon (MLX):  uv pip install -e '.[vlm]'
    NVIDIA GPU (CUDA):    uv pip install -e '.[vlm-cuda]'

Usage (this module is runnable; scripts/run_volume_ocr.py drives it per volume):
    python -m nyc_executive_orders.vlm_ocr /path/to/file.pdf --pages 5 \
        --output-dir ./out
    python -m nyc_executive_orders.vlm_ocr /path/to/file.pdf --device cuda \
        --quantization 8bit --pages 5 --output-dir ./out

Page numbering is absolute (page 1 = the PDF's first page), so a later run
with --start-page can extend an existing --output-dir: only the new pages are
rendered and OCR'd, and they slot in beside the pages already recorded rather
than renumbering them. E.g. after the run above:

    python -m nyc_executive_orders.vlm_ocr /path/to/file.pdf --start-page 6 \
        --pages 5 --output-dir ./out

First run downloads the model weights from Hugging Face (cached afterwards
under ~/.cache/huggingface). For dense, layout-heavy pages, raise
--max-tokens if a page's JSON looks truncated.

Pass --profile-memory to see how much unified memory each stage costs, e.g.
when picking a quantization or a --dpi/--max-tokens combination that fits.
"""

from __future__ import annotations

import argparse
import json
import math
import platform
import re
import resource
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# The same coverage floor the corpus reader flags on, so a run's own counters and
# the record built from them later agree about what "low coverage" means.
from .vlm_pages import MIN_COVERED_FRACTION


class VlmBackendUnavailable(RuntimeError):
    """Raised when the ``vlm`` extra (mlx + mlx-vlm) is not installed.

    Everything that only needs pixels — rendering, the blank-page classifier,
    ``--classify-blank-only`` — works without it. Only the generation path needs
    the model, so the import failure surfaces there rather than at module import.
    """


@dataclass(frozen=True)
class _Backend:
    """The lazily-imported MLX surface this module uses, resolved exactly once.

    Held in one object rather than as module globals so the import site is a
    single, greppable place and so nothing can half-initialize: either every
    attribute is present or :func:`backend` raised.
    """

    mx: object
    load: object
    load_config: object
    stream_generate: object
    apply_chat_template: object
    replacement_char: str
    kind: str = "mlx"


@dataclass(frozen=True)
class _CudaBackend:
    """The lazily-imported torch/transformers surface for the CUDA path.

    Parallel to :class:`_Backend` (mlx): resolved exactly once, cached, and
    everything the CUDA generation path needs lives on this one object rather
    than as module globals.
    """

    torch: object
    AutoConfig: object
    AutoModelForCausalLM: object
    AutoProcessor: object
    BitsAndBytesConfig: object
    LogitsProcessorList: object
    kind: str = "cuda"


_BACKENDS: dict[str, _Backend | _CudaBackend] = {}


def resolve_device(requested: str) -> str:
    """Turn ``--device`` into a concrete backend kind ("mlx" or "cuda").

    "auto" prefers mlx on Apple Silicon when the ``vlm`` extra is actually
    importable (so a Mac without it installed still falls through to cuda
    rather than failing outright), and cuda everywhere else. Import-checking
    mlx only happens for "auto" -- an explicit --device cuda on a Linux box
    with no mlx installed must never try to import it.
    """
    if requested in ("mlx", "cuda"):
        return requested
    if requested != "auto":
        raise ValueError(f"unknown --device {requested!r}")
    if sys.platform == "darwin" and platform.machine() == "arm64":
        try:
            import mlx.core  # noqa: F401, PLC0415
        except ImportError:
            pass
        else:
            return "mlx"
    return "cuda"


def backend(kind: str) -> _Backend | _CudaBackend:
    """Import the given backend's dependencies on first use, and cache.

    Lazy so the package installs and its offline tests run without either the
    ``vlm`` (mlx) or ``vlm-cuda`` (torch/transformers) extra. Raises
    :class:`VlmBackendUnavailable` with the install command rather than a bare
    ImportError traceback.
    """
    cached = _BACKENDS.get(kind)
    if cached is not None:
        return cached
    if kind == "mlx":
        result = _load_mlx_backend()
    elif kind == "cuda":
        result = _load_cuda_backend()
    else:
        raise ValueError(f"unknown backend kind: {kind!r}")
    _BACKENDS[kind] = result
    return result


def _load_mlx_backend() -> _Backend:
    try:
        import mlx.core as mx  # noqa: PLC0415 - lazy import keeps mlx optional
        from mlx_vlm import load, stream_generate  # noqa: PLC0415
        from mlx_vlm.prompt_utils import apply_chat_template  # noqa: PLC0415
        from mlx_vlm.tokenizer_utils import (  # noqa: PLC0415
            REPLACEMENT_CHAR,
            BPEStreamingDetokenizer,
            _remove_space,
        )
        from mlx_vlm.utils import load_config  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise VlmBackendUnavailable(
            "the mlx VLM OCR backend needs the 'vlm' extra (mlx + mlx-vlm, "
            "Apple Silicon only). Install it with: uv pip install -e '.[vlm]'. "
            "Rendering and --classify-blank-only work without it."
        ) from exc

    _patch_bpe_streaming_detokenizer(BPEStreamingDetokenizer, _remove_space)
    return _Backend(
        mx=mx,
        load=load,
        load_config=load_config,
        stream_generate=stream_generate,
        apply_chat_template=apply_chat_template,
        replacement_char=REPLACEMENT_CHAR,
    )


def _load_cuda_backend() -> _CudaBackend:
    try:
        import torch  # noqa: PLC0415 - lazy import keeps torch/CUDA optional
        from transformers import (  # noqa: PLC0415
            AutoConfig,
            AutoModelForCausalLM,
            AutoProcessor,
            BitsAndBytesConfig,
            LogitsProcessorList,
        )
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise VlmBackendUnavailable(
            "the cuda VLM OCR backend needs the 'vlm-cuda' extra (torch + "
            "transformers + bitsandbytes, NVIDIA GPU only). Install it with: "
            "uv pip install -e '.[vlm-cuda]'. Rendering and "
            "--classify-blank-only work without it."
        ) from exc
    if not torch.cuda.is_available():
        raise VlmBackendUnavailable(
            "--device cuda (or --device auto off Apple Silicon) needs a "
            "visible CUDA GPU, but torch.cuda.is_available() is False. Check "
            "`nvidia-smi` and that this torch build actually has CUDA support."
        )

    return _CudaBackend(
        torch=torch,
        AutoConfig=AutoConfig,
        AutoModelForCausalLM=AutoModelForCausalLM,
        AutoProcessor=AutoProcessor,
        BitsAndBytesConfig=BitsAndBytesConfig,
        LogitsProcessorList=LogitsProcessorList,
    )


def _patch_bpe_streaming_detokenizer(BPEStreamingDetokenizer, _remove_space) -> None:
    """mlx_vlm's BPEStreamingDetokenizer.add_token() does an unguarded
    bytes.decode("utf-8") every time a "new word" token arrives, with no
    error handling -- unlike finalize(), which already tolerates bad bytes.
    If the model emits a token sequence that doesn't land on a valid UTF-8
    boundary at that flush point (seen in practice on a degenerate page),
    this raises UnicodeDecodeError and kills the whole page/run. Patch both
    methods to use errors="replace" and warn whenever that safety net
    actually fires, so a crash becomes a visible warning instead.
    """

    # Signature mirrors mlx_vlm's own add_token verbatim (mutable default
    # included) — this replaces that method, so it must match it.
    def add_token(self, token, skip_special_token_ids: list = []):  # noqa: B006
        if token in skip_special_token_ids:
            return
        v = self.tokenmap[token]
        if self._byte_decoder[v[0]] == 32:
            raw = bytearray(self._byte_decoder[c] for c in self._unflushed)
            try:
                current_text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                print(
                    f"      WARNING: stream decode failed mid-generation ({exc}); "
                    f"substituting U+FFFD for {bytes(raw)!r}",
                    file=sys.stderr,
                )
                current_text = raw.decode("utf-8", errors="replace")
            if self.text or not self.trim_space:
                self.text += current_text
            else:
                self.text += _remove_space(current_text)
            self._unflushed = v
        else:
            self._unflushed += v

    def finalize(self):
        raw = bytearray(self._byte_decoder[c] for c in self._unflushed)
        try:
            current_text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            print(
                f"      WARNING: stream decode failed at finalize ({exc}); "
                f"substituting U+FFFD for {bytes(raw)!r}",
                file=sys.stderr,
            )
            current_text = raw.decode("utf-8", errors="replace")
        if self.text or not self.trim_space:
            self.text += current_text
        else:
            self.text += _remove_space(current_text)
        self._unflushed = ""

    BPEStreamingDetokenizer.add_token = add_token
    BPEStreamingDetokenizer.finalize = finalize


# dots.mocr-8bit is the quantization the proven full-volume run used (126/126
# pages of the Impellitteri-Sharkey volume); the prototype's own default was
# 4bit. Pinned here so a volume OCR'd today matches one OCR'd last month —
# changing it changes the text, so it is a deliberate, recorded choice (the
# resolved model id is written into the provenance sidecar per record).
DEFAULT_MODEL = "mlx-community/dots.mocr-8bit"
# The upstream (unconverted) checkpoint the mlx-community weights above were
# themselves converted from -- used by the cuda backend, which runs it through
# transformers instead of mlx-vlm. NOT interchangeable with DEFAULT_MODEL: a
# repo id valid for one backend is meaningless to the other.
DEFAULT_MODEL_CUDA = "rednote-hilab/dots.ocr"
DEFAULT_DPI = 200
DEFAULT_MAX_TOKENS = 12000

# Provenance tag stamped on records read by this backend. Distinct from ocr.py's
# "ocr" (ocrmypdf/Tesseract) so the two OCR lineages stay separable in the corpus
# — they have different failure modes and different quality profiles, and a
# consumer must be able to tell which read a given order.
TEXT_SOURCE_OCR_VLM = "ocr-vlm"
# The backend ran but yielded no usable text for a document (an unparseable page,
# a repetition loop, a page the model returned empty). Parallel to ocr.py's
# "ocr-failed", and kept separate from it for the same reason as above.
TEXT_SOURCE_OCR_VLM_FAILED = "ocr-vlm-failed"

# The prompt ships inside the package (beside lexicon.py's wordlist.txt) so a
# checkout is self-contained and the prompt can be hashed for provenance.
DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent / "data" / "dots_ocr_prompt.txt"

# Blank/bleed-through page detection (see page_ink_stats()). Real printed
# text/tables carry a meaningful fraction of true-black ink pixels and higher
# grayscale contrast than faint show-through from the reverse side of thin
# scanned paper. This only holds within the page's interior, though -- the
# rendered page also includes the book's binding and the black scanner-bed
# background around the edges, which are dark/high-contrast on *every* page
# regardless of content and otherwise swamp the signal, so stats are computed
# over a cropped central ROI first (see ROI_MARGIN). Both conditions must
# hold (AND, not OR) before a page is skipped, to keep false-positive skips
# (silently dropping real content) rare -- tune via --dark-pixel-threshold /
# --min-dark-fraction / --min-contrast-std / --ink-roi-margin after checking
# a run's page_stats. Calibrated against one volume's scans (2276x2989 @
# 200dpi); re-check page_stats on other volumes before trusting the defaults.
DEFAULT_DARK_PIXEL_THRESHOLD = 100
DEFAULT_MIN_DARK_FRACTION = 0.001
DEFAULT_MIN_CONTRAST_STD = 6.5
DEFAULT_INK_ROI_MARGIN = 0.12

# Page rotation (see detect_page_rotation). "auto" infers from page shape;
# an explicit angle forces every page of the run, which is the escape hatch
# for a volume the shape heuristic reads wrong.
ROTATE_CHOICES = ["auto", "0", "90", "180", "270"]
DEFAULT_ROTATE = "auto"

# Token log-probability below which a token is counted as "low confidence" in
# the per-page summary. -2.0 is roughly a 13% chance the model assigned to the
# token it actually emitted. This is a triage knob, not a calibrated threshold:
# raw logprobs are not probabilities of *correctness*, they just tend to dip
# where the model is guessing. Tune against a hand-checked page set.
DEFAULT_LOW_LOGPROB = -2.0
# Number of worst-scoring tokens kept per page for display in the viewer.
WORST_TOKEN_SAMPLE = 12

# Ink-coverage reconciliation (see ink_coverage()). Uncovered ink is grouped
# into cells rather than reported per-pixel, so that a missed paragraph reads as
# one region instead of thousands of speckles.
DEFAULT_UNCOVERED_CELL_PX = 24
# Ink pixels a cell must hold before it counts as inked at all. At 200 DPI a
# 24x24 cell over typewritten text runs well above this; stray scanner noise and
# JPEG ringing fall below it.
UNCOVERED_CELL_MIN_INK_FRACTION = 0.025
# Total ink a connected group of cells must hold to be worth reporting. 400px at
# 200 DPI is a few words -- below that it is speckle, a rule, or a stamp edge.
DEFAULT_MIN_UNCOVERED_INK = 400
# Most uncovered regions reported per page, largest first.
MAX_UNCOVERED_REGIONS = 12

# MLX-ready quantizations published by mlx-community, in two families:
# dots.mocr (newer -- stronger multilingual parsing + structured-graphics
# output) and the original dots.ocr. Within a family, smaller quant = less
# memory + faster, at some accuracy cost; bf16 is full precision. All fit
# comfortably in 18GB of unified memory; 4bit is a good default. cuda has no
# equivalent fixed list -- it takes any transformers-loadable HF repo id, with
# --quantization choosing bitsandbytes 8bit/4bit/none at load time instead of
# picking a pre-quantized repo.
MLX_MODEL_CHOICES = [
    "mlx-community/dots.mocr-4bit",
    "mlx-community/dots.mocr-5bit",
    "mlx-community/dots.mocr-6bit",
    "mlx-community/dots.mocr-8bit",
    "mlx-community/dots.mocr-bf16",
    "mlx-community/dots.mocr-mxfp4",
    "mlx-community/dots.mocr-mxfp8",
    "mlx-community/dots.mocr-nvfp4",
    "mlx-community/dots.ocr-4bit",
    "mlx-community/dots.ocr-5bit",
    "mlx-community/dots.ocr-6bit",
    "mlx-community/dots.ocr-8bit",
    "mlx-community/dots.ocr-bf16",
    "mlx-community/dots.ocr-mxfp4",
    "mlx-community/dots.ocr-mxfp8",
    "mlx-community/dots.ocr-nvfp4",
]

# One distinct color per dots.ocr layout category, for the bbox overlays.
CATEGORY_COLORS = {
    "Caption": (166, 118, 29),
    "Footnote": (127, 127, 127),
    "Formula": (214, 39, 40),
    "Handwriting": (255, 0, 255),
    "List-item": (44, 160, 44),
    "Page-footer": (140, 86, 75),
    "Page-header": (140, 86, 75),
    "Picture": (148, 103, 189),
    "Section-header": (255, 127, 14),
    "Table": (23, 190, 207),
    "Text": (31, 119, 180),
    "Title": (227, 119, 194),
}
UNKNOWN_CATEGORY_COLOR = (0, 0, 0)
# Ink the model returned no bbox for, drawn on the overlay in alarm red so a
# missed region is visible at a glance next to the categories it sits among.
UNCOVERED_INK_COLOR = (255, 0, 0)

# dots.ocr's image processor (Qwen2-VL style) resizes the input image before
# the model ever sees it -- rounding to multiples of 28px and capping total
# area -- and returns bboxes in THAT resized image's pixel space, not the
# original's. Reproduced here (see the model card's "bbox coordinates" note)
# so overlays land on the right pixels instead of drifting for large/odd-size
# page renders.
IMAGE_FACTOR = 28
MIN_PIXELS = 56 * 56
MAX_PIXELS = 11_289_600

GB = 1024**3
# Per-page allocated-memory drift above the post-model-load baseline that is
# worth shouting about. Steady state is flat, so this is generous.
MEMORY_DRIFT_WARN_GB = 0.05
# getrusage reports ru_maxrss in bytes on macOS but kilobytes on Linux.
RSS_UNIT = 1 if sys.platform == "darwin" else 1024


def peak_rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * RSS_UNIT / GB


class _MlxMemSampler:
    def __init__(self, mx) -> None:
        self._mx = mx

    def reset_peak(self) -> None:
        self._mx.reset_peak_memory()

    def active_gb(self) -> float:
        return self._mx.get_active_memory() / GB

    def peak_gb(self) -> float:
        return self._mx.get_peak_memory() / GB

    def cache_gb(self) -> float:
        return self._mx.get_cache_memory() / GB


class _CudaMemSampler:
    def __init__(self, torch) -> None:
        self._torch = torch

    def reset_peak(self) -> None:
        self._torch.cuda.reset_peak_memory_stats()

    def active_gb(self) -> float:
        return self._torch.cuda.memory_allocated() / GB

    def peak_gb(self) -> float:
        return self._torch.cuda.max_memory_allocated() / GB

    def cache_gb(self) -> float:
        return self._torch.cuda.memory_reserved() / GB


def mem_sampler_for(be: "_Backend | _CudaBackend") -> "_MlxMemSampler | _CudaMemSampler":
    return _MlxMemSampler(be.mx) if be.kind == "mlx" else _CudaMemSampler(be.torch)


class MemoryProfiler:
    """Records memory high-water marks around each pipeline stage.

    Two numbers, because both backends draw from one pool shared with
    everything else in the process (MLX's unified memory; the CUDA process's
    default device):

      accel_peak  largest live backend allocation during the stage -- weights,
                  KV cache, activations. This is what grows with --max-tokens
                  and with page size (more pixels -> more vision tokens).
      rss_peak    peak resident set for the whole process, so it also covers
                  the rendered PNGs, PyMuPDF, Pillow, and the Python heap. Note
                  this is monotonic (a lifetime high-water mark), so it only
                  ever rises across stages. On cuda this does NOT include GPU
                  memory (that's accel_* only); on mlx it does, since unified
                  memory is process RSS.

    accel_cache is memory the backend is holding onto for reuse rather than
    returning to the OS/driver; on mlx it counts against rss but is available
    for the next allocation, on cuda it's what nvidia-smi shows as "used" past
    what's actively allocated.
    """

    def __init__(self, enabled: bool, mem=None) -> None:
        self.enabled = enabled
        # None in the render-only paths (--classify-blank-only), where no
        # backend is ever imported. Timings are still recorded; the
        # accelerator numbers are simply absent rather than the run failing.
        self._mem = mem
        self.samples: list[dict] = []

    @contextmanager
    def stage(self, label: str):
        if not self.enabled:
            yield
            return
        mem = self._mem
        if mem is not None:
            # Reset so the peak we read back is this stage's, not the whole run's.
            mem.reset_peak()
        t0 = time.time()
        yield
        sample = {
            "stage": label,
            "seconds": round(time.time() - t0, 2),
            "accel_active_gb": round(mem.active_gb(), 3) if mem else 0.0,
            "accel_peak_gb": round(mem.peak_gb(), 3) if mem else 0.0,
            "accel_cache_gb": round(mem.cache_gb(), 3) if mem else 0.0,
            "rss_peak_gb": round(peak_rss_gb(), 3),
        }
        self.samples.append(sample)
        print(
            f"      [mem] {label}: accel peak {sample['accel_peak_gb']:.2f} GB"
            f" (active {sample['accel_active_gb']:.2f}, cache {sample['accel_cache_gb']:.2f}),"
            f" process rss peak {sample['rss_peak_gb']:.2f} GB"
        )

    def summary(self) -> dict:
        page_peaks = [s["accel_peak_gb"] for s in self.samples if s["stage"].startswith("page ")]
        return {
            "accel_peak_gb": max((s["accel_peak_gb"] for s in self.samples), default=0.0),
            "rss_peak_gb": round(peak_rss_gb(), 3),
            "per_page_accel_peak_gb": {
                "min": min(page_peaks, default=0.0),
                "max": max(page_peaks, default=0.0),
                "mean": round(sum(page_peaks) / len(page_peaks), 3) if page_peaks else 0.0,
            },
            "stages": self.samples,
        }

    def report(self, out_path: Path) -> None:
        if not self.enabled:
            return
        summary = self.summary()
        out_path.write_text(json.dumps(summary, indent=2))
        print("\nmemory profile:")
        print(f"  accel peak:          {summary['accel_peak_gb']:.2f} GB")
        print(f"  process rss peak:    {summary['rss_peak_gb']:.2f} GB")
        per_page = summary["per_page_accel_peak_gb"]
        if per_page["max"]:
            print(
                f"  per-page accel peak: min {per_page['min']:.2f} /"
                f" mean {per_page['mean']:.2f} / max {per_page['max']:.2f} GB"
            )


def smart_resize(height: int, width: int) -> tuple[int, int]:
    def round_by_factor(x: float) -> int:
        return max(IMAGE_FACTOR, round(x / IMAGE_FACTOR) * IMAGE_FACTOR)

    h_bar, w_bar = round_by_factor(height), round_by_factor(width)
    if h_bar * w_bar > MAX_PIXELS:
        beta = math.sqrt((height * width) / MAX_PIXELS)
        h_bar = max(IMAGE_FACTOR, math.floor(height / beta / IMAGE_FACTOR) * IMAGE_FACTOR)
        w_bar = max(IMAGE_FACTOR, math.floor(width / beta / IMAGE_FACTOR) * IMAGE_FACTOR)
    elif h_bar * w_bar < MIN_PIXELS:
        beta = math.sqrt(MIN_PIXELS / (height * width))
        h_bar = math.ceil(height * beta / IMAGE_FACTOR) * IMAGE_FACTOR
        w_bar = math.ceil(width * beta / IMAGE_FACTOR) * IMAGE_FACTOR
    return h_bar, w_bar


def rescale_bbox(
    bbox: list[float], orig_w: int, orig_h: int, resized_w: int, resized_h: int
) -> tuple[float, float, float, float]:
    scale_x = orig_w / resized_w
    scale_y = orig_h / resized_h
    x1, y1, x2, y2 = bbox
    return x1 * scale_x, y1 * scale_y, x2 * scale_x, y2 * scale_y


def detect_page_rotation(page: fitz.Page) -> int:
    """Degrees CLOCKWISE this page must be rotated to bring its text upright.

    Two of the fourteen volumes -- 1968-01-10_1969-12-29_Lindsay_Orders (389
    pages) and 1970-01-01_1971-12-09_Lindsay_Orders (422) -- were bound and
    scanned sideways: every page is landscape-shaped with portrait text running
    bottom-to-top. Nothing in the file says so. `/Rotate` is 0 on all 2,936
    pages of all 14 volumes, and dots.ocr reports no orientation of its own --
    its JSON carries only axis-aligned bbox/category/text, and prompting it for
    an angle does not work (it answers with page text instead of an angle).

    Page SHAPE is the signal that is actually available, and here it is exact:
    811/811 pages of those two volumes are landscape, against 0/2547 across the
    other twelve. So a landscape page means a sideways scan.

    The direction is fixed at 90 rather than inferred: both volumes need the
    same quarter turn clockwise, verified against the rendered pages. Nothing
    here can tell 90 from 270 -- that needs to read the text -- so a volume
    that turns the other way needs --rotate 270, and 180 (upside down, same
    shape) is invisible to a shape test by construction. Both of those would
    show up the way this one did: as a volume whose pages fail to parse.
    """
    return 90 if page.rect.width > page.rect.height else 0


def render_pdf_pages(
    pdf_path: Path,
    out_dir: Path,
    dpi: int,
    page_limit: int | None,
    start_page: int = 1,
    rotate: str = DEFAULT_ROTATE,
) -> list[tuple[Path, dict]]:
    """Renders page_limit pages (or the rest of the PDF) starting at the 1-based
    start_page. Filenames carry the absolute page number, so rendering a later
    slice into an existing run directory extends it instead of renumbering it.

    Returns (path, rotation) per page, where `rotation` is the dict main() writes
    into the page record verbatim. Pages are rendered ALREADY UPRIGHT: every
    measurement downstream (ink stats, smart_resize, bboxes, coverage, overlays)
    then works in one consistent upright space and needs no knowledge of the
    rotation. It is recorded anyway because the raw PNG on disk no longer matches
    the PDF page's own geometry, and `scripts/run_picture_clips.py` has to undo
    it to map a bbox back onto the PDF.

    `applied_cw` 0 is recorded rather than omitted: a consumer needs to tell
    "checked, already upright" from "this JSON predates rotation handling", and
    an absent key is the only way to say the latter.

    rotate is "auto" (per-page detect_page_rotation) or an explicit angle
    forcing every page.
    """
    doc = fitz.open(pdf_path)
    try:
        first = start_page - 1
        if first >= doc.page_count:
            return []
        last = doc.page_count if page_limit is None else min(first + page_limit, doc.page_count)
        zoom = dpi / 72.0
        rendered = []
        for i in range(first, last):
            page = doc[i]
            applied = detect_page_rotation(page) if rotate == "auto" else int(rotate)
            # fitz.Matrix(deg) rotates CLOCKWISE, matching the convention above
            # (verified pixel-identical to PIL's rotate(-deg, expand=True)).
            matrix = fitz.Matrix(zoom, zoom)
            if applied:
                matrix = matrix * fitz.Matrix(applied)
            pix = page.get_pixmap(matrix=matrix)
            path = out_dir / f"page_{i + 1:04d}.png"
            pix.save(path)
            rendered.append((
                path,
                {
                    "applied_cw": applied,
                    "source": "auto-aspect" if rotate == "auto" else "forced",
                    "pdf_page_size": [round(page.rect.width, 2), round(page.rect.height, 2)],
                },
            ))
        return rendered
    finally:
        doc.close()


def load_gray(image_path: Path) -> np.ndarray:
    """The rendered page as a grayscale array. Decoded once per page and shared
    by every ink measurement below, since these images are ~7 megapixels."""
    with Image.open(image_path) as im:
        return np.asarray(im.convert("L"), dtype=np.uint8)


def roi_bounds(shape: tuple[int, int], roi_margin: float) -> tuple[int, int, int, int]:
    """The page's interior as (left, top, right, bottom) pixel bounds, cropping
    the outer roi_margin fraction off each side. Everything that measures ink
    works inside this box: the book's binding and the black scanner-bed
    background around the page are dark on *every* page regardless of content,
    and would otherwise dominate whatever is being measured."""
    h, w = shape
    return (
        int(w * roi_margin),
        int(h * roi_margin),
        int(w * (1 - roi_margin)),
        int(h * (1 - roi_margin)),
    )


def page_ink_stats(gray: np.ndarray, dark_pixel_threshold: int, roi_margin: float) -> dict:
    """Cheap blank/bleed-through signal computed straight from the rendered
    page image, before any OCR is run. Real printed text/tables carry a
    meaningful fraction of true-black ink pixels and high grayscale
    contrast; faint show-through from the reverse side of thin scanned
    paper rarely gets that dark and stays low-contrast. Measured over the
    page interior only -- see roi_bounds()."""
    left, top, right, bottom = roi_bounds(gray.shape, roi_margin)
    arr = gray[top:bottom, left:right]
    return {
        "mean": round(float(arr.mean()), 2),
        "std": round(float(arr.std()), 2),
        "dark_fraction": round(float((arr < dark_pixel_threshold).mean()), 5),
    }


def is_blank_or_bleedthrough(stats: dict, min_dark_fraction: float, min_contrast_std: float) -> bool:
    return stats["dark_fraction"] < min_dark_fraction and stats["std"] < min_contrast_std


def find_uncovered_regions(
    uncovered: np.ndarray, cell_px: int, min_region_ink: int, offset: tuple[int, int]
) -> list[dict]:
    """Group leftover ink pixels into a handful of reportable rectangles.

    Works on a coarse grid rather than the pixel mask: a cell counts as inked
    once it holds enough ink to be type rather than speckle, adjacent inked
    cells are merged, and a merged group has to carry min_region_ink pixels
    before it is worth anyone's attention. The result is "the model missed this
    paragraph" instead of ten thousand loose pixels. Returned bboxes are in
    full-page coordinates (offset is the ROI's top-left corner)."""
    h, w = uncovered.shape
    if h == 0 or w == 0:
        return []
    grid_h, grid_w = math.ceil(h / cell_px), math.ceil(w / cell_px)
    padded = np.zeros((grid_h * cell_px, grid_w * cell_px), dtype=np.int32)
    padded[:h, :w] = uncovered
    # Ink pixels per cell, by folding each cell's rows and columns into one sum.
    cells = padded.reshape(grid_h, cell_px, grid_w, cell_px).sum(axis=(1, 3))
    hot = cells >= max(1, int(cell_px * cell_px * UNCOVERED_CELL_MIN_INK_FRACTION))

    off_x, off_y = offset
    seen = np.zeros_like(hot, dtype=bool)
    regions = []
    for row in range(grid_h):
        for col in range(grid_w):
            if not hot[row, col] or seen[row, col]:
                continue
            # Flood-fill this group of touching inked cells.
            stack, group = [(row, col)], []
            seen[row, col] = True
            while stack:
                r, c = stack.pop()
                group.append((r, c))
                for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < grid_h and 0 <= nc < grid_w and hot[nr, nc] and not seen[nr, nc]:
                        seen[nr, nc] = True
                        stack.append((nr, nc))
            rows, cols = [p[0] for p in group], [p[1] for p in group]
            ink_px = int(cells[rows, cols].sum())
            if ink_px < min_region_ink:
                continue
            regions.append(
                {
                    "bbox": [
                        off_x + min(cols) * cell_px,
                        off_y + min(rows) * cell_px,
                        off_x + min((max(cols) + 1) * cell_px, w),
                        off_y + min((max(rows) + 1) * cell_px, h),
                    ],
                    "ink_px": ink_px,
                }
            )
    regions.sort(key=lambda r: r["ink_px"], reverse=True)
    return regions[:MAX_UNCOVERED_REGIONS]


def ink_coverage(
    gray: np.ndarray,
    elements: list[dict],
    resized_w: int,
    resized_h: int,
    dark_pixel_threshold: int,
    roi_margin: float,
    cell_px: int,
    min_region_ink: int,
) -> dict:
    """Reconcile the ink actually on the page against the boxes the model
    returned, and record what didn't line up.

    This is the only QA signal here that can see *dropped* content. A page where
    the model silently skipped a paragraph produces perfectly well-formed JSON
    that parses, reads fine, and is missing a paragraph -- nothing in the text
    itself gives that away, but the ink is still sitting there outside every
    bbox. Two directions are measured:

      covered_fraction   ink inside some bbox / all ink. Low means content was
                         dropped, and uncovered_regions says where.
      per-element ink     ink and area under each individual bbox, so a box that
                         covers a dense block but returned five characters (low
                         chars_per_1k_ink) stands out even when page coverage is
                         fine.

    Measured over the page interior only (see roi_bounds), so ink in the outer
    margin -- and any header or footer sitting out there -- is outside both the
    numerator and the denominator."""
    h, w = gray.shape
    left, top, right, bottom = roi_bounds(gray.shape, roi_margin)
    roi_ink = gray[top:bottom, left:right] < dark_pixel_threshold
    total_ink = int(roi_ink.sum())
    covered = np.zeros(roi_ink.shape, dtype=bool)
    roi_h, roi_w = roi_ink.shape

    for el in elements:
        bbox = el.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        # Model bboxes are in the resized image's pixel space, and the model
        # sometimes returns them with the corners the other way round.
        bx1, by1, bx2, by2 = rescale_bbox(bbox, w, h, resized_w, resized_h)
        x1 = int(max(0, min(math.floor(min(bx1, bx2)) - left, roi_w)))
        x2 = int(max(0, min(math.ceil(max(bx1, bx2)) - left, roi_w)))
        y1 = int(max(0, min(math.floor(min(by1, by2)) - top, roi_h)))
        y2 = int(max(0, min(math.ceil(max(by1, by2)) - top, roi_h)))
        if x2 <= x1 or y2 <= y1:
            # Entirely outside the interior -- typically a page header or footer
            # sitting in the cropped margin. Not measurable, so say so.
            el["ink"] = None
            continue
        covered[y1:y2, x1:x2] = True
        el_ink = int(roi_ink[y1:y2, x1:x2].sum())
        area = (y2 - y1) * (x2 - x1)
        chars = len(el.get("text") or "")
        el["ink"] = {
            "ink_px": el_ink,
            "area_px": area,
            "density": round(el_ink / area, 5),
            "chars": chars,
            # Characters returned per 1000 ink pixels under the box. A dense
            # block that produced almost no text scores near zero.
            "chars_per_1k_ink": round(1000 * chars / el_ink, 2) if el_ink else None,
        }

    covered_ink = int((roi_ink & covered).sum())
    return {
        "roi": [left, top, right, bottom],
        "ink_px": total_ink,
        "covered_px": covered_ink,
        "covered_fraction": round(covered_ink / total_ink, 5) if total_ink else None,
        "uncovered_px": total_ink - covered_ink,
        "uncovered_regions": find_uncovered_regions(
            roi_ink & ~covered, cell_px, min_region_ink, (left, top)
        ),
        "cell_px": cell_px,
    }


def chosen_token_logprob(logprobs, token: int | None) -> float | None:
    """The log-probability the model gave the token it actually emitted.

    stream_generate hands back the whole vocabulary's log-probabilities for the
    step (shape [vocab], occasionally [1, vocab]), so pull out the one entry
    that matters and drop the rest -- retaining full distributions for every
    token would cost hundreds of MB on a dense page."""
    if logprobs is None or token is None:
        return None
    try:
        row = logprobs
        if getattr(row, "ndim", 1) == 2 and row.shape[0] == 1:
            row = row[0]
        value = float(row[token])
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def decode_ids(tokenizer, ids: list[int]) -> str:
    """Decode a run of tokens, skipping special tokens where supported."""
    try:
        try:
            return tokenizer.decode(ids, skip_special_tokens=True)
        except TypeError:  # a tokenizer whose decode() has no such kwarg
            return tokenizer.decode(ids)
    except (UnicodeDecodeError, ValueError):
        return ""


def decode_token(tokenizer, token_id: int) -> str:
    return decode_ids(tokenizer, [token_id])


# Tokens of left context used when measuring one token's contribution. A
# multi-byte UTF-8 character spans at most a handful of byte-level BPE tokens,
# so a small window is enough to decode the boundary correctly.
TOKEN_SPAN_CONTEXT = 8


def exact_token_segments(
    tokenizer, token_ids: list[int], expected_len: int
) -> list[tuple[int, int, int, int]] | None:
    """One (char_start, char_end, i, i) segment per token -- exact offsets.

    The streaming detokenizer flushes on word boundaries, so the segments it
    yields cover several tokens at once and a span that ends mid-chunk pulls in
    whatever else shared that chunk. For attribution that means JSON scaffolding
    -- a trailing `"},`, the leading digits of the next bbox -- gets credited to
    the element's text. Measuring each token on its own removes the ambiguity:
    a token's contribution is what its arrival adds to the decoded string.

    Returns None if the reconstruction doesn't add up to the streamed text, so
    the caller can fall back rather than attribute against bad offsets."""
    segments = []
    pos = 0
    for i in range(len(token_ids)):
        lo = max(0, i - TOKEN_SPAN_CONTEXT)
        prefix = decode_ids(tokenizer, token_ids[lo:i])
        full = decode_ids(tokenizer, token_ids[lo : i + 1])
        piece_len = max(0, len(full) - len(prefix))
        segments.append((pos, pos + piece_len, i, i))
        pos += piece_len
    return segments if pos == expected_len else None


def _logprob_scope_stats(
    scored: list[tuple[int, float]], threshold: float, token_ids: list[int], tokenizer, worst_n: int
) -> dict | None:
    """Distribution stats over one subset of (token index, logprob) pairs."""
    if not scored:
        return None
    values = np.asarray([lp for _, lp in scored], dtype=np.float64)
    stats = {
        "n_tokens": len(scored),
        "mean": round(float(values.mean()), 4),
        "min": round(float(values.min()), 4),
        "p05": round(float(np.percentile(values, 5)), 4),
        "p50": round(float(np.percentile(values, 50)), 4),
        "n_below_threshold": int((values < threshold).sum()),
    }
    if worst_n:
        # Only tokens carrying actual uncertainty. A page can easily have fewer
        # than worst_n of them -- most tokens on a clean scan score exactly 0.0,
        # and padding the list out with certainties would present maximally
        # confident tokens under a "least confident" heading.
        uncertain = [pair for pair in scored if pair[1] < 0]
        stats["worst_tokens"] = [
            {
                "index": i,
                "token_id": token_ids[i],
                "logprob": round(lp, 4),
                "text": decode_token(tokenizer, token_ids[i]),
            }
            for i, lp in sorted(uncertain, key=lambda pair: pair[1])[:worst_n]
        ]
    return stats


def summarize_logprobs(
    token_logprobs: list[float | None],
    token_ids: list[int],
    tokenizer,
    threshold: float,
    scopes: dict[str, set[int]],
) -> dict | None:
    """Per-page confidence summary from the decoder's own numbers.

    Reported in three scopes, because they measure different things and the
    differences are not small:

      content  tokens inside some element's text -- what the page actually says.
               This is the scope that answers "should a human read this page
               again".
      layout   tokens inside a bbox or category value -- where the model decided
               each region was and what kind of region it is. Routinely much
               less certain than the text and usually harmlessly so: hesitating
               over whether a box edge is at 1893 or 1894 costs nothing. Worth
               watching for category flips and for wildly unsure coordinates.
      all      every generated token, including JSON punctuation and key names.

    Scoping matters because layout and scaffolding together outnumber the
    content, and would otherwise monopolise any "least confident tokens" list
    while saying nothing about whether the page was read correctly.

    No scope is a calibrated probability of being *correct* -- the model is
    routinely confident and wrong, especially on a character it simply misread.
    What they do is concentrate, which is enough to rank a review queue."""
    scored = [(i, lp) for i, lp in enumerate(token_logprobs) if lp is not None]
    if not scored:
        return None
    summary = {"threshold": threshold, "n_generated_tokens": len(token_logprobs)}
    for name in ("content", "layout"):
        subset = [pair for pair in scored if pair[0] in scopes.get(name, set())]
        summary[name] = _logprob_scope_stats(
            subset, threshold, token_ids, tokenizer, WORST_TOKEN_SAMPLE
        )
    summary["all"] = _logprob_scope_stats(scored, threshold, token_ids, tokenizer, 0)
    return summary


# The opening of each field's value in the model's JSON output.
FIELD_PATTERNS = {
    "bbox": re.compile(r'"bbox"\s*:\s*\['),
    "category": re.compile(r'"category"\s*:\s*"'),
    "text": re.compile(r'"text"\s*:\s*"'),
}


def find_string_field_span(raw_text: str, field: str, value: str, cursor: int) -> tuple[int, int] | None:
    """Span of a string field's value, anchored on its key.

    Anchoring matters because a bare substring search is badly unsafe for short
    values: a Page-footer whose text is "2" otherwise matches the first "2" in
    the output, which is almost always a digit of an earlier bbox coordinate.
    The parsed value is unescaped, so match against the JSON-escaped form -- the
    byte sequence the model actually emitted -- and require it to sit exactly
    where the key says it should."""
    escaped = json.dumps(value, ensure_ascii=False)[1:-1]
    for match in FIELD_PATTERNS[field].finditer(raw_text, cursor):
        start = match.end()
        if raw_text.startswith(escaped, start):
            return start, start + len(escaped)
    return None


def find_bbox_span(raw_text: str, bbox: list, cursor: int) -> tuple[int, int] | None:
    """Span of a bbox's coordinate list, between its brackets.

    Candidates are checked by parsing the numbers and comparing them to the
    element's own bbox, so an element is never attributed a neighbour's
    coordinates just because the keys line up."""
    for match in FIELD_PATTERNS["bbox"].finditer(raw_text, cursor):
        start = match.end()
        end = raw_text.find("]", start)
        if end == -1:
            continue
        try:
            if json.loads(f"[{raw_text[start:end]}]") == list(bbox):
                return start, end
        except ValueError:
            continue
    return None


def find_text_span(raw_text: str, text: str, cursor: int) -> tuple[int, int] | None:
    """Span of an element's text value, with a plain search as a last resort for
    output that can't be lined up against the schema at all."""
    span = find_string_field_span(raw_text, "text", text, cursor)
    if span:
        return span
    escaped = json.dumps(text, ensure_ascii=False)[1:-1]
    for needle in (escaped, text):
        if not needle:
            continue
        pos = raw_text.find(needle, cursor)
        if pos != -1:
            return pos, pos + len(needle)
    return None


def tokens_in_span(
    segments: list[tuple[int, int, int, int]], span: tuple[int, int], n_tokens: int
) -> set[int]:
    """Token indices overlapping a character span."""
    start, end = span
    indices: set[int] = set()
    for seg_start, seg_end, tok_lo, tok_hi in segments:
        if seg_start < end and seg_end > start:
            indices.update(range(tok_lo, tok_hi + 1))
    return {i for i in indices if i < n_tokens}


def _span_stats(indices: set[int], token_logprobs: list[float | None]) -> dict | None:
    values = [token_logprobs[i] for i in sorted(indices) if token_logprobs[i] is not None]
    if not values:
        return None
    return {
        "n_tokens": len(values),
        "mean": round(sum(values) / len(values), 4),
        "min": round(min(values), 4),
    }


def attribute_element_logprobs(
    elements: list[dict], raw_text: str, segments: list[tuple[int, int, int, int]],
    token_logprobs: list[float | None],
) -> dict[str, set[int]]:
    """Attach each element's per-field confidence stats, in place.

    Every field the model emits is located in the raw output and mapped back to
    the tokens that produced it, so an element ends up with separate numbers for
    what it *read* (text) and where it decided the box was (bbox, category):

        "logprob_stats": {"text": {...}, "bbox": {...}, "category": {...}}

    Precision depends on the segments passed in: exact_token_segments() gives
    one token each, so only tokens genuinely inside a field's value are counted;
    the streaming detokenizer's own chunks are coarser and over-credit at the
    boundaries. Fields that can't be located get None rather than a wrong
    answer.

    Returns the token indices for each page-level scope -- "content" (text) and
    "layout" (bbox + category) -- which is what scopes the page summary. See
    summarize_logprobs()."""
    scopes: dict[str, set[int]] = {"content": set(), "layout": set()}
    n = len(token_logprobs)
    cursor = 0
    for el in elements:
        bbox, category, text = el.get("bbox"), el.get("category"), el.get("text")
        spans = {
            "bbox": find_bbox_span(raw_text, bbox, cursor) if isinstance(bbox, list) else None,
            "category": (
                find_string_field_span(raw_text, "category", category, cursor)
                if isinstance(category, str) and category
                else None
            ),
            "text": (
                find_text_span(raw_text, text, cursor)
                if isinstance(text, str) and text
                else None
            ),
        }
        stats = {}
        # `field_name`, not `field` — the latter shadows dataclasses.field, which
        # this module imports at the top.
        for field_name, scope in (("bbox", "layout"), ("category", "layout"),
                                  ("text", "content")):
            span = spans[field_name]
            if span is None:
                stats[field_name] = None
                continue
            indices = tokens_in_span(segments, span, n)
            scopes[scope] |= indices
            stats[field_name] = _span_stats(indices, token_logprobs)
        el["logprob_stats"] = stats
        # Start the next element after everything this one claimed, so repeated
        # values (blank index cells, boilerplate headers) don't rematch earlier.
        cursor = max([span[1] for span in spans.values() if span] or [cursor])
    return scopes


def extract_json(text: str):
    """Pull the JSON value out of a model response, tolerating ```json fences
    or stray prose the model wraps around the actual JSON."""
    stripped = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    for open_ch, close_ch in (("[", "]"), ("{", "}")):
        start, end = stripped.find(open_ch), stripped.rfind(close_ch)
        if start != -1 and end > start:
            try:
                return json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("could not find valid JSON in model output")


def normalize_elements(parsed) -> list[dict]:
    """dots.ocr usually returns a bare JSON array; be lenient about the rest."""
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("elements", "layout", "results"):
            if isinstance(parsed.get(key), list):
                return parsed[key]
        return [parsed]
    return []


def draw_box_label(draw: ImageDraw.ImageDraw, x: float, y: float, text: str, color, font) -> None:
    """A filled caption tag sitting just above (x, y), the box's top-left."""
    label_bbox = draw.textbbox((0, 0), text, font=font)
    label_w, label_h = label_bbox[2] - label_bbox[0], label_bbox[3] - label_bbox[1]
    label_top = max(0, y - label_h - 4)
    draw.rectangle([x, label_top, x + label_w + 6, label_top + label_h + 4], fill=color)
    draw.text((x + 3, label_top + 1), text, fill=(255, 255, 255), font=font)


def draw_overlay(
    image_path: Path,
    elements: list[dict],
    resized_w: int,
    resized_h: int,
    out_path: Path,
    uncovered_regions: list[dict] = (),
) -> None:
    img = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except OSError:
        font = ImageFont.load_default()

    for el in elements:
        bbox = el.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        category = el.get("category", "Unknown")
        color = CATEGORY_COLORS.get(category, UNKNOWN_CATEGORY_COLOR)
        bx1, by1, bx2, by2 = rescale_bbox(bbox, img.width, img.height, resized_w, resized_h)
        # Same corner-swapping the model does to ink_coverage's boxes: ImageDraw
        # raises on a reversed rectangle rather than drawing it, so a single
        # inverted box would otherwise kill the run at the very last step.
        x1, x2 = min(bx1, bx2), max(bx1, bx2)
        y1, y2 = min(by1, by2), max(by1, by2)
        draw.rectangle([x1, y1, x2, y2], outline=color, width=3)
        draw_box_label(draw, x1, y1, category, color, font)

    # Drawn last and heavier than the category boxes: this is ink the model
    # returned nothing for, and it should be the first thing seen on the page.
    for region in uncovered_regions:
        x1, y1, x2, y2 = region["bbox"]
        draw.rectangle([x1, y1, x2, y2], outline=UNCOVERED_INK_COLOR, width=5)
        draw_box_label(draw, x1, y1, "UNCOVERED INK", UNCOVERED_INK_COLOR, font)

    img.save(out_path)


def element_to_markdown(el: dict) -> str:
    category = el.get("category", "Text")
    text = el.get("text", "")
    if category == "Picture":
        return f"*[Picture — bbox {el.get('bbox')}]*"
    if category == "Title" and text:
        return f"# {text}"
    if category == "Section-header" and text:
        return f"## {text}"
    return text


def collect_page_records(json_dir: Path) -> list[dict]:
    """Every per-page record in a json dir, in page order -- including pages
    written by earlier runs, since --start-page extends a directory rather than
    replacing it. This is what lets anything whole-document (the stitched
    Markdown, the combined JSON, both via scripts/render_volume_document.py) be
    built from the records on demand instead of stored beside them."""
    records = []
    for path in sorted(json_dir.glob("page_*.json")):
        try:
            records.append(json.loads(path.read_text()))
        except json.JSONDecodeError as exc:
            print(f"      WARNING: skipping unreadable {path.name} ({exc})", file=sys.stderr)
    records.sort(key=lambda r: r.get("page", 0))
    return records


def records_to_markdown(records: list[dict]) -> str:
    md_parts = []
    for record in records:
        md_parts.append(f"<!-- page {record.get('page')} -->\n\n")
        if record.get("skipped"):
            md_parts.append(f"*[page skipped: {record['skipped']}]*\n\n")
        else:
            md_parts.extend(element_to_markdown(el) + "\n\n" for el in record.get("elements", []))
        md_parts.append("---\n\n")
    return "".join(md_parts).strip() + "\n"


def _decode_full_sequence(tokenizer, token_ids: list[int]) -> str:
    """Independent whole-sequence decode of every generated token, bypassing
    the incremental streaming detokenizer's word-boundary flush entirely.
    Used as a debug cross-check against the (patched) streaming text -- a
    mismatch means the streaming decode's flush heuristic corrupted
    something, even if it no longer crashed.

    Special tokens are skipped, because the streaming detokenizer skips them
    too: without that the model's end token renders here but not there, and
    every single page reports a spurious mismatch."""
    try:
        try:
            debug_text = tokenizer.decode(token_ids, skip_special_tokens=True)
        except TypeError:  # a tokenizer whose decode() has no such kwarg
            debug_text = tokenizer.decode(token_ids)
    except UnicodeDecodeError as exc:
        print(f"      WARNING: full-sequence debug decode raised ({exc})", file=sys.stderr)
        return ""
    # Only ever called from the mlx generation path (see _run_page_mlx).
    replacement_char = backend("mlx").replacement_char
    if replacement_char in debug_text:
        n = debug_text.count(replacement_char)
        print(
            f"      WARNING: full-sequence debug decode introduced {n} U+FFFD "
            f"replacement char(s) -- some generated bytes were not valid UTF-8",
            file=sys.stderr,
        )
    return debug_text


@dataclass
class PageGeneration:
    """Everything one page's generation pass produced.

    text          the streaming detokenizer's output (error-tolerant via the
                  monkeypatch above) -- the string that gets parsed as JSON.
    debug_text    an independent from-scratch decode of the full token
                  sequence, for the streaming cross-check.
    finish_reason "stop" if the model emitted its end token, "length" if it ran
                  into max_tokens with more to say.
    token_logprobs  per generated token, the log-probability the model assigned
                  to the token it chose (None where unavailable).
    segments      (char_start, char_end, tok_lo, tok_hi) per flushed chunk of
                  text, mapping spans of `text` back to the tokens behind them.
    """

    text: str = ""
    debug_text: str = ""
    finish_reason: str | None = None
    token_ids: list[int] = field(default_factory=list)
    token_logprobs: list[float | None] = field(default_factory=list)
    segments: list[tuple[int, int, int, int]] = field(default_factory=list)


def run_page(
    be: "_Backend | _CudaBackend",
    model,
    processor,
    config,
    prompt_text: str,
    image_path: Path,
    max_tokens: int,
    temperature: float,
    want_logprobs: bool = True,
    recorder: GreedyLogprobRecorder | None = None,
) -> PageGeneration:
    """Dispatches to the backend-specific generation path. Everything past this
    point (JSON extraction, logprob attribution, ink coverage, overlays) is
    backend-agnostic and works identically on whichever PageGeneration comes
    back.

    recorder is the cuda path's per-token logprob buffer, reused across every
    page of the run (see GreedyLogprobRecorder); the mlx path gets its logprobs
    from stream_generate and ignores it."""
    if be.kind == "mlx":
        return _run_page_mlx(be, model, processor, config, prompt_text, image_path, max_tokens, temperature, want_logprobs)
    return _run_page_cuda(
        be, model, processor, prompt_text, image_path, max_tokens, temperature, want_logprobs, recorder
    )


def _run_page_mlx(
    be: _Backend,
    model,
    processor,
    config,
    prompt_text: str,
    image_path: Path,
    max_tokens: int,
    temperature: float,
    want_logprobs: bool = True,
) -> PageGeneration:
    formatted_prompt = be.apply_chat_template(processor, config, prompt_text, num_images=1)
    tokenizer = processor.tokenizer if hasattr(processor, "tokenizer") else processor
    tokenizer.stopping_criteria.reset(model.config.eos_token_id)

    gen = PageGeneration()
    attributed = -1  # highest token index already claimed by a segment
    for response in be.stream_generate(
        model=model,
        processor=processor,
        prompt=formatted_prompt,
        image=str(image_path),
        max_tokens=max_tokens,
        temperature=temperature,
        verbose=False,
    ):
        # stream_generate repeats its last token on the terminal chunk (and on a
        # "stop" finish that token is the end token it broke on, which never got
        # yielded in the loop). generation_tokens is the 1-based count of tokens
        # produced so far, so it de-duplicates both cases.
        if response.token is not None and response.generation_tokens > len(gen.token_ids):
            gen.token_ids.append(response.token)
            gen.token_logprobs.append(
                chosen_token_logprob(response.logprobs, response.token) if want_logprobs else None
            )
        if response.text:
            newest = len(gen.token_ids) - 1
            if newest >= 0:
                gen.segments.append(
                    (len(gen.text), len(gen.text) + len(response.text), min(attributed + 1, newest), newest)
                )
                attributed = newest
            gen.text += response.text
        if response.finish_reason:
            gen.finish_reason = response.finish_reason

    gen.debug_text = _decode_full_sequence(tokenizer, gen.token_ids)
    return gen


class GreedyLogprobRecorder:
    """Records log P(chosen token) per decoding step, in O(1) memory.

    The obvious way to get per-token logprobs out of transformers is
    `generate(output_scores=True)`, which retains the full `[1, vocab]` logit
    row for *every* generated token until generation finishes. At dots.ocr's
    151936-token vocabulary, in the float32 that generation casts to, that is
    608 KB per token -- 0.4 GB for a median page here, 1.6 GB for the longest
    one seen, all of it live at once on top of the weights and the KV cache.
    That, not allocator fragmentation, is what was OOMing 8 GB cards mid-page.

    A logits processor sees the same row, one step at a time, and can reduce it
    to the single float we actually keep before it is discarded. Under greedy
    decoding transformers selects `argmax(scores)`, so the logprob of the token
    it is about to emit is exactly the maximum of the row's log-softmax -- no
    need to know which token was chosen, and no off-by-one against the caller's
    token list.

    The reduction is deliberately `log_softmax(row).max()` and not the cheaper
    `row.max() - row.logsumexp(-1)`, which is algebraically identical but sums
    151936 float32 terms in a different order: measured against the code this
    replaces, that shifts values by up to 3e-5, which is enough to move the 4th
    decimal that summarize_logprobs serializes. Going through the same
    log_softmax kernel on the same input makes the recorded numbers bit-identical
    to what output_scores produced, so page JSON does not change. The savings
    were never in avoiding the softmax -- they are in not *retaining* the row --
    and its full-vocabulary temporary is freed each step and recycled by the
    caching allocator rather than accumulating.

    Scalars land in one preallocated buffer that is reused for every page of the
    run (48 KB at --max-tokens 12000, since max_new_tokens hard-bounds the step
    count). Nothing is allocated per page, so there is no per-page lifetime to
    get wrong and nothing that can accumulate across a 400-page volume. Writing
    a 0-dim device tensor into it stays on the GPU: the single host sync happens
    in values(), once per page, instead of once per token.

    NOT valid under sampling (--temperature > 0), where the emitted token is not
    the argmax. _run_page_cuda leaves the recorder unused there and falls back to
    output_scores, paying the memory rather than silently reporting the logprob
    of a token that was never emitted. The pipeline runs greedy (temperature
    0.0, see the module docstring's Determinism note), so that is not the path
    the volumes take.

    Deliberately not a LogitsProcessor subclass: transformers is imported lazily
    (see :func:`backend`), so the base class does not exist at the time this
    module is defined. LogitsProcessorList only requires a callable taking
    (input_ids, scores).
    """

    def __init__(self, torch, max_tokens: int, device) -> None:
        self._buf = torch.empty(max_tokens, dtype=torch.float32, device=device)
        self.n = 0

    def reset(self) -> None:
        """Drop the previous page's values without freeing or reallocating.

        Called before each generate() rather than after, so a page that dies
        part-way through cannot leave its tail behind for the next one.
        """
        self.n = 0

    def __call__(self, input_ids, scores):
        row = scores[0]  # [vocab] view of the batch-of-1 row; no copy
        if self.n < self._buf.numel():
            self._buf[self.n] = row.log_softmax(dim=-1).max()
            self.n += 1
        return scores  # must hand the row back untouched: generate() selects on it

    def values(self) -> list[float]:
        return self._buf[: self.n].cpu().tolist()


def _run_page_cuda(
    be: _CudaBackend,
    model,
    processor,
    prompt_text: str,
    image_path: Path,
    max_tokens: int,
    temperature: float,
    want_logprobs: bool = True,
    recorder: GreedyLogprobRecorder | None = None,
) -> PageGeneration:
    """transformers/CUDA equivalent of _run_page_mlx. One-shot (not truly
    streamed -- model.generate() runs to completion before returning), so
    `segments` is a single span covering every generated token rather than
    per-chunk offsets; exact_token_segments() in main() recomputes precise
    per-token offsets independently and ignores `segments` whenever it
    succeeds, so this costs nothing but live per-token stdout progress, which
    --profile-memory/-verbose don't currently print for the mlx path either."""
    torch = be.torch
    messages = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": prompt_text}]}]
    chat_text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    with Image.open(image_path) as im:
        image = im.convert("RGB")
    inputs = processor(text=[chat_text], images=[image], return_tensors="pt").to(model.device)
    input_len = inputs["input_ids"].shape[1]

    # temperature 0.0 (the pipeline default -- see module docstring's
    # Determinism note) must mean true greedy argmax decoding, never sampling.
    # transformers' generate() raises ValueError for temperature<=0 when
    # do_sample=True, and just warns-and-ignores it when do_sample=False -- so
    # do_sample/num_beams are set explicitly here rather than trusting
    # whatever sampling defaults the checkpoint's own generation_config.json
    # ships with (some repos default to do_sample=True). temperature is only
    # ever passed through when actually sampling.
    greedy = temperature <= 0.0
    gen_kwargs: dict = {
        "max_new_tokens": max_tokens,
        "do_sample": not greedy,
        "num_beams": 1,
        # output_scores would retain a [1, 151936] float32 row per generated
        # token for the whole page; the recorder below reduces each row to one
        # float as it goes instead. See GreedyLogprobRecorder. Only turned on
        # for the sampling path, where the recorder's argmax assumption breaks.
        "output_scores": want_logprobs and not greedy,
        "return_dict_in_generate": True,
    }
    if not greedy:
        gen_kwargs["temperature"] = temperature

    record = recorder if (want_logprobs and greedy) else None
    if record is not None:
        record.reset()
        gen_kwargs["logits_processor"] = be.LogitsProcessorList([record])

    with torch.inference_mode():
        out = model.generate(**inputs, **gen_kwargs)

    new_token_ids = out.sequences[0, input_len:].tolist()

    eos_id = getattr(model.generation_config, "eos_token_id", None)
    eos_ids = set(eos_id) if isinstance(eos_id, (list, tuple)) else ({eos_id} if eos_id is not None else set())
    hit_max = len(new_token_ids) >= max_tokens
    finish_reason = "length" if hit_max and (not new_token_ids or new_token_ids[-1] not in eos_ids) else "stop"

    token_logprobs: list[float | None] = []
    if record is not None:
        # One host sync for the page, not one per token. The recorder is called
        # exactly once per generated token, so this lines up 1:1 with
        # new_token_ids; the length reconciliation below is a backstop only.
        token_logprobs = [lp if math.isfinite(lp) else None for lp in record.values()]
        del token_logprobs[len(new_token_ids) :]
    elif want_logprobs:
        for step_scores, token_id in zip(out.scores or (), new_token_ids):
            lp = step_scores[0].float().log_softmax(dim=-1)[token_id].item()
            token_logprobs.append(lp if math.isfinite(lp) else None)
    if len(token_logprobs) < len(new_token_ids):
        token_logprobs.extend([None] * (len(new_token_ids) - len(token_logprobs)))

    tokenizer = processor.tokenizer if hasattr(processor, "tokenizer") else processor
    text = decode_ids(tokenizer, new_token_ids)

    return PageGeneration(
        text=text,
        debug_text=text,  # one-shot decode already, not incremental -- nothing to cross-check
        finish_reason=finish_reason,
        token_ids=new_token_ids,
        token_logprobs=token_logprobs,
        segments=[(0, len(text), 0, len(new_token_ids) - 1)] if new_token_ids else [],
    )


def _load_cuda_model(be: _CudaBackend, model_id: str, quantization: str, attn_implementation: str | None):
    """Loads the cuda backend's model + processor.

    quantization selects a bitsandbytes load path: "8bit"/"4bit" quantize
    weights on load (this is the CUDA equivalent of picking an mlx-community
    ...-8bit repo -- there's no separate pre-quantized repo id to choose
    instead, unlike the mlx backend). "none" loads full bf16 weights.
    trust_remote_code=True is required by dots.ocr's custom modeling code (see
    the module docstring's trust-boundary note)."""
    torch = be.torch
    quant_config = None
    if quantization in ("8bit", "4bit"):
        quant_config = be.BitsAndBytesConfig(
            load_in_8bit=quantization == "8bit",
            load_in_4bit=quantization == "4bit",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )
    config = be.AutoConfig.from_pretrained(model_id, trust_remote_code=True)
    _set_vision_attn_implementation(config, attn_implementation)
    load_kwargs: dict = {
        "config": config,
        "trust_remote_code": True,
        "torch_dtype": torch.bfloat16,
        "device_map": "auto",
        "quantization_config": quant_config,
    }
    if attn_implementation:
        load_kwargs["attn_implementation"] = attn_implementation
    model = be.AutoModelForCausalLM.from_pretrained(model_id, **load_kwargs)
    model.eval()
    with _qwen_vl_processor_video_shim():
        processor = be.AutoProcessor.from_pretrained(model_id, trust_remote_code=True)
    return model, processor


def _set_vision_attn_implementation(config, attn_implementation: str | None) -> None:
    """Points dots.ocr's vision tower at an attention kernel that fits in VRAM.

    The vision tower doesn't use transformers' `_attn_implementation`: it
    dispatches on its own `vision_config.attn_implementation` field, which the
    checkpoint ships as "flash_attention_2". When flash-attn isn't installed
    its own fallback is *eager*, which materializes a [1, N, N] fp16 score
    matrix over every 14px patch of the page -- ~16 GB for one 200-DPI page,
    i.e. instant OOM on anything short of a datacenter card, and the failure
    is in the vision forward pass long after the model has loaded.

    Its "sdpa" path is the same math through torch's memory-efficient SDPA
    backend (no N x N score matrix), so prefer that whenever flash-attn is
    missing. --attn-implementation, if given, wins for both towers.
    """
    vision_config = getattr(config, "vision_config", None)
    if vision_config is None or not hasattr(vision_config, "attn_implementation"):
        return  # not a dots.ocr-shaped config -- leave whatever it ships with alone
    if attn_implementation:
        vision_config.attn_implementation = attn_implementation
        return
    if vision_config.attn_implementation != "flash_attention_2":
        return
    try:
        import flash_attn  # noqa: F401, PLC0415 - presence check only
    except ImportError:
        vision_config.attn_implementation = "sdpa"


@contextmanager
def _qwen_vl_processor_video_shim():
    """Lets dots.ocr's DotsVLProcessor load on transformers >= 4.57.

    dots.ocr's remote `DotsVLProcessor.__init__` calls
    `Qwen2_5_VLProcessor.__init__(image_processor, tokenizer, chat_template=...)`
    -- the 3-arg signature from when it was written (upstream pins
    transformers==4.56.1). Newer transformers made `video_processor` a
    required processor attribute and hard-rejects the resulting None with
    `TypeError: Received a NoneType for argument video_processor`.

    Nothing here ever passes videos, so fill in a default Qwen2VL video
    processor for the duration of the load only. Scoped to this call rather
    than patched globally so it can't leak into anything else in-process;
    a no-op (and no import cost) on transformers versions that don't need it.
    """
    try:  # noqa: PLC0415 - transformers is an optional (vlm-cuda) dep
        from transformers.models.qwen2_5_vl import Qwen2_5_VLProcessor
        from transformers.models.qwen2_vl.video_processing_qwen2_vl import Qwen2VLVideoProcessor
    except ImportError:  # pragma: no cover - older/newer transformers layout
        yield
        return

    original_init = Qwen2_5_VLProcessor.__init__

    def patched_init(self, image_processor=None, tokenizer=None, video_processor=None, **kwargs):
        if video_processor is None:
            video_processor = Qwen2VLVideoProcessor()
        original_init(self, image_processor, tokenizer, video_processor, **kwargs)

    Qwen2_5_VLProcessor.__init__ = patched_init
    try:
        yield
    finally:
        Qwen2_5_VLProcessor.__init__ = original_init


# --------------------------------------------------------------------------- #
# Reusable OCR API — one model load, any number of PDFs                         #
# --------------------------------------------------------------------------- #
# main() below is a thin shell over these. They exist because the pre-1974 driver
# (scripts/run_volume_ocr.py) shells out once per volume -- fine for 14 volumes,
# catastrophic for the 1,086 post-1974 scans, where 1,086 weight loads would cost
# 9-18 hours before a single page is read. scripts/run_post1974_ocr.py calls
# load_model() once and ocr_pdf() per document instead.
#
# Everything here was MOVED OUT OF main() verbatim, not rewritten. In particular
# ocr_pdf builds each page_record with the same key insertion order it always
# had: json.dumps(indent=2) preserves dict order, so a reshuffle would rewrite
# all 2,936 committed pre-1974 records as a spurious diff.

# Stamped beside blank_override on every page of a document whose blank skips
# were cleared -- see OcrOptions.never_skip_every_page.
BLANK_OVERRIDE_REASON = "every page of this document classified blank"


@dataclass(frozen=True)
class OcrOptions:
    """Every per-run knob main() used to read straight off `args`.

    Defaults are main()'s defaults, so OcrOptions(prompt_text=...) reproduces a
    default CLI run exactly. The two additions are off by default for the same
    reason: a pre-1974 run must not change.
    """

    prompt_text: str
    dpi: int = DEFAULT_DPI
    rotate: str = DEFAULT_ROTATE
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = 0.0
    skip_blank_pages: bool = True
    dark_pixel_threshold: int = DEFAULT_DARK_PIXEL_THRESHOLD
    min_dark_fraction: float = DEFAULT_MIN_DARK_FRACTION
    min_contrast_std: float = DEFAULT_MIN_CONTRAST_STD
    ink_roi_margin: float = DEFAULT_INK_ROI_MARGIN
    token_logprobs: bool = True
    low_logprob_threshold: float = DEFAULT_LOW_LOGPROB
    ink_coverage: bool = True
    min_uncovered_ink: int = DEFAULT_MIN_UNCOVERED_INK
    uncovered_cell_px: int = DEFAULT_UNCOVERED_CELL_PX

    # --- additions, both default-off so pre-1974 output is untouched --------- #

    # Overlay PNGs are the viewer's input and ~1-2 MB a page. A 1,799-page bulk
    # run writes 2-5 GB of them, and N parallel workers contend for that I/O, so
    # the post-1974 driver turns them off and keeps renders only where a page
    # raised a QA flag.
    write_overlays: bool = True

    # A false blank in a 400-page volume loses one page. A false blank on a
    # one-page executive order loses THE ENTIRE ORDER, and the record then reads
    # "_No text available_" -- indistinguishable from a genuine no-PDF gap. 764
    # of the 1,086 post-1974 targets are one page. So: if EVERY page of a
    # document classifies blank, that is evidence against the threshold, not
    # against the document. Clear the skips, OCR it anyway, and say so on every
    # record. Costs at most a few wasted inferences.
    never_skip_every_page: bool = False


@dataclass
class LoadedModel:
    """One resident model, reusable across any number of PDFs.

    `max_tokens` is carried because the cuda backend's GreedyLogprobRecorder is
    SIZED BY IT at load time: a later ocr_pdf asking for more tokens than the
    recorder was built for would silently truncate its logprobs, so ocr_pdf
    checks rather than trusts.
    """

    be: "_Backend | _CudaBackend"
    model: object
    processor: object
    config: object | None
    recorder: "GreedyLogprobRecorder | None"
    model_id: str
    device: str
    quantization: str
    max_tokens: int
    baseline_gb: float


@dataclass
class PdfOcrResult:
    """What one document's OCR pass produced. Counters, not text."""

    pdf_path: Path
    json_dir: Path
    pages_rendered: int = 0
    pages_ocred: int = 0
    pages_skipped_blank: int = 0
    pages_resumed: int = 0
    parse_errors: int = 0
    truncated: int = 0
    low_coverage: int = 0
    no_measurable_ink: int = 0
    blank_override: bool = False
    seconds: float = 0.0

    @property
    def flagged(self) -> bool:
        """Did any page fail loudly? This is what --keep-renders flagged keeps."""
        return bool(
            self.parse_errors or self.truncated or self.low_coverage
            or self.no_measurable_ink or self.blank_override
        )

    def observe(self, page_record: dict) -> None:
        """Tally one finished page record."""
        if page_record.get("skipped"):
            self.pages_skipped_blank += 1
            return
        self.pages_ocred += 1
        if page_record.get("parse_error"):
            self.parse_errors += 1
        if page_record.get("finish_reason") == "length":
            self.truncated += 1
        coverage = page_record.get("ink_coverage") or {}
        covered = coverage.get("covered_fraction")
        if covered is not None and covered < MIN_COVERED_FRACTION:
            self.low_coverage += 1
        if (page_record.get("page_stats") or {}).get("dark_fraction") == 0:
            self.no_measurable_ink += 1


def load_model(
    device: str,
    model_id: str,
    quantization: str,
    attn_implementation: str | None,
    max_tokens: int,
    profiler: "MemoryProfiler | None" = None,
    *,
    log=print,
) -> LoadedModel:
    """Load the weights once. Lifted from main()'s [2/3] stage verbatim.

    The caller is expected to have resolved `device` through resolve_device() and
    checked backend availability first: a missing extra should fail in a second,
    not after half an hour of rendering.
    """
    profiler = profiler or MemoryProfiler(False, mem=None)
    device_note = f" ({quantization})" if device == "cuda" else ""
    log(f"[2/3] loading {model_id}{device_note} on {device} (first run downloads weights, be patient) ...")
    t0 = time.time()
    be = backend(device)
    recorder = None
    with profiler.stage("model-load"):
        if be.kind == "mlx":
            model, processor = be.load(model_id)
            config = be.load_config(model_id)
            # Weights load lazily; force evaluation so the stage's peak reflects
            # the real resident cost of the model rather than unmaterialized arrays.
            be.mx.eval(model.parameters())
        else:
            model, processor = _load_cuda_model(be, model_id, quantization, attn_implementation)
            config = None
            be.torch.cuda.synchronize()
            # Allocated once here and reused by every page, so the steady-state
            # baseline below covers it (see GreedyLogprobRecorder).
            recorder = GreedyLogprobRecorder(be.torch, max_tokens, model.device)
    log(f"      model loaded in {time.time() - t0:.1f}s")

    # Everything the run needs on the GPU is now resident, and each page should
    # return to exactly this figure. Anything that does not is a leak, and the
    # point of checking per page is to see it on page 3 rather than infer it
    # from an OOM on page 122 (which is how the retained score rows were found).
    mem = mem_sampler_for(be) if be.kind == "cuda" else None
    baseline_gb = mem.active_gb() if mem else 0.0

    return LoadedModel(
        be=be,
        model=model,
        processor=processor,
        config=config,
        recorder=recorder,
        model_id=model_id,
        device=device,
        quantization=quantization,
        max_tokens=max_tokens,
        baseline_gb=baseline_gb,
    )


def classify_pages(
    rendered: list,
    opts: OcrOptions,
    json_dir: Path,
    start_page: int = 1,
    *,
    log=print,
) -> dict:
    """Score every rendered page blank/keep and write classify_report.json.

    No model is loaded. This is the calibration path: run it on a body of scans
    you have not OCR'd before, because a false skip silently drops real content.
    Merges into an existing report rather than overwriting it, mirroring how the
    real-OCR path extends an output directory in place.
    """
    page_paths = [path for path, _ in rendered]
    log(f"\n[classify-blank-only] scoring {len(rendered)} page(s) -- no model will be loaded")
    n_skip = 0
    pages_by_number = {}
    # Beside the page records, not in the scratch dir: it is a durable finding
    # about the volume (which pages were judged blank, under which thresholds)
    # and readers of the page JSON expect it in the same directory.
    report_path = json_dir / "classify_report.json"
    if report_path.exists():
        # A prior classify-only pass (e.g. before a --start-page resume) already
        # scored some pages -- merge rather than overwrite, same spirit as how
        # the real-OCR path extends an --output-dir in place.
        try:
            existing = json.loads(report_path.read_text())
            for p in existing.get("pages", []):
                pages_by_number[p["page"]] = p
        except (json.JSONDecodeError, KeyError):
            pass
    for n, (page_path, rotation) in enumerate(rendered, start=1):
        i = start_page + n - 1
        stats = page_ink_stats(load_gray(page_path), opts.dark_pixel_threshold, opts.ink_roi_margin)
        blank = is_blank_or_bleedthrough(stats, opts.min_dark_fraction, opts.min_contrast_std)
        n_skip += blank
        verdict = "SKIP (blank/bleed-through)" if blank else "KEEP"
        log(
            f"  page {i:04d}: {verdict:24s} "
            f"dark_fraction={stats['dark_fraction']:.5f} std={stats['std']:6.2f} mean={stats['mean']:6.2f}"
        )
        pages_by_number[i] = {
            "page": i,
            "blank": blank,
            "page_stats": stats,
            "rotation": rotation,
        }
    log(f"\n{n_skip}/{len(rendered)} page(s) would be skipped as blank/bleed-through.")

    report = {
        "model_classifier": "ink-stats",
        "params": {
            "dark_pixel_threshold": opts.dark_pixel_threshold,
            "ink_roi_margin": opts.ink_roi_margin,
            "min_dark_fraction": opts.min_dark_fraction,
            "min_contrast_std": opts.min_contrast_std,
        },
        "pages": [pages_by_number[n] for n in sorted(pages_by_number)],
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    log(f"      wrote {report_path}")
    return report


def render_for_run(
    pdf_path: Path,
    raw_dir: Path,
    opts: "OcrOptions",
    pages: int | None,
    start_page: int,
    profiler: "MemoryProfiler | None" = None,
    *,
    log=print,
) -> list:
    """Render a PDF's pages upright and announce what happened. main()'s [1/3].

    Shared by the OCR path and the classify-only path so both report rendering
    and rotation identically -- rotation in particular silently changes what
    every bbox in the run is relative to, and on --rotate auto nothing else
    announces it.
    """
    profiler = profiler or MemoryProfiler(False, mem=None)
    # The public API owns its output directories: main() makes them too, so this
    # is a no-op there, but a library caller must not have to know.
    Path(raw_dir).mkdir(parents=True, exist_ok=True)
    log(
        f"[1/3] rendering pages from {Path(pdf_path).name} at {opts.dpi} DPI, "
        f"starting at page {start_page} ..."
    )
    with profiler.stage("render"):
        rendered = render_pdf_pages(pdf_path, raw_dir, opts.dpi, pages, start_page, opts.rotate)
    if not rendered:
        return rendered
    page_paths = [path for path, _ in rendered]
    rotations = [rot for _, rot in rendered]
    log(f"      {len(page_paths)} page(s) rendered -> {raw_dir}")
    n_rotated = sum(1 for rot in rotations if rot["applied_cw"])
    if n_rotated:
        # Worth stating plainly: it silently changes what every bbox in this run
        # is relative to, and on --rotate auto nothing else announces it.
        angles = sorted({rot["applied_cw"] for rot in rotations if rot["applied_cw"]})
        log(
            f"      rotated {n_rotated}/{len(page_paths)} page(s) by "
            f"{'/'.join(f'{a}' for a in angles)} degrees clockwise ({rotations[0]['source']}) "
            f"so their text is upright"
        )
    return rendered


def ocr_pdf(
    pdf_path: Path,
    *,
    loaded: LoadedModel,
    opts: OcrOptions,
    raw_dir: Path,
    json_dir: Path,
    overlay_dir: Path | None = None,
    start_page: int = 1,
    pages: int | None = None,
    skip_existing: bool = False,
    profiler: "MemoryProfiler | None" = None,
    log=print,
) -> PdfOcrResult:
    """Render one PDF and run layout+OCR over its pages, reusing a loaded model.

    This is main()'s [1/3] + [3/3] stages. Page numbering is ABSOLUTE, so a run
    that starts partway through extends an existing json_dir rather than
    renumbering it.

    `skip_existing` is the resume knob the post-1974 driver uses: a page whose
    page_XXXX.json is already on disk is left alone and counted as resumed. It is
    per PAGE, not per document, so a document killed on page 3 of 8 resumes at 3.
    The pre-1974 driver keeps using --start-page instead, which is why this
    defaults False.

    `overlay_dir` may be None (with opts.write_overlays False) for a bulk run.
    """
    profiler = profiler or MemoryProfiler(False, mem=None)
    Path(json_dir).mkdir(parents=True, exist_ok=True)
    if overlay_dir is not None and opts.write_overlays:
        Path(overlay_dir).mkdir(parents=True, exist_ok=True)
    counts = PdfOcrResult(pdf_path=Path(pdf_path), json_dir=json_dir)
    t_doc = time.time()

    rendered = render_for_run(pdf_path, raw_dir, opts, pages, start_page, profiler, log=log)
    if not rendered:
        return counts
    counts.pages_rendered = len(rendered)
    n_total = len(rendered)

    # The recorder is sized at load time; asking for more now would truncate
    # logprobs without saying so.
    if loaded.recorder is not None and opts.max_tokens > loaded.max_tokens:
        raise ValueError(
            f"opts.max_tokens={opts.max_tokens} exceeds the {loaded.max_tokens} the "
            f"logprob recorder was built for; reload the model with the larger value"
        )

    # --- the whole-document blank guard ------------------------------------- #
    skip_blank_pages = opts.skip_blank_pages
    blank_override = False
    if skip_blank_pages and opts.never_skip_every_page:
        all_blank = all(
            is_blank_or_bleedthrough(
                page_ink_stats(load_gray(path), opts.dark_pixel_threshold, opts.ink_roi_margin),
                opts.min_dark_fraction,
                opts.min_contrast_std,
            )
            for path, _ in rendered
        )
        if all_blank:
            skip_blank_pages = False
            blank_override = True
            counts.blank_override = True
            log(
                f"      WARNING every page of {Path(pdf_path).name} classified blank; "
                f"OCR'ing anyway ({BLANK_OVERRIDE_REASON})"
            )

    mem = mem_sampler_for(loaded.be) if loaded.be.kind == "cuda" else None

    for n, (page_path, rotation) in enumerate(rendered, start=1):
        if skip_existing and (json_dir / f"page_{start_page + n - 1:04d}.json").exists():
            counts.pages_resumed += 1
            continue
        i = start_page + n - 1  # absolute page number in the PDF
        t0 = time.time()
        with profiler.stage(f"page {i:04d}"):
            gray = load_gray(page_path)
            orig_h, orig_w = gray.shape
            resized_h, resized_w = smart_resize(orig_h, orig_w)

            stats = page_ink_stats(gray, opts.dark_pixel_threshold, opts.ink_roi_margin)
            skip_page = skip_blank_pages and is_blank_or_bleedthrough(
                stats, opts.min_dark_fraction, opts.min_contrast_std
            )
            coverage = None

            if skip_page:
                log(
                    f"      page {i}: SKIPPED (looks blank/bleed-through -- "
                    f"dark_fraction={stats['dark_fraction']:.5f}, std={stats['std']:.1f})"
                )
                elements = []
                page_record = {
                    "page": i,
                    "source_image": page_path.name,
                    "elements": [],
                    "skipped": "blank_or_bleedthrough",
                    "page_stats": stats,
                }
            else:
                gen = run_page(
                    loaded.be,
                    loaded.model,
                    loaded.processor,
                    loaded.config,
                    opts.prompt_text,
                    page_path,
                    opts.max_tokens,
                    opts.temperature,
                    want_logprobs=opts.token_logprobs,
                    recorder=loaded.recorder,
                )

                try:
                    elements = normalize_elements(extract_json(gen.text))
                    page_record = {"page": i, "source_image": page_path.name, "elements": elements}
                except ValueError as exc:
                    log(f"      page {i}: WARNING could not parse JSON ({exc}); saving raw text instead")
                    elements = []
                    page_record = {
                        "page": i,
                        "source_image": page_path.name,
                        "elements": [],
                        "parse_error": str(exc),
                        "raw_text": gen.text,
                    }

                # Cross-check the streaming decode against a from-scratch decode
                # of the full token sequence; only store the debug text (instead
                # of just a bool) when they actually disagree, to keep normal
                # pages' JSON small.
                debug_matches = gen.debug_text == gen.text
                page_record["debug_decode_matches_stream"] = debug_matches
                if not debug_matches:
                    page_record["debug_decode"] = gen.debug_text
                    log(f"      page {i}: WARNING streaming decode diverged from full debug decode")

                # Truncation is worth shouting about: unlike a parse failure it
                # can leave JSON that loads cleanly and is simply missing the
                # tail of the page.
                page_record["finish_reason"] = gen.finish_reason
                if gen.finish_reason == "length":
                    log(
                        f"      page {i}: WARNING hit --max-tokens ({opts.max_tokens}); "
                        f"this page's output is truncated"
                    )

                if opts.token_logprobs:
                    tokenizer = (
                        loaded.processor.tokenizer
                        if hasattr(loaded.processor, "tokenizer")
                        else loaded.processor
                    )
                    exact = exact_token_segments(tokenizer, gen.token_ids, len(gen.text))
                    if exact is None:
                        log(
                            f"      page {i}: WARNING per-token offsets did not reconstruct the "
                            f"streamed text; falling back to coarser chunk attribution"
                        )
                    # Attribution first: it identifies which tokens carry text
                    # and which carry layout, which is what scopes the summary.
                    scopes = attribute_element_logprobs(
                        elements, gen.text, exact or gen.segments, gen.token_logprobs
                    )
                    page_record["logprobs"] = summarize_logprobs(
                        gen.token_logprobs,
                        gen.token_ids,
                        tokenizer,
                        opts.low_logprob_threshold,
                        scopes,
                    )
                    if page_record["logprobs"]:
                        page_record["logprobs"]["attribution"] = "exact" if exact else "chunk"

                page_record["page_stats"] = stats

            # Set once here rather than in each of the three page_record
            # constructions above, so a skipped or unparseable page carries it
            # too -- those are exactly the pages a rotation bug shows up on.
            page_record["rotation"] = rotation

            if opts.ink_coverage and not skip_page:
                coverage = ink_coverage(
                    gray,
                    elements,
                    resized_w,
                    resized_h,
                    opts.dark_pixel_threshold,
                    opts.ink_roi_margin,
                    opts.uncovered_cell_px,
                    opts.min_uncovered_ink,
                )
                page_record["ink_coverage"] = coverage
                if coverage["uncovered_regions"]:
                    log(
                        f"      page {i}: WARNING {len(coverage['uncovered_regions'])} region(s) of ink "
                        f"outside every bbox ({coverage['covered_fraction']:.1%} of ink covered)"
                    )

            # Appended last so a run without the guard writes byte-identical
            # records: the pre-1974 volumes must not gain a key.
            if blank_override:
                page_record["blank_override"] = True
                page_record["blank_override_reason"] = BLANK_OVERRIDE_REASON

            counts.observe(page_record)
            (json_dir / f"page_{i:04d}.json").write_text(json.dumps(page_record, indent=2, ensure_ascii=False))

            if opts.write_overlays and overlay_dir is not None:
                draw_overlay(
                    page_path,
                    elements,
                    resized_w,
                    resized_h,
                    overlay_dir / f"page_{i:04d}.png",
                    coverage["uncovered_regions"] if coverage else (),
                )

            status = " [skipped]" if page_record.get("skipped") else ""
            log(
                f"      page {i} ({n}/{n_total}): {len(elements)} elements,"
                f" {time.time() - t0:.1f}s{status}"
            )
        # WARNING so it clears run_full_vlm_pipeline.sh's digest filter and
        # reaches the terminal, not just the volume log. The threshold is well
        # above allocator noise -- steady state should be flat to the megabyte.
        if mem is not None and mem.active_gb() > loaded.baseline_gb + MEMORY_DRIFT_WARN_GB:
            log(
                f"      page {i}: WARNING {mem.active_gb() - loaded.baseline_gb:.2f} GB still allocated "
                f"after this page above the {loaded.baseline_gb:.2f} GB post-load baseline "
                f"(expected ~0.00 -- something is being retained across pages)"
            )
    counts.seconds = time.time() - t_doc
    return counts


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pdf_path", type=Path, help="Path to the input PDF")
    parser.add_argument(
        "--pages", type=int, default=None, help="Number of pages to process (default: all remaining)"
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="1-based page of the PDF to start at (default: 1). Point this plus --output-dir at a "
        "previous run's directory to add more pages to it: page numbering is absolute, so new "
        "pages slot in alongside the old ones",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=None, help="Output directory (default: ./vlm-ocr-runs/<pdf-stem>)"
    )
    parser.add_argument(
        "--json-dir",
        type=Path,
        default=None,
        help="Where the durable per-page records go: page_XXXX.json plus classify_report.json "
        "(default: --output-dir/json). Point it at a committed directory to have this run write "
        "its results straight there instead of leaving a copy to be published afterwards -- what "
        "scripts/run_volume_ocr.py does. Everything else (rendered pages, overlays, memory "
        "profile) stays under --output-dir, which is scratch.",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "mlx", "cuda"],
        default="auto",
        help="Compute backend to run dots.ocr on (default: auto -- mlx on Apple Silicon if the "
        "'vlm' extra is importable, otherwise cuda). See the module docstring for the trust-boundary "
        "difference between the two.",
    )
    parser.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help=f"Model repo id. Default depends on --device: {DEFAULT_MODEL} (mlx, one of "
        f"{', '.join(MLX_MODEL_CHOICES)}) / {DEFAULT_MODEL_CUDA} (cuda, any transformers-loadable repo)",
    )
    parser.add_argument(
        "--quantization",
        choices=["none", "8bit", "4bit"],
        default=None,
        help="bitsandbytes weight quantization, cuda backend only (default: 8bit on cuda; mlx models "
        "are already pre-quantized via --model and reject any value here other than the implied 'none')",
    )
    parser.add_argument(
        "--attn-implementation",
        default=None,
        help="Forwarded to transformers' from_pretrained() on the cuda backend (e.g. sdpa, "
        "flash_attention_2). Default: let transformers pick.",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        default=DEFAULT_PROMPT_PATH,
        help=f"Prompt text file (default: {DEFAULT_PROMPT_PATH.name} shipped in the package)",
    )
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help=f"Page render DPI (default: {DEFAULT_DPI})")
    parser.add_argument(
        "--rotate",
        choices=ROTATE_CHOICES,
        default=DEFAULT_ROTATE,
        help="Degrees clockwise to rotate each page at render time so its text is "
        "upright. 'auto' (default) turns landscape-shaped pages 90 -- see "
        "detect_page_rotation. Give an explicit angle to force every page of the run.",
    )
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--profile-memory",
        action="store_true",
        help="Report unified-memory + RSS high-water marks per stage and write memory_profile.json",
    )
    parser.add_argument(
        "--skip-blank-pages",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip OCR on pages that look blank or are only bleed-through ghosting from the "
        "facing page, based on dark-pixel fraction + contrast (default: on)",
    )
    parser.add_argument(
        "--dark-pixel-threshold",
        type=int,
        default=DEFAULT_DARK_PIXEL_THRESHOLD,
        help="Grayscale value (0-255) below which a pixel counts as real ink, not paper/ghosting "
        f"(default: {DEFAULT_DARK_PIXEL_THRESHOLD})",
    )
    parser.add_argument(
        "--min-dark-fraction",
        type=float,
        default=DEFAULT_MIN_DARK_FRACTION,
        help="Minimum fraction of ink-dark pixels required to treat a page as real content; "
        f"below this AND --min-contrast-std, the page is skipped (default: {DEFAULT_MIN_DARK_FRACTION})",
    )
    parser.add_argument(
        "--min-contrast-std",
        type=float,
        default=DEFAULT_MIN_CONTRAST_STD,
        help="Minimum grayscale standard deviation required to treat a page as real content "
        f"(default: {DEFAULT_MIN_CONTRAST_STD})",
    )
    parser.add_argument(
        "--ink-roi-margin",
        type=float,
        default=DEFAULT_INK_ROI_MARGIN,
        help="Fraction of page width/height to crop from each edge before computing ink stats, "
        "to exclude the book binding + scanner-bed background from the signal "
        f"(default: {DEFAULT_INK_ROI_MARGIN})",
    )
    parser.add_argument(
        "--token-logprobs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Record the decoder's log-probability for each token it chose, summarised per page "
        "and attributed to each element, as a stand-in for the confidence score the model does "
        "not emit (default: on)",
    )
    parser.add_argument(
        "--low-logprob-threshold",
        type=float,
        default=DEFAULT_LOW_LOGPROB,
        help="Token log-probability below which a token is counted as low-confidence in the "
        f"per-page summary (default: {DEFAULT_LOW_LOGPROB})",
    )
    parser.add_argument(
        "--ink-coverage",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reconcile the page's ink against the returned bboxes, to catch content the model "
        "dropped silently -- the failure mode that leaves valid-looking JSON (default: on)",
    )
    parser.add_argument(
        "--min-uncovered-ink",
        type=int,
        default=DEFAULT_MIN_UNCOVERED_INK,
        help="Ink pixels a region of uncovered ink must hold before it is reported and drawn on "
        f"the overlay (default: {DEFAULT_MIN_UNCOVERED_INK})",
    )
    parser.add_argument(
        "--uncovered-cell-px",
        type=int,
        default=DEFAULT_UNCOVERED_CELL_PX,
        help="Grid cell size used to group uncovered ink into regions "
        f"(default: {DEFAULT_UNCOVERED_CELL_PX})",
    )
    parser.add_argument(
        "--classify-blank-only",
        action="store_true",
        help="Render pages, print each page's blank/bleed-through classification to stdout, write "
        "it to --json-dir/classify_report.json, and exit -- no model is loaded and no OCR is "
        "run. Use this to calibrate --dark-pixel-threshold / --min-dark-fraction / "
        "--min-contrast-std / --ink-roi-margin against a new volume before committing to a full run.",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()

    if not args.pdf_path.exists():
        sys.exit(f"error: no such file: {args.pdf_path}")

    if args.start_page < 1:
        sys.exit(f"error: --start-page is 1-based, got {args.start_page}")

    device = resolve_device(args.device)
    model_id = args.model or (DEFAULT_MODEL if device == "mlx" else DEFAULT_MODEL_CUDA)
    if device == "mlx" and model_id not in MLX_MODEL_CHOICES:
        sys.exit(
            f"error: --model {model_id!r} is not a known mlx quantization; choose one of: "
            f"{', '.join(MLX_MODEL_CHOICES)}"
        )
    quantization = args.quantization
    if quantization is None:
        quantization = "8bit" if device == "cuda" else "none"
    elif device == "mlx" and quantization != "none":
        sys.exit("error: --quantization only applies to --device cuda (mlx models are "
                  "already pre-quantized via --model)")

    prompt_text = ""
    if not args.classify_blank_only:
        prompt_text = args.prompt_file.read_text().strip()
        if not prompt_text:
            sys.exit(f"error: prompt file is empty: {args.prompt_file}")
        # Resolve the backend BEFORE rendering: a missing extra should fail in a
        # second, not after half an hour of rendering a 400-page volume.
        try:
            backend(device)
        except VlmBackendUnavailable as exc:
            sys.exit(f"error: {exc}")

    opts = OcrOptions(
        prompt_text=prompt_text,
        dpi=args.dpi,
        rotate=args.rotate,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        skip_blank_pages=args.skip_blank_pages,
        dark_pixel_threshold=args.dark_pixel_threshold,
        min_dark_fraction=args.min_dark_fraction,
        min_contrast_std=args.min_contrast_std,
        ink_roi_margin=args.ink_roi_margin,
        token_logprobs=args.token_logprobs,
        low_logprob_threshold=args.low_logprob_threshold,
        ink_coverage=args.ink_coverage,
        min_uncovered_ink=args.min_uncovered_ink,
        uncovered_cell_px=args.uncovered_cell_px,
    )

    # Relative to the CWD, not the package — runs are scratch output (rendered
    # PNGs, ~600MB a volume) and must never land inside site-packages.
    out_dir = args.output_dir or Path("vlm-ocr-runs") / args.pdf_path.stem
    raw_dir, overlay_dir = out_dir / "raw", out_dir / "overlays"
    # The one output that is a durable record rather than scratch, so it is the
    # one that can be pointed elsewhere -- at a committed directory, written
    # once, instead of written here and copied there afterwards.
    json_dir = args.json_dir or out_dir / "json"
    for d in (raw_dir, overlay_dir, json_dir):
        d.mkdir(parents=True, exist_ok=True)

    # --classify-blank-only never loads the model, so it never imports a backend.
    profiler = MemoryProfiler(
        args.profile_memory,
        mem=None if args.classify_blank_only else mem_sampler_for(backend(device)),
    )

    if args.classify_blank_only:
        rendered = render_for_run(
            args.pdf_path, raw_dir, opts, args.pages, args.start_page, profiler
        )
        if not rendered:
            sys.exit(
                f"error: no pages to process (is --start-page {args.start_page} past "
                f"the end of the PDF?)"
            )
        classify_pages(rendered, opts, json_dir, args.start_page)
        profiler.report(out_dir / "memory_profile.json")
        return

    result = ocr_pdf(
        args.pdf_path,
        loaded=load_model(
            device, model_id, quantization, args.attn_implementation,
            opts.max_tokens, profiler,
        ),
        opts=opts,
        raw_dir=raw_dir,
        json_dir=json_dir,
        overlay_dir=overlay_dir,
        start_page=args.start_page,
        pages=args.pages,
        profiler=profiler,
    )
    if not result.pages_rendered:
        sys.exit(
            f"error: no pages to process (is --start-page {args.start_page} past "
            f"the end of the PDF?)"
        )

    # No combined raw_output.json / document.md: both were pure functions of the
    # page records beside them, rebuilt from disk on every run, and a stored copy
    # of derived data is a copy that can go stale or be read as a second source
    # of truth. scripts/render_volume_document.py builds either one on demand.
    n_pages = len(list(json_dir.glob("page_*.json")))

    print(f"\ndone. {n_pages} page(s) recorded.")
    print(f"  per-page:  {json_dir}/page_XXXX.json")
    print(f"  overlays:  {overlay_dir}/page_XXXX.png")
    print(f"  markdown:  python scripts/render_volume_document.py --json-dir {json_dir}")
    if args.profile_memory:
        print(f"  memory:    {out_dir / 'memory_profile.json'}")

    profiler.report(out_dir / "memory_profile.json")


if __name__ == "__main__":
    main()
