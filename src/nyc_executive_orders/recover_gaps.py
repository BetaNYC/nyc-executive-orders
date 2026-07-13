"""Phase B.2: recover current-era EO PDFs missing from disk, via Wayback.

The Phase A (live-nycgov) harvest records every current-era executive order it
enumerates, but a subset have NO PDF on disk: the recorded PDF URL 404'd (the
file was pulled from live nyc.gov), pointed at an internal host the public can't
reach (`nyc-csg-web.csc.nycnet`), was served from an alternate edge
(`www1.nyc.gov`), or was never resolved at all (one order has no recorded URL).
This module recovers those PDFs from the Internet Archive.

Strategy (per gap):
  1. Derive an **ordered list of public-equivalent candidate URLs** for the order:
       (a) the DAM candidate — the recorded URL host-normalized to the canonical
           public DAM host (`www.nyc.gov`), or, for the one order with no recorded
           URL, reconstructed from the documented DAM path shape
           (config.DAM_PDF_PATH_TEMPLATE); then
       (b) the LEGACY candidate — the same `{year}/{filename}` tail under the
           pre-redesign `/assets/home/...` path (config.LEGACY_ASSETS_PDF_PATH_
           TEMPLATE). A CDX sweep found 24 of the 59 gap orders archived only here
           (as www1.nyc.gov 200 application/pdf); the DAM URL for those has just
           404 text/html captures. CDX folds www/www1, so the `www.nyc.gov` form
           matches the www1 captures.
  2. Query Wayback (via the archiver client) for an EXACT-URL snapshot of each
     candidate IN ORDER that is an `application/pdf` served `200`, take the newest,
     and STOP at the first candidate that yields one (the matched candidate wins;
     later candidates are not queried).
  3. Download the archived bytes go-slow and VALIDATE they are actually a PDF
     (magic bytes) before accepting — a soft 404 page or an HTML interstitial
     archived under a .pdf URL is rejected, never written into the corpus.
  4. Stamp the recovered row: pdf_path -> the file, source -> "wayback-gap". The
     matched candidate URL is recorded on the outcome (and the Wayback playback
     URL embeds it), so which route recovered each order stays auditable.

Like Phases A and B this is dependency-injected on the archiver client and only
duck-types the documented `ny_gov_web_archiver` / EDGI `wayback` surface
(`enumerate_captures`, `playback_url`, `client.get_memento(record).content`), so
the whole pass runs offline against a mocked client. Live Internet-Archive
traffic happens solely in the supervised runner (scripts/run_gap_recovery_live.py).

Wayback engine + API are built against the archiver's documented interface (see
gather_wayback_eo.py and ny-gov-web-archiver/harvest.py), NOT guessed — §0.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from . import config
from .download import pdf_dest, _rel_to_repo
from .gather_wayback_eo import (
    load_index_rows,
    reconcile_pdf_paths,
    _manifest_from_index,
)
from .index import IndexRow, write_index
from .manifest import write_gaps, write_manifest

logger = logging.getLogger("nyc_executive_orders.recover_gaps")


# --------------------------------------------------------------------------- #
# Gap detection + candidate-URL derivation
# --------------------------------------------------------------------------- #
def compute_gaps(
    rows: list[IndexRow], pdf_dir: str | Path = config.DEFAULT_PDF_DIR
) -> list[IndexRow]:
    """Return the rows with NO PDF on disk at the canonical pdfs/YYYY/<eo_id>.pdf.

    Disk presence — not the recorded `pdf_path` — is the authority: a row is a gap
    iff its expected PDF file is absent. (Call reconcile_pdf_paths first so a row
    whose file IS present but whose pdf_path was never stamped is not miscounted.)
    """
    pdf_dir_path = Path(pdf_dir)
    return [r for r in rows if not pdf_dest(r.eo_id, r.year, pdf_dir_path).exists()]


def candidate_public_url(row: IndexRow) -> str | None:
    """The public-equivalent DAM URL to look up on Wayback for a gap row.

    * Row HAS a recorded source_pdf_url -> keep its exact path (the city's real
      filename, which is not uniformly patterned) and normalize the host to the
      canonical public DAM host. This turns an internal `*.nycnet` URL or an
      alternate `www1.nyc.gov` edge into the `www.nyc.gov` URL Wayback would have
      archived; a `www.nyc.gov` URL is unchanged.
    * Row has NO recorded URL -> reconstruct from the documented DAM path shape
      using year/number/series. Requires a parseable number; returns None if the
      number is unknown (nothing to reconstruct from), so the caller flags it.
    """
    if row.source_pdf_url:
        parts = urlsplit(row.source_pdf_url)
        return urlunsplit(("https", config.DAM_PUBLIC_HOST, parts.path, "", ""))

    if not row.number:
        return None
    series = "eeo" if row.is_emergency else "eo"
    path = config.DAM_PDF_PATH_TEMPLATE.format(
        year=row.year, series=series, number=row.number
    )
    return f"https://{config.DAM_PUBLIC_HOST}{path}"


def candidate_legacy_url(dam_url: str, row: IndexRow) -> str:
    """The legacy `/assets/home/...` fallback URL for a gap row.

    Reuses the DAM candidate's `{year}/{filename}` tail under the pre-redesign
    path on the canonical public host. `{filename}` is the DAM candidate's
    basename lowercased — which is the recorded source URL's basename (host
    normalization preserves the path) for a URL-bearing row, and the reconstructed
    `<series>-<number>.pdf` for the no-URL row. Lowercasing matches the archived
    legacy captures (e.g. `2022/eeo-290.pdf`). CDX folds www/www1, so this
    `www.nyc.gov` URL matches the www1.nyc.gov captures the sweep found.
    """
    filename = Path(urlsplit(dam_url).path).name.lower()
    path = config.LEGACY_ASSETS_PDF_PATH_TEMPLATE.format(
        year=row.year, filename=filename
    )
    return f"https://{config.DAM_PUBLIC_HOST}{path}"


def candidate_urls(row: IndexRow) -> list[str]:
    """Ordered list of public-equivalent candidate URLs to try for a gap row.

    [DAM candidate, LEGACY-assets candidate]. Empty if no DAM candidate can be
    derived (no recorded URL and no parseable number). The gap pass queries these
    in order and stops at the first that has a usable Wayback snapshot.
    """
    dam = candidate_public_url(row)
    if dam is None:
        return []
    return [dam, candidate_legacy_url(dam, row)]


def _looks_like_pdf(content: bytes) -> bool:
    """True iff the bytes begin with the PDF magic marker `%PDF`.

    Wayback occasionally returns an archived HTML soft-404 / interstitial under a
    .pdf URL; the magic-byte check rejects those so only real PDFs enter the
    corpus. (Standard PDFs start with `%PDF-1.x`; we check the 4-byte prefix.)
    """
    return content[:4] == b"%PDF"


# --------------------------------------------------------------------------- #
# Wayback lookup for one exact URL
# --------------------------------------------------------------------------- #
def find_latest_pdf_capture(client, candidate_url: str):
    """Newest `application/pdf`/200 Wayback capture of an EXACT URL, or None.

    Delegates the CDX query to the archiver's `enumerate_captures` with
    match_type="exact" and the same PDF+200 client-side filter Phase B uses, so
    behavior is identical whether records come from live CDX or a mocked client.
    All returned records are captures of the same URL; the latest by timestamp is
    the best-preserved copy.
    """
    from ny_gov_web_archiver.harvest import enumerate_captures

    records = enumerate_captures(
        client,
        candidate_url,
        match_type="exact",
        mimetypes=["application/pdf"],
        statuses=[200],
    )
    if not records:
        return None
    return max(records, key=lambda r: r.timestamp)


# --------------------------------------------------------------------------- #
# Recovery outcome model
# --------------------------------------------------------------------------- #
# Terminal statuses for a single gap.
ST_RECOVERED = "recovered"  # bytes downloaded + validated this run
ST_CACHED = "cached"  # PDF already on disk (idempotent re-run)
ST_WOULD_RECOVER = "would-recover"  # dry-run: a usable snapshot was found
ST_NO_SNAPSHOT = "no-snapshot"  # Wayback has no PDF/200 capture of the URL
ST_NOT_PDF = "not-a-pdf"  # snapshot fetched but failed the magic-byte check
ST_NO_CANDIDATE = "no-candidate-url"  # no recorded URL and number unparseable
ST_ERROR = "error"  # fetch raised

_UNRECOVERABLE = {ST_NO_SNAPSHOT, ST_NOT_PDF, ST_NO_CANDIDATE, ST_ERROR}


@dataclass
class GapOutcome:
    """The result of attempting to recover one gap order."""

    eo_id: str
    year: int
    candidate_url: str | None
    status: str
    wayback_url: str | None = None  # playback URL of the recovered/found capture
    pdf_path: str | None = None
    reason: str | None = None  # human-readable detail for the unrecoverable set

    @property
    def unrecoverable(self) -> bool:
        return self.status in _UNRECOVERABLE


@dataclass
class GapRecoveryResult:
    outcomes: list[GapOutcome] = field(default_factory=list)
    index_rows: list[IndexRow] = field(default_factory=list)
    gaps_total: int = 0
    recovered: int = 0
    cached: int = 0
    would_recover: int = 0
    unrecoverable: int = 0
    errors: int = 0
    dry_run: bool = True
    output_paths: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Per-gap recovery
# --------------------------------------------------------------------------- #
def _recover_one(
    client,
    row: IndexRow,
    *,
    download: bool,
    pdf_dir: Path,
    on_fetch,
) -> GapOutcome:
    """Attempt to recover a single gap row. Never raises — records every outcome."""
    candidates = candidate_urls(row)
    if not candidates:
        return GapOutcome(
            eo_id=row.eo_id,
            year=row.year,
            candidate_url=None,
            status=ST_NO_CANDIDATE,
            reason="no recorded source URL and no parseable number to reconstruct one",
        )
    primary = candidates[0]

    # Idempotency: a PDF already on disk needs no fetch (safe re-run).
    dest = pdf_dest(row.eo_id, row.year, pdf_dir)
    if dest.exists():
        return GapOutcome(
            eo_id=row.eo_id,
            year=row.year,
            candidate_url=primary,
            status=ST_CACHED,
            pdf_path=_rel_to_repo(dest),
        )

    # Try each candidate IN ORDER; stop at the first with a usable snapshot. The
    # DAM candidate is queried first; the legacy `/assets/home/...` candidate is
    # only queried if the DAM candidate has no PDF/200 capture.
    matched: str | None = None
    record = None
    last_error: Exception | None = None
    for cand in candidates:
        try:
            rec = find_latest_pdf_capture(client, cand)
        except Exception as exc:  # noqa: BLE001 - record every failure, never abort
            logger.warning("gap.enumerate.error eo_id=%s url=%s error=%s", row.eo_id, cand, exc)
            last_error = exc
            continue
        if rec is not None:
            matched = cand
            record = rec
            break

    if record is None:
        if last_error is not None:
            return GapOutcome(
                eo_id=row.eo_id,
                year=row.year,
                candidate_url=primary,
                status=ST_ERROR,
                reason=(
                    "CDX lookup failed for every candidate (dam + legacy-assets); "
                    f"last: {type(last_error).__name__}: {last_error}"
                ),
            )
        logger.info("gap.no_snapshot eo_id=%s urls=%s", row.eo_id, candidates)
        return GapOutcome(
            eo_id=row.eo_id,
            year=row.year,
            candidate_url=primary,
            status=ST_NO_SNAPSHOT,
            reason=(
                "no application/pdf 200 snapshot on Wayback for either the dam or "
                "legacy-assets candidate"
            ),
        )

    from ny_gov_web_archiver.harvest import playback_url

    wb_url = playback_url(record)

    if not download:
        # Dry-run: a usable snapshot exists, but we issue ZERO memento fetches.
        return GapOutcome(
            eo_id=row.eo_id,
            year=row.year,
            candidate_url=matched,
            status=ST_WOULD_RECOVER,
            wayback_url=wb_url,
        )

    try:
        logger.info("gap.download.start eo_id=%s url=%s", row.eo_id, record.original)
        memento = client.get_memento(record)
        on_fetch()
        content = memento.content
    except Exception as exc:  # noqa: BLE001 - record every failure, never abort
        logger.warning("gap.download.error eo_id=%s url=%s error=%s", row.eo_id, matched, exc)
        return GapOutcome(
            eo_id=row.eo_id,
            year=row.year,
            candidate_url=matched,
            status=ST_ERROR,
            wayback_url=wb_url,
            reason=f"memento fetch failed: {type(exc).__name__}: {exc}",
        )

    if not _looks_like_pdf(content):
        logger.warning(
            "gap.not_pdf eo_id=%s url=%s bytes=%d — snapshot is not a PDF, rejected",
            row.eo_id,
            matched,
            len(content),
        )
        return GapOutcome(
            eo_id=row.eo_id,
            year=row.year,
            candidate_url=matched,
            status=ST_NOT_PDF,
            wayback_url=wb_url,
            reason=f"archived snapshot failed the PDF magic-byte check ({len(content)} bytes)",
        )

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    logger.info("gap.download.done eo_id=%s path=%s bytes=%d", row.eo_id, dest, len(content))
    return GapOutcome(
        eo_id=row.eo_id,
        year=row.year,
        candidate_url=matched,
        status=ST_RECOVERED,
        wayback_url=wb_url,
        pdf_path=_rel_to_repo(dest),
    )


def _apply_outcome(row: IndexRow, outcome: GapOutcome) -> None:
    """Stamp a successful recovery onto its index row (in place).

    On recovery/cache the row gains a pdf_path and its provenance flips to
    `wayback-gap`. The row's original recorded source_pdf_url is PRESERVED (the
    task's rule) — except the one order that had none, where the Wayback playback
    URL is recorded so the row finally carries a usable source URL.
    """
    if outcome.status not in (ST_RECOVERED, ST_CACHED):
        return
    row.pdf_path = outcome.pdf_path
    row.source = config.SOURCE_WAYBACK_GAP
    if not row.source_pdf_url and outcome.wayback_url:
        row.source_pdf_url = outcome.wayback_url


# --------------------------------------------------------------------------- #
# gaps.md — unrecoverable + recovered sections appended to the standard report
# --------------------------------------------------------------------------- #
def _append_gap_recovery_sections(gaps_path: Path, result: GapRecoveryResult) -> None:
    """Append gap-recovery detail to a freshly written gaps.md (idempotent).

    write_gaps() rewrites gaps.md from scratch each run, so appending here is
    deterministic — the sections are always regenerated, never accreted.
    """
    recovered = [o for o in result.outcomes if o.status in (ST_RECOVERED, ST_CACHED)]
    unrecoverable = [o for o in result.outcomes if o.unrecoverable]

    lines: list[str] = ["", "## Recovered via Wayback gap pass", ""]
    if recovered:
        lines.append("| eo_id | wayback_url | pdf_path |")
        lines.append("|---|---|---|")
        for o in recovered:
            lines.append(f"| {o.eo_id} | {o.wayback_url or '(cached on disk)'} | {o.pdf_path or ''} |")
    else:
        lines.append("_None recovered this pass._")
    lines.append("")

    lines.append("## Unrecoverable after Wayback pass")
    lines.append("")
    lines.append(
        "_Current-era orders with no PDF on disk that the Wayback gap pass could "
        "not recover. Each has a reason; these are the residual gaps._"
    )
    lines.append("")
    if unrecoverable:
        lines.append("| eo_id | candidate_url | reason |")
        lines.append("|---|---|---|")
        for o in unrecoverable:
            lines.append(f"| {o.eo_id} | {o.candidate_url or ''} | {o.reason or o.status} |")
    else:
        lines.append("_None — every gap was recovered._")
    lines.append("")

    with gaps_path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def _delayer(delay: float):
    import time

    if delay and delay > 0:
        return lambda: time.sleep(delay)
    return lambda: None


def run_gap_recovery(
    client,
    *,
    download: bool = False,
    delay: float = 0.0,
    pdf_dir: str | Path = config.DEFAULT_PDF_DIR,
    index_dir: str | Path = config.DEFAULT_INDEX_DIR,
    out_dir: str | Path = config.DEFAULT_OUT_DIR,
    index_path: str | Path | None = None,
    write_outputs: bool = True,
) -> GapRecoveryResult:
    """Full gap-recovery pass: load -> reconcile -> find gaps -> recover -> write.

    `download=False` (default) is a dry run: it enumerates Wayback snapshots and
    reports which gaps WOULD be recovered, but issues ZERO memento fetches and
    writes no PDF. The archiver client's own throttle plus `delay` enforce go-slow
    when downloading.
    """
    on_fetch = _delayer(delay)
    result = GapRecoveryResult(dry_run=not download)

    idx_path = Path(index_path) if index_path is not None else Path(index_dir) / "eo_index.json"
    rows = load_index_rows(idx_path)

    # Make the index truthful to disk BEFORE computing gaps: a row whose PDF is
    # present but whose pdf_path was never stamped must not be treated as a gap.
    reconcile_pdf_paths(rows, pdf_dir)

    gaps = compute_gaps(rows, pdf_dir)
    result.gaps_total = len(gaps)
    logger.info("gap.recovery.begin gaps=%d download=%s", len(gaps), download)

    pdf_dir_path = Path(pdf_dir)
    for row in gaps:
        outcome = _recover_one(
            client, row, download=download, pdf_dir=pdf_dir_path, on_fetch=on_fetch
        )
        result.outcomes.append(outcome)
        _apply_outcome(row, outcome)
        if outcome.status == ST_RECOVERED:
            result.recovered += 1
        elif outcome.status == ST_CACHED:
            result.cached += 1
        elif outcome.status == ST_WOULD_RECOVER:
            result.would_recover += 1
        elif outcome.status == ST_ERROR:
            result.errors += 1
            result.unrecoverable += 1
        elif outcome.unrecoverable:
            result.unrecoverable += 1

    result.index_rows = rows

    if write_outputs:
        index_paths = write_index(rows, index_dir)
        manifest_rows = [_manifest_from_index(r) for r in rows]
        manifest_path = write_manifest(manifest_rows, out_dir)
        gaps_path = write_gaps(manifest_rows, out_dir)
        _append_gap_recovery_sections(gaps_path, result)
        result.output_paths = {
            "index_json": index_paths["json"],
            "index_csv": index_paths["csv"],
            "manifest": manifest_path,
            "gaps": gaps_path,
        }

    logger.info(
        "gap.recovery.done gaps=%d recovered=%d cached=%d would_recover=%d "
        "unrecoverable=%d errors=%d dry_run=%s",
        result.gaps_total,
        result.recovered,
        result.cached,
        result.would_recover,
        result.unrecoverable,
        result.errors,
        result.dry_run,
    )
    return result


__all__ = [
    "compute_gaps",
    "candidate_public_url",
    "candidate_legacy_url",
    "candidate_urls",
    "find_latest_pdf_capture",
    "GapOutcome",
    "GapRecoveryResult",
    "run_gap_recovery",
]
