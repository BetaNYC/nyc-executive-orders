"""Phase B: historical EO backfill from the Internet Archive (Wayback Machine).

The 2026 nyc.gov redesign removed the historical executive-order PDFs that had
long lived under `nyc.gov/html/records/pdf/executive_orders/`. This module
recovers them from Wayback, produces `source: "wayback"` records, and merges them
into the current-era corpus (Phase A), preferring the live rows on any collision.

Wayback logic is NOT reimplemented here. The engine is BetaNYC's
`ny-gov-web-archiver` (a throttled EDGI-`wayback` orchestrator): we call its
public API — `enumerate_captures()` (CDX), `playback_url()`, and the go-slow
client from `wayback_client.build_client()`. This module is the EO-specific
application layer on top of it: the URL prefix, the filename->eo_id parse, the
`pdfs/YYYY/<eo_id>.pdf` layout, and the prefer-live merge.

Like Phase A, the pipeline is dependency-injected on the client: `run_wayback_harvest`
takes an already-built client and only duck-types the documented archiver/wayback
surface, so the whole thing is exercised offline with a mocked client. Live
Internet-Archive traffic happens solely in the supervised runner
(scripts/run_wayback_harvest_live.py), never in tests or under any agent.

Filename grammar (built against the documented pattern, NOT guessed — project
STATUS.md / README data landscape):

    nyc.gov/html/records/pdf/executive_orders/YYYYEO0NN.pdf   (regular)
    ...                                       /YYYYEEO0NN.pdf  (emergency, if present)

We enumerate what CDX actually returns and parse each captured basename against
this shape. A basename that does not parse is FLAGGED (never silently dropped)
so a human can inspect era-specific filename variants.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .download import pdf_dest, _rel_to_repo
from .harvest import dedupe_rows
from .identity import mint_eo_id
from .index import INDEX_FIELDS, IndexRow, write_index
from .manifest import ManifestRow, write_gaps, write_manifest

logger = logging.getLogger("nyc_executive_orders.gather_wayback_eo")


# --------------------------------------------------------------------------- #
# Filename -> identity parse
# --------------------------------------------------------------------------- #
# YYYY + series token (EEO before EO in the alternation) + a number label. The
# label may be zero-padded ("001") or, defensively, dotted ("1.37") for the
# emergency scheme; it is preserved verbatim and handed to mint_eo_id, which
# zero-pads integer regular labels and keeps emergency labels literal. A single
# optional separator between components tolerates minor era variations.
_EO_FILENAME_RE = re.compile(
    r"(?P<year>\d{4})[-_ ]?(?P<series>EEO|EO)[-_ ]?(?P<num>\d+(?:\.\d+)?)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedEO:
    """Identity parsed from a captured EO PDF filename."""

    year: int
    number: str  # literal label ("041", "1.37"); mint_eo_id normalizes it
    is_emergency: bool
    basename: str  # the filename we parsed, for provenance/flagging


def _basename(original_url: str) -> str:
    """The final path segment of a captured original URL (no query/fragment)."""
    path = original_url.split("?", 1)[0].split("#", 1)[0]
    return path.rstrip("/").rsplit("/", 1)[-1]


def parse_eo_filename(original_url: str) -> ParsedEO | None:
    """Parse a captured EO PDF URL's filename into an identity, or None.

    Returns None when the basename does not match the documented EO filename
    shape — the caller flags those for human review rather than dropping them.
    """
    base = _basename(original_url)
    stem = base[:-4] if base.lower().endswith(".pdf") else base
    m = _EO_FILENAME_RE.search(stem)
    if not m:
        return None
    return ParsedEO(
        year=int(m.group("year")),
        number=m.group("num"),
        is_emergency=m.group("series").upper() == "EEO",
        basename=base,
    )


# --------------------------------------------------------------------------- #
# Capture selection
# --------------------------------------------------------------------------- #
def select_best_capture_per_url(records: list) -> list:
    """Collapse many captures of one original URL to the single latest capture.

    CDX returns every snapshot of every file over time; for the corpus we want
    one PDF per source URL. The most recent 200 capture is chosen (the
    best-preserved copy). Records are duck-typed CdxRecords (`.original`,
    `.timestamp`). Order of first appearance is preserved for determinism.
    """
    best: dict[str, object] = {}
    order: list[str] = []
    for r in records:
        key = r.original
        if key not in best:
            best[key] = r
            order.append(key)
        elif r.timestamp > best[key].timestamp:
            best[key] = r
    return [best[k] for k in order]


# --------------------------------------------------------------------------- #
# Enumeration (delegates CDX to the archiver)
# --------------------------------------------------------------------------- #
def enumerate_eo_captures(
    client,
    *,
    from_year: int | None = None,
    to_year: int | None = None,
    limit: int | None = None,
) -> list:
    """Enumerate historical EO PDF captures via the archiver's CDX prefix query.

    One CDX query (etiquette: single index query, then throttle downloads) over
    `WAYBACK_EO_URL_PREFIX`, match_type=prefix, filtered to `application/pdf` +
    HTTP 200. The archiver applies the same client-side filter deterministically,
    so this is faithful whether records come from live CDX or a mocked client.
    """
    from ny_gov_web_archiver.harvest import enumerate_captures

    kwargs = {
        "match_type": "prefix",
        "from_year": from_year,
        "to_year": to_year,
        "mimetypes": ["application/pdf"],
        "statuses": [200],
    }
    if limit is not None:
        kwargs["limit"] = limit

    records = enumerate_captures(client, config.WAYBACK_EO_URL_PREFIX, **kwargs)
    logger.info("wayback.enumerate captures=%d", len(records))
    return records


# --------------------------------------------------------------------------- #
# Row construction + fetch
# --------------------------------------------------------------------------- #
@dataclass
class Flagged:
    """A captured URL whose filename did not parse into an EO identity."""

    original_url: str
    basename: str
    wayback_url: str


def _delayer(delay: float):
    if delay and delay > 0:
        return lambda: time.sleep(delay)
    return lambda: None


def _fetch_memento(client, record, dest: Path, on_fetch) -> tuple[str | None, str, str | None]:
    """Fetch one archived PDF to `dest` (skip-if-present). Go-slow via `on_fetch`.

    Returns (pdf_path_rel_or_None, download_status, error). The archiver's client
    paces memento calls internally (its go-slow throttle); `on_fetch` adds the
    configured extra delay. A network call happens ONLY on a cache miss.
    """
    if dest.exists():
        logger.info("wayback.download.cached path=%s", dest)
        return _rel_to_repo(dest), "cached", None
    try:
        logger.info("wayback.download.start url=%s", record.original)
        memento = client.get_memento(record)
        on_fetch()
        content = memento.content
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        logger.info("wayback.download.done path=%s bytes=%d", dest, len(content))
        return _rel_to_repo(dest), "downloaded", None
    except Exception as exc:  # noqa: BLE001 - record every failure, never abort
        logger.warning("wayback.download.error url=%s error=%s", record.original, exc)
        return None, "error", f"{type(exc).__name__}: {exc}"


@dataclass
class WaybackBuild:
    index_rows: list[IndexRow] = field(default_factory=list)
    manifest_rows: list[ManifestRow] = field(default_factory=list)
    flagged: list[Flagged] = field(default_factory=list)
    downloaded: int = 0
    cached: int = 0
    errors: int = 0


def build_wayback_rows(
    client,
    records: list,
    *,
    download: bool = False,
    pdf_dir: str | Path = config.DEFAULT_PDF_DIR,
    on_fetch=None,
) -> WaybackBuild:
    """Parse captures -> mint ids -> (optionally) fetch -> build index/manifest rows.

    `records` should already be one-per-URL (see select_best_capture_per_url).
    Unparseable filenames are flagged, not dropped. `playback_url` (the resolved
    Wayback URL) is the row's source_pdf_url. Rows are paired 1:1 index<->manifest.
    """
    from ny_gov_web_archiver.harvest import playback_url

    out = WaybackBuild()
    on_fetch = on_fetch or (lambda: None)
    pdf_dir_path = Path(pdf_dir)

    for record in records:
        wb_url = playback_url(record)
        parsed = parse_eo_filename(record.original)
        if parsed is None:
            out.flagged.append(
                Flagged(original_url=record.original, basename=_basename(record.original), wayback_url=wb_url)
            )
            logger.info("wayback.flagged unparsed basename=%s url=%s", _basename(record.original), record.original)
            continue

        eo_id = mint_eo_id(parsed.year, parsed.number, parsed.is_emergency)

        pdf_path: str | None = None
        download_status = "skipped"  # dry-run default
        download_error: str | None = None
        if download:
            dest = pdf_dest(eo_id, parsed.year, pdf_dir_path)
            pdf_path, download_status, download_error = _fetch_memento(
                client, record, dest, on_fetch
            )
            if download_status == "downloaded":
                out.downloaded += 1
            elif download_status == "cached":
                out.cached += 1
            elif download_status == "error":
                out.errors += 1

        out.index_rows.append(
            IndexRow(
                eo_id=eo_id,
                number=parsed.number,
                year=parsed.year,
                is_emergency=parsed.is_emergency,
                date_signed=None,  # not derivable from a filename (Phase B has no title/date)
                title="",  # no title in the archived filename; filled by a later metadata pass
                source_pdf_url=wb_url,
                pdf_path=pdf_path,
                source=config.SOURCE_WAYBACK,
            )
        )
        out.manifest_rows.append(
            ManifestRow(
                eo_id=eo_id,
                number=parsed.number,
                year=parsed.year,
                is_emergency=parsed.is_emergency,
                date_signed=None,
                title="",
                article_url="",  # historical scheme has no article page
                source_pdf_url=wb_url,
                pdf_path=pdf_path,
                pdf_resolved=True,  # the capture IS the resolved PDF
                download_status=download_status,
                download_error=download_error,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Load the live corpus + merge (prefer live)
# --------------------------------------------------------------------------- #
def load_index_rows(index_path: str | Path) -> list[IndexRow]:
    """Load an existing eo_index.json into IndexRows (or [] if absent).

    Used to read the Phase A (live-nycgov) corpus so Phase B can merge against
    it. Only the locked INDEX_FIELDS are read; unknown keys are ignored.
    """
    path = Path(index_path)
    if not path.exists():
        logger.info("wayback.merge no live index at %s — wayback-only corpus", path)
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: list[IndexRow] = []
    for d in data:
        rows.append(
            IndexRow(
                eo_id=d["eo_id"],
                number=d.get("number"),
                year=d["year"],
                is_emergency=bool(d.get("is_emergency")),
                date_signed=d.get("date_signed"),
                title=d.get("title") or "",
                source_pdf_url=d.get("source_pdf_url"),
                pdf_path=d.get("pdf_path"),
                source=d.get("source", config.SOURCE_LIVE),
            )
        )
    logger.info("wayback.merge loaded live index rows=%d path=%s", len(rows), path)
    return rows


def _manifest_from_index(ir: IndexRow) -> ManifestRow:
    """Derive a ManifestRow from an IndexRow (for live rows loaded from disk).

    The merged manifest/gaps span the whole 1974->present corpus; live rows come
    from the index (which lacks article_url / download detail), so those fields
    are reconstructed conservatively: a row with a pdf_path is treated as cached,
    otherwise skipped, and pdf_resolved follows whether a source URL is present.
    """
    return ManifestRow(
        eo_id=ir.eo_id,
        number=ir.number,
        year=ir.year,
        is_emergency=ir.is_emergency,
        date_signed=ir.date_signed,
        title=ir.title,
        article_url="",
        source_pdf_url=ir.source_pdf_url,
        pdf_path=ir.pdf_path,
        pdf_resolved=ir.source_pdf_url is not None,
        download_status="cached" if ir.pdf_path else "skipped",
    )


@dataclass
class MergeResult:
    index_rows: list[IndexRow] = field(default_factory=list)
    manifest_rows: list[ManifestRow] = field(default_factory=list)
    dropped_wayback_ids: list[str] = field(default_factory=list)
    kept_wayback: int = 0


def merge_prefer_live(
    live_index: list[IndexRow],
    wayback_index: list[IndexRow],
    wayback_manifest: list[ManifestRow],
) -> MergeResult:
    """Combine live + wayback corpora, preferring live-nycgov on an eo_id clash.

    An EO recovered from Wayback that ALSO exists in the live corpus (same eo_id)
    is a duplicate: the live row is authoritative (fresher, richer metadata), so
    the Wayback row is dropped and logged. Wayback rows whose eo_id is absent from
    the live corpus are kept — that is the historical recovery.

    `wayback_index` and `wayback_manifest` are paired 1:1 by position; they are
    filtered in lockstep. The merged manifest = live rows (derived from the live
    index) + kept wayback manifest rows, so gaps.md spans the full corpus.
    """
    if len(wayback_index) != len(wayback_manifest):
        raise ValueError(
            f"wayback index/manifest counts diverge "
            f"({len(wayback_index)} vs {len(wayback_manifest)})"
        )

    live_ids = {ir.eo_id for ir in live_index}
    result = MergeResult()

    # Live rows first (authoritative), then kept wayback rows.
    result.index_rows.extend(live_index)
    result.manifest_rows.extend(_manifest_from_index(ir) for ir in live_index)

    for ir, mr in zip(wayback_index, wayback_manifest):
        if ir.eo_id in live_ids:
            result.dropped_wayback_ids.append(ir.eo_id)
            logger.info(
                "wayback.merge drop eo_id=%s — already present from live-nycgov "
                "(preferring live)",
                ir.eo_id,
            )
            continue
        result.index_rows.append(ir)
        result.manifest_rows.append(mr)
        result.kept_wayback += 1

    logger.info(
        "wayback.merge done live=%d wayback_kept=%d wayback_dropped=%d total=%d",
        len(live_index),
        result.kept_wayback,
        len(result.dropped_wayback_ids),
        len(result.index_rows),
    )
    return result


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
@dataclass
class WaybackHarvestResult:
    merged_index: list[IndexRow] = field(default_factory=list)
    merged_manifest: list[ManifestRow] = field(default_factory=list)
    flagged: list[Flagged] = field(default_factory=list)
    conflicts: list[tuple[str, str | None, str | None]] = field(default_factory=list)
    dropped_wayback_ids: list[str] = field(default_factory=list)
    enumerated: int = 0
    unique_urls: int = 0
    wayback_rows: int = 0
    wayback_kept: int = 0
    downloaded: int = 0
    cached: int = 0
    errors: int = 0
    intra_deduped: int = 0
    dry_run: bool = True
    output_paths: dict = field(default_factory=dict)


def run_wayback_harvest(
    client,
    *,
    from_year: int | None = None,
    to_year: int | None = None,
    download: bool = False,
    delay: float = 0.0,
    limit: int | None = None,
    pdf_dir: str | Path = config.DEFAULT_PDF_DIR,
    index_dir: str | Path = config.DEFAULT_INDEX_DIR,
    out_dir: str | Path = config.DEFAULT_OUT_DIR,
    live_index_path: str | Path | None = None,
    write_outputs: bool = True,
) -> WaybackHarvestResult:
    """Full Phase B pipeline: enumerate -> parse -> (fetch) -> merge -> write.

    `download=False` (default) is a dry run: it enumerates, parses, and merges,
    but issues ZERO memento fetches — the merged index/manifest record what WOULD
    be recovered. The archiver client's own throttle plus `delay` enforce go-slow
    when downloading.
    """
    on_fetch = _delayer(delay)
    result = WaybackHarvestResult(dry_run=not download)

    records = enumerate_eo_captures(client, from_year=from_year, to_year=to_year, limit=limit)
    result.enumerated = len(records)

    records = select_best_capture_per_url(records)
    result.unique_urls = len(records)

    build = build_wayback_rows(
        client, records, download=download, pdf_dir=pdf_dir, on_fetch=on_fetch
    )
    result.flagged = build.flagged
    result.downloaded = build.downloaded
    result.cached = build.cached
    result.errors = build.errors

    # Reuse Phase A's exact-duplicate collapse + same-id/different-pdf conflict
    # guard over the wayback set before merging (defensive: URL-dedup already
    # removed exact dups, so this mainly surfaces any intra-wayback conflicts).
    wb_index, wb_manifest, deduped, conflicts = dedupe_rows(
        build.index_rows, build.manifest_rows
    )
    result.wayback_rows = len(wb_index)
    result.intra_deduped = deduped
    result.conflicts = conflicts

    live_path = Path(live_index_path) if live_index_path is not None else Path(index_dir) / "eo_index.json"
    live_index = load_index_rows(live_path)

    merged = merge_prefer_live(live_index, wb_index, wb_manifest)
    result.merged_index = merged.index_rows
    result.merged_manifest = merged.manifest_rows
    result.dropped_wayback_ids = merged.dropped_wayback_ids
    result.wayback_kept = merged.kept_wayback

    if write_outputs:
        index_paths = write_index(merged.index_rows, index_dir)
        manifest_path = write_manifest(merged.manifest_rows, out_dir)
        gaps_path = write_gaps(merged.manifest_rows, out_dir)
        result.output_paths = {
            "index_json": index_paths["json"],
            "index_csv": index_paths["csv"],
            "manifest": manifest_path,
            "gaps": gaps_path,
        }

    logger.info(
        "wayback.harvest.done enumerated=%d unique=%d wayback_rows=%d kept=%d "
        "dropped_dup=%d flagged=%d downloaded=%d cached=%d errors=%d dry_run=%s",
        result.enumerated,
        result.unique_urls,
        result.wayback_rows,
        result.wayback_kept,
        len(result.dropped_wayback_ids),
        len(result.flagged),
        result.downloaded,
        result.cached,
        result.errors,
        result.dry_run,
    )
    return result


# Re-export for callers that want the locked field list without a second import.
__all__ = [
    "ParsedEO",
    "parse_eo_filename",
    "select_best_capture_per_url",
    "enumerate_eo_captures",
    "build_wayback_rows",
    "load_index_rows",
    "merge_prefer_live",
    "run_wayback_harvest",
    "WaybackHarvestResult",
    "INDEX_FIELDS",
]
