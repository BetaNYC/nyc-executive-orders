"""Post-1974 VLM OCR: pick the documents, then read back what OCR wrote.

Two halves of one contract, in one module on purpose.

**Selection** — :func:`select_candidates` decides which of the 2,291 post-1974
PDFs the VLM should transcribe. It re-probes each file with
:func:`textlayer.classify_pdf` rather than trusting the ``text_source`` already
in the corpus. That costs milliseconds and buys three things: a document that was
stubbed out by an earlier ``--no-ocr`` run needs no special case (it is simply a
scanned PDF); stage 1 and stage 2 cannot drift apart about the population,
because :func:`build_corpus.parse_record` branches on the same probe; and a
mislabelled record cannot hide from the worklist behind its own tag.

That last one is not hypothetical. Until 2026-09 the probe read a single number
— characters per page — and 665 records reached the corpus tagged
``born-digital`` while being scans with a second-hand OCR layer. They were
excluded from this worklist for exactly that reason, and re-probing did not
save them, because the probe itself was what was wrong. The probe now reads the
text render mode as well and returns :data:`textlayer.CLASS_OCR_LAYER` for them,
which is selected here: a scan is a scan whether or not somebody already ran OCR
over it.

**Loading** — :func:`load_vlm_document` reads a document's committed page records
back and renders them through :mod:`vlm_pages` into the body a corpus record
publishes, plus the QA flags that force ``needs-review``.

Layout mirrors the PDFs one-for-one::

    pdfs/2023/2023-EEO-302.pdf  ->  sources/ocr/2023/2023-EEO-302/page_0001.json

Under ``sources/ocr/`` rather than ``sources/gpp/`` because these scans come from
the primary nyc.gov lineage, not the DORIS GPP deposit; putting them beside the
GPP volumes would misattribute their provenance.

No network. No model. Pure local reads.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import textlayer
from .vlm_pages import DocumentText, document_text

logger = logging.getLogger("nyc_executive_orders.vlm_corpus")

# Root of the committed per-document page records. Relative to the repo root.
DEFAULT_VLM_OCR_ROOT = Path("sources") / "ocr"

# Selection outcomes. Only SCANNED is work; the rest are reported, never silently
# dropped — an unreadable PDF is a harvest bug and has to be visible as one.
STATUS_SCANNED = "scanned"            # image-only: the VLM's job
STATUS_BORN_DIGITAL = "born-digital"  # has a real text layer; never re-OCR it
STATUS_NO_PDF = "no-pdf"              # no pdf_path, or the file is not on disk
STATUS_UNREADABLE = "unreadable"      # PyMuPDF cannot open it at all


# --------------------------------------------------------------------------- #
# Selection                                                                     #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Candidate:
    """One post-1974 record, probed and judged."""

    eo_id: str
    year: int
    pdf_path: Path | None
    status: str
    page_count: int = 0
    text_source: str | None = None   # what the corpus says TODAY, for reporting
    error: str | None = None

    @property
    def selected(self) -> bool:
        return self.status == STATUS_SCANNED

    def as_dict(self) -> dict:
        return {
            "eo_id": self.eo_id,
            "year": self.year,
            "pdf_path": str(self.pdf_path) if self.pdf_path else None,
            "status": self.status,
            "page_count": self.page_count,
            "text_source": self.text_source,
            "error": self.error,
        }


def resolve_pdf_path(record: dict, repo_root: Path) -> Path | None:
    """Absolute path to a record's PDF, or None if it has no ``pdf_path``.

    ``pdf_path`` is stored relative to the repo root (e.g. ``pdfs/2022/x.pdf``),
    the same convention :func:`build_corpus._resolve_pdf_path` reads.
    """
    rel = record.get("pdf_path")
    if not rel:
        return None
    p = Path(rel)
    return p if p.is_absolute() else (repo_root / p)


def probe_record(record: dict, repo_root: Path) -> Candidate:
    """Classify one corpus record for the OCR worklist. One PDF read."""
    eo_id = record["eo_id"]
    year = int(record["year"])
    text_source = record.get("text_source")
    pdf_path = resolve_pdf_path(record, repo_root)

    if pdf_path is None or not pdf_path.exists():
        return Candidate(eo_id, year, pdf_path, STATUS_NO_PDF, text_source=text_source)

    probe = textlayer.classify_pdf(pdf_path)
    if probe.classification == textlayer.CLASS_ERROR:
        # PyMuPDF cannot open it, or it has zero pages. render_pdf_pages calls the
        # same fitz.open, so attempting OCR would fail identically. This is a
        # harvest bug (cf. the truncated 2021-EEO-250.pdf), not an OCR one.
        status = STATUS_UNREADABLE
    elif probe.needs_ocr:
        # THE selection rule, defined once on TextLayerResult so this module and
        # build_corpus.parse_record cannot disagree about the population: an
        # image-only PDF, a scan carrying somebody else's OCR, or a born-digital
        # document that holds a scanned page.
        status = STATUS_SCANNED
    else:
        status = STATUS_BORN_DIGITAL

    return Candidate(
        eo_id=eo_id,
        year=year,
        pdf_path=pdf_path,
        status=status,
        page_count=probe.page_count,
        text_source=text_source,
        error=probe.error,
    )


def select_candidates(
    records: Iterable[dict],
    *,
    repo_root: Path,
    years: set[int] | None = None,
    eo_ids: set[str] | None = None,
    limit: int | None = None,
) -> list[Candidate]:
    """Probe every record and return the candidates in ``(year, eo_id)`` order.

    Every record is returned, selected or not, so a caller can report what it
    skipped and why. ``limit`` caps the SELECTED documents, not the probed ones.

    Ordering is deterministic and never shuffled: a resumed run has to visit
    documents in the same order as the run it is resuming.
    """
    out: list[Candidate] = []
    n_selected = 0
    for record in sorted(records, key=lambda r: (int(r["year"]), r["eo_id"])):
        if years is not None and int(record["year"]) not in years:
            continue
        if eo_ids is not None and record["eo_id"] not in eo_ids:
            continue
        if limit is not None and n_selected >= limit:
            break
        candidate = probe_record(record, repo_root)
        if candidate.selected:
            n_selected += 1
        out.append(candidate)
    return out


def bin_pack(candidates: list[Candidate], n_shards: int) -> list[list[Candidate]]:
    """Split documents across workers so every shard holds ~the same page count.

    Greedy longest-first: sort by page count descending, then hand each document
    to whichever shard is currently lightest. Measured against the real
    population (1,086 documents, 1,799 pages, longest 21 pages) this lands within
    0.1% of a perfect split at 4, 6, 8 and 12 workers — which is why the driver
    needs no work queue, no claim protocol and no inter-process coordination at
    all. Each shard is then walked in ``(year, eo_id)`` order so a worker's own
    log reads chronologically.
    """
    if n_shards < 1:
        raise ValueError(f"n_shards must be >= 1, got {n_shards}")
    shards: list[list[Candidate]] = [[] for _ in range(n_shards)]
    loads = [0] * n_shards
    for candidate in sorted(candidates, key=lambda c: (-c.page_count, c.year, c.eo_id)):
        i = loads.index(min(loads))
        shards[i].append(candidate)
        loads[i] += max(candidate.page_count, 1)
    return [sorted(s, key=lambda c: (c.year, c.eo_id)) for s in shards]


# --------------------------------------------------------------------------- #
# Loading back what OCR wrote                                                   #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class VlmDocument:
    """One document's committed page records, rendered into a corpus body."""

    eo_id: str
    year: int
    ocr_dir: Path
    doc: DocumentText
    records: list[dict] = field(default_factory=list)

    @property
    def records_present(self) -> bool:
        """Did stage 1 write anything here at all?

        The distinction that matters downstream: no records means "not OCR'd
        yet" (emit ocr-skipped, or fall back to Tesseract under --ocr-engine
        auto), while records that yielded no text is a real, recorded failure
        (emit ocr-vlm-failed). Conflating them would let a genuine failure look
        like work that had simply not happened.
        """
        return bool(self.records)

    @property
    def has_text(self) -> bool:
        return self.doc.has_text

    @property
    def text(self) -> str:
        return self.doc.text

    @property
    def flags(self) -> list[str]:
        return self.doc.flags

    @property
    def page_count(self) -> int:
        return self.doc.page_count


def doc_ocr_dir(ocr_root: str | Path, year: int, eo_id: str) -> Path:
    """Where one document's page records live."""
    return Path(ocr_root) / str(year) / eo_id


def load_page_records(ocr_dir: str | Path) -> list[dict]:
    """Every ``page_XXXX.json`` in a directory, in page order.

    An unreadable record is skipped with a warning rather than raising: a run
    killed mid-write leaves at most one truncated file, and losing the whole
    document over it would be worse than losing that page. The page is missing
    from the body, and the ink-coverage / flag machinery has nothing to say about
    a page it never saw — which is why the driver re-runs partial documents.
    """
    directory = Path(ocr_dir)
    if not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("page_*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("vlm_corpus: skipping unreadable %s (%s)", path, exc)
    records.sort(key=lambda r: r.get("page", 0))
    return records


def load_vlm_document(
    ocr_root: str | Path,
    year: int,
    eo_id: str,
    **document_text_kwargs,
) -> VlmDocument | None:
    """Read one document's records and render its corpus body.

    Returns ``None`` when the directory does not exist at all — "stage 1 has not
    reached this document", which a caller must not confuse with "stage 1 tried
    and got nothing".
    """
    ocr_dir = doc_ocr_dir(ocr_root, year, eo_id)
    if not ocr_dir.is_dir():
        return None
    records = load_page_records(ocr_dir)
    return VlmDocument(
        eo_id=eo_id,
        year=year,
        ocr_dir=ocr_dir,
        doc=document_text(records, **document_text_kwargs),
        records=records,
    )
