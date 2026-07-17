"""Phase D — fold the DORIS Government Publications Portal (GPP) harvest into the corpus.

The DORIS Government Publications Portal (a Samvera Hyrax repository at
``a860-gpp.nyc.gov``) holds the City's deposited copies of mayoral executive
orders. A 2026-07-17 browser-session harvest pulled every file behind Report Type
= "Executive Orders" into a local staging dir as ``gpp-<fileset_id>.pdf``. This
module classifies each GPP item against the corpus and folds the files in
*additively* — mirroring the de Blasio backfill's discipline (existing records
that aren't touched stay byte-identical).

Authoritative sources, built against — never guessed (engineering-standards §0):
  * Per-item metadata: the committed GPP inventory snapshot
    (``sources/gpp/inputs/gpp-eo-inventory-2026-07-17.json``), a faithful copy of
    the live catalog JSON. Fields used: ``id`` (gpp item id), ``t`` (title),
    ``dp`` (date_published, YYYY-MM-DD — the trustworthy date), ``cr`` (creators;
    the mayor), ``fs`` (comma-joined file-set ids), ``desc`` (description).
    ``cy`` (calendar years) is UNRELIABLE and never used (recon report §6).
  * Overlap/disposition math + the title parser + the creator→mayor map are ported
    from the recon analysis script (BetaNYC workspace
    ``team/research/mayoral-executive-orders/data/2026-07-17-gpp/overlap_analysis_script.py``),
    which handles every observed title variant. The mayoral-term table is NOT
    re-ported — it is reused from ``enrich.MAYORAL_TERMS`` (the one place a NYC
    mayor is hardcoded in this repo).
  * Download URL shape (``/downloads/<file_set_id>``): recon report §4.

Disposition (re-derived here from inventory + corpus, per the recon; the harvest
manifest's ``class`` field is advisory/stale and used only as a cross-check):

  * ``net-new``            — no corpus record; GPP supplies it wholesale → MINT a
                             record, PDF becomes the primary ``pdfs/YYYY/<eo_id>.pdf``.
  * ``gap-closer-mint``    — a known-missing order with no corpus record (19 de
                             Blasio regular EOs + Koch EO 9) → MINT, primary PDF.
  * ``gap-closer-existing``— an existing corpus record with ``pdf_path: null`` (the
                             53 "no-pdf" orders) → attach the GPP PDF as its
                             primary, re-parse for text; metadata byte-preserved.
  * ``dual``               — an order we already hold WITH a PDF → the GPP copy is a
                             SECOND source lineage under ``sources/gpp/`` (primary
                             ``pdfs/`` untouched, record byte-identical).
  * ``volume``             — a pre-1974 bound compilation → park under
                             ``sources/gpp/volumes/``; NO record (splitting is a
                             later phase).
  * ``excluded``           — 6 non-EO strays misfiled under the report type + 1
                             no-file item; skipped with a logged reason.

The GPP lineage for every touched order lands in a SIDECAR
(``corpus/gpp_provenance.json``), never inline in ``eo.json`` — the corpus
frontmatter field set is locked and re-emitted from that locked set on every
parse/clean/supersede pass, so inline GPP keys would be silently dropped; the
sidecar also keeps dual records byte-identical.

Fully local + offline: reads staged PDFs + the committed corpus, copies files,
writes JSON/Markdown. No network, no cloud (engineering-standards §7). Idempotent
and resumable: re-running over the same (or a more-complete) staging dir never
duplicates or corrupts — the prior sidecar pins already-integrated orders.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import config
from .build_corpus import ParsedEO, parse_record, render_markdown
from .download import pdf_dest
from .enrich import mayor_for_year
from .identity import mint_eo_id
from .ocr import OcrConfig

logger = logging.getLogger("nyc_executive_orders.gpp")

# --------------------------------------------------------------------------- #
# Disposition classes
# --------------------------------------------------------------------------- #
NET_NEW = "net-new"
GAP_CLOSER_MINT = "gap-closer-mint"
GAP_CLOSER_EXISTING = "gap-closer-existing"
DUAL = "dual"
VOLUME = "volume"
EXCLUDED = "excluded"

# Dispositions that create a brand-new corpus record.
_MINT_DISPOSITIONS = frozenset({NET_NEW, GAP_CLOSER_MINT})

# --------------------------------------------------------------------------- #
# Ported classification logic (source: recon overlap_analysis_script.py)
# --------------------------------------------------------------------------- #
# Creator string → corpus mayor label. Primary mayor resolution (the date table
# in enrich.MAYORAL_TERMS is the secondary fallback). Includes pre-1974 names so
# volume creators resolve, though volumes are not classified by mayor.
CREATOR_TO_MAYOR: dict[str, str] = {
    "abraham d. beame": "Beame", "abraham beame": "Beame", "beame": "Beame",
    "edward i. koch": "Koch", "ed koch": "Koch", "koch": "Koch",
    "david n. dinkins": "Dinkins", "dinkins": "Dinkins",
    "rudolph w. giuliani": "Giuliani", "rudolph giuliani": "Giuliani",
    "giuliani": "Giuliani",
    "michael r. bloomberg": "Bloomberg", "michael bloomberg": "Bloomberg",
    "bloomberg": "Bloomberg",
    "bill de blasio": "de Blasio", "de blasio": "de Blasio", "deblasio": "de Blasio",
    "eric adams": "Adams", "eric l. adams": "Adams", "adams": "Adams",
    "zohran mamdani": "Mamdani", "zohran kwame mamdani": "Mamdani",
    "mamdani": "Mamdani",
    "john v. lindsay": "Lindsay", "robert f. wagner": "Wagner", "wagner": "Wagner",
    "vincent r. impellitteri": "Impellitteri", "william o’dwyer": "O'Dwyer",
    "william o'dwyer": "O'Dwyer",
}

_VOLUME_RE = re.compile(r"executive orders and memoranda", re.I)
_TITLE_PATTERNS = (
    # "EEO 131 - ..." / "EEO 1.37" / "EO 56 - ..."
    re.compile(r"^\s*(?P<em>E?)EO\s+(?:No\.?\s*)?(?P<num>\d+(?:\.\d+)?)\b", re.I),
    # "Emergency Executive Order (No.) 188" / "Executive Order No. 40" /
    # "Executive Order 51, 2020"
    re.compile(
        r"^\s*(?P<em>Emergency\s+)?Executive\s+Order[,\s]*(?:No\.?\s*)?\s*"
        r"(?P<num>\d+(?:\.\d+)?)\b",
        re.I,
    ),
)

# Title kinds parse_title returns.
KIND_VOLUME = "volume"
KIND_EEO = "eeo"
KIND_EO = "eo"
KIND_UNPARSED = "unparsed"

# The 3 real EOs whose titles defeat the parser (recon report §1, §6) — rescued by
# GPP id to their (kind, number) IDENTITY read from the human-verified description.
# This map fixes ONLY the parse; the DISPOSITION still falls out of the corpus
# match (all 3 turned out to match existing corpus records — see the module test).
RESCUE_IDENTITY: dict[str, tuple[str, str]] = {
    "f1881n57p": (KIND_EEO, "99"),   # "Emergency Executive Order - Occupancy
                                     # Enforcement" / desc "…Number Ninety Nine…"
    "jw827d19v": (KIND_EO, "55"),    # "Ban on Non-Essential Travel…" / desc "EO 55"
    "3b591d01g": (KIND_EEO, "526"),  # "Emergency Execurive Order No. 526" (typo)
}

# 6 non-EO strays misfiled under the report type + 1 no-file item (recon §6).
# Excluded by GPP id at integration.
EXCLUDED_IDS: frozenset[str] = frozenset({
    "bz60d0174",  # Annual Report on Advertising FY2020
    "3f4628695",  # Comptroller audit letter (Special Narcotics Prosecutor)
    "n296x281t",  # FY2025 Quarterly Report Part II (DCAS)
    "kd17cz294",  # Hotel Order #55 (Rent Guidelines Board)
    "6682x8019",  # Apartment/Loft Order #57 (Rent Guidelines Board)
    "4m90f205b",  # DOF designation, Admin Code § 11-319
    "nz8063533",  # no file attached
})

# The 20 orders known to exist but never captured to a corpus record (recon §2):
# 19 de Blasio regular EOs + Koch EO 9. GPP closes these by MINTING a record.
# (mayor, is_emergency, number-string) keys — the number's year comes from GPP's dp.
KNOWN_MISSING_KEYS: frozenset[tuple[str, bool, str]] = frozenset(
    {("de Blasio", False, str(n)) for n in (
        23, 25, 29, 30, 31, 33, 37, 38, 46, 48,
        56, 61, 68, 69, 80, 82, 86, 87, 88,
    )}
    | {("Koch", False, "9")}
)


def mayor_from_creator(creators: str | None) -> str | None:
    """Resolve a corpus mayor label from a GPP ``cr`` (creators) string."""
    for part in re.split(r"[;,]", creators or ""):
        mayor = CREATOR_TO_MAYOR.get(part.strip().lower())
        if mayor:
            return mayor
    return None


def year_of(date_published: str | None) -> int | None:
    """Signing year = leading YYYY of GPP ``dp`` (date_published; the trusted date)."""
    m = re.match(r"^(\d{4})", date_published or "")
    return int(m.group(1)) if m else None


def mayor_from_date(date_published: str | None) -> str | None:
    """Fallback mayor resolution from the signing year (reuses enrich's term table)."""
    year = year_of(date_published)
    return mayor_for_year(year) if year is not None else None


def parse_title(title: str | None) -> tuple[str, str | None]:
    """``title -> (kind, number)`` where kind ∈ {volume, eo, eeo, unparsed}.

    Ported verbatim in behaviour from the recon analysis script so it handles every
    observed variant (``EEO 131 - …``, ``Executive Order No. 40``, dotted ``1.37``,
    trailing junk). ``number`` is the literal label as printed (may be dotted).
    """
    t = title or ""
    if _VOLUME_RE.search(t):
        return KIND_VOLUME, None
    for rx in _TITLE_PATTERNS:
        m = rx.match(t)
        if m:
            is_em = bool((m.group("em") or "").strip())
            return (KIND_EEO if is_em else KIND_EO), m.group("num")
    # Loose fallbacks: number later in the string.
    m = re.search(
        r"Emergency\s+Executive\s+Order\s*(?:No\.?\s*)?(\d+(?:\.\d+)?)", t, re.I
    )
    if m:
        return KIND_EEO, m.group(1)
    m = re.search(r"\bE\.?O\.?\s*(?:No\.?\s*)?(\d+(?:\.\d+)?)\b", t)
    if m:
        return KIND_EO, m.group(1)
    return KIND_UNPARSED, None


def norm_num(number: str | None) -> str | None:
    """Normalise a number label: strip leading zeros, keep the dotted scheme.

    ``"008" -> "8"``, ``"1.37" -> "1.37"``. Used only as a *match key* against the
    corpus (whose ``number`` is also the literal label); the minted ``eo_id`` gets
    its own padding from :func:`identity.mint_eo_id`.
    """
    if number is None:
        return None
    n = str(number).strip()
    if "." in n:
        head, tail = n.split(".", 1)
        return f"{int(head)}.{tail}"
    return str(int(n)) if n.isdigit() else n


def clean_gpp_title(title: str | None) -> str:
    """Light title cleanup for a minted record: collapse whitespace, strip trailing
    period. Never invents content; an empty title stays empty (gap-filled later by
    the clean stage from the PDF text)."""
    t = re.sub(r"\s+", " ", (title or "").strip())
    return t.rstrip(".").strip()


def download_url(fileset_id: str) -> str:
    """The DOCUMENTED GPP same-session download URL for a file-set id (recon §4).

    Recorded as provenance (``source_pdf_url`` on minted records / the sidecar); the
    bytes were already captured by the harvest — this is never re-fetched here.
    """
    return config.GPP_ORIGIN + config.GPP_DOWNLOAD_PATH_TEMPLATE.format(
        fileset_id=fileset_id
    )


# --------------------------------------------------------------------------- #
# Inventory / manifest loading
# --------------------------------------------------------------------------- #
def load_inventory(path: str | Path) -> list[dict]:
    """Load the GPP inventory snapshot; return its ``records`` list."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    records = data.get("records") if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise ValueError(f"inventory at {path} has no 'records' list")
    return records


def load_manifest(path: str | Path) -> dict[str, dict]:
    """Load the harvest manifest ``{fileset_id: {gpp_id, class}}`` (advisory)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"manifest at {path} is not an object")
    return data


def _fileset_ids(record: dict) -> list[str]:
    """Split the inventory ``fs`` (comma-joined file-set ids) into a clean list."""
    return [s.strip() for s in (record.get("fs") or "").split(",") if s.strip()]


# --------------------------------------------------------------------------- #
# Parsed GPP items and grouped orders
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class GppItem:
    """One GPP catalog item (one row of the inventory), parsed."""

    gpp_id: str
    title: str
    date_published: str | None
    creators: str
    description: str
    fileset_ids: tuple[str, ...]
    kind: str                      # volume | eo | eeo | unparsed
    number: str | None            # literal label as printed
    rescued: bool = False         # identity came from RESCUE_IDENTITY

    @property
    def is_emergency(self) -> bool:
        return self.kind == KIND_EEO

    @property
    def mayor(self) -> str | None:
        return mayor_from_creator(self.creators) or mayor_from_date(self.date_published)

    @property
    def year(self) -> int | None:
        return year_of(self.date_published)

    @property
    def match_key(self) -> tuple[str | None, bool, str | None]:
        """(mayor, is_emergency, normalised-number) — the corpus overlap key."""
        return (self.mayor, self.is_emergency, norm_num(self.number))


def build_items(inventory: Iterable[dict]) -> list[GppItem]:
    """Parse every inventory record into a :class:`GppItem` (rescues applied)."""
    items: list[GppItem] = []
    for rec in inventory:
        gpp_id = rec["id"]
        rescued = gpp_id in RESCUE_IDENTITY
        if rescued:
            kind, number = RESCUE_IDENTITY[gpp_id]
        else:
            kind, number = parse_title(rec.get("t"))
        items.append(GppItem(
            gpp_id=gpp_id,
            title=rec.get("t") or "",
            date_published=rec.get("dp"),
            creators=rec.get("cr") or "",
            description=rec.get("desc") or "",
            fileset_ids=tuple(_fileset_ids(rec)),
            kind=kind,
            number=number,
            rescued=rescued,
        ))
    return items


@dataclass
class OrderPlan:
    """One distinct order's integration plan (may bundle several GPP items)."""

    key: tuple[str | None, bool, str | None]
    disposition: str
    items: list[GppItem]
    corpus_eo_ids: list[str] = field(default_factory=list)
    eo_id: str | None = None       # target eo_id (minted, or the matched corpus id)
    year: int | None = None
    already_integrated: bool = False

    @property
    def fileset_ids(self) -> list[str]:
        """All file-set ids across this order's GPP items, sorted & de-duped."""
        seen: dict[str, None] = {}
        for it in sorted(self.items, key=lambda i: i.gpp_id):
            for fs in it.fileset_ids:
                seen.setdefault(fs, None)
        return list(seen)

    @property
    def gpp_ids(self) -> list[str]:
        return sorted(it.gpp_id for it in self.items)


@dataclass
class ClassifyResult:
    """Outcome of classifying an inventory against the corpus."""

    orders: list[OrderPlan]
    volumes: list[GppItem]
    excluded: list[tuple[str, str]]        # (gpp_id, reason)

    def by_disposition(self) -> dict[str, list[OrderPlan]]:
        out: dict[str, list[OrderPlan]] = {}
        for o in self.orders:
            out.setdefault(o.disposition, []).append(o)
        return out

    def counts(self) -> dict[str, int]:
        c = {d: len(v) for d, v in self.by_disposition().items()}
        c["volume"] = len(self.volumes)
        c["excluded"] = len(self.excluded)
        return c


def _corpus_index(corpus: Iterable[dict]) -> dict[tuple, list[dict]]:
    idx: dict[tuple, list[dict]] = {}
    for r in corpus:
        key = (r["mayor"], bool(r["is_emergency"]), norm_num(str(r["number"])))
        idx.setdefault(key, []).append(r)
    return idx


def classify(
    inventory: Iterable[dict],
    corpus: Iterable[dict],
    *,
    prior_ledger: dict[str, str] | None = None,
) -> ClassifyResult:
    """Group the inventory into distinct orders and assign each a disposition.

    ``prior_ledger`` maps ``eo_id -> disposition`` from a previous integration
    (the sidecar). Any order whose target/matched eo_id is already in the ledger
    keeps that disposition and is flagged ``already_integrated`` — this pins a
    minted net-new order as net-new on re-run (otherwise the record it created,
    now in the corpus with a PDF, would re-classify as ``dual``). That is what
    makes the integration idempotent AND resumable.
    """
    prior_ledger = prior_ledger or {}
    corpus_list = list(corpus)
    idx = _corpus_index(corpus_list)

    excluded: list[tuple[str, str]] = []
    volumes: list[GppItem] = []
    grouped: dict[tuple, list[GppItem]] = {}

    for item in build_items(inventory):
        if item.gpp_id in EXCLUDED_IDS:
            excluded.append((item.gpp_id, "non-EO / no-file item misfiled under the report type"))
            continue
        if item.kind == KIND_VOLUME:
            volumes.append(item)
            continue
        if item.kind == KIND_UNPARSED:
            excluded.append((item.gpp_id, f"unparseable title: {item.title[:80]!r}"))
            continue
        grouped.setdefault(item.match_key, []).append(item)

    orders: list[OrderPlan] = []
    for key, items in grouped.items():
        matched = idx.get(key, [])
        matched_ids = [r["eo_id"] for r in matched]
        year = next((it.year for it in items if it.year is not None), None)

        # Ledger override (idempotency/resumability): if this order was integrated
        # before, its disposition is fixed and its eo_id is the recorded one.
        ledger_hit = next(
            (eid for eid in (matched_ids or [_mint_id(key, year)]) if eid in prior_ledger),
            None,
        )
        if ledger_hit is not None:
            dispo = prior_ledger[ledger_hit]
            # A minted order's OWN record now appears in the corpus; it never had a
            # pre-existing match, so keep corpus_eo_ids empty (else the sidecar would
            # flip [] → [self] on re-run and break idempotency).
            corpus_ids = [] if dispo in _MINT_DISPOSITIONS else matched_ids
            orders.append(OrderPlan(
                key=key, disposition=dispo, items=items,
                corpus_eo_ids=corpus_ids, eo_id=ledger_hit, year=year,
                already_integrated=True,
            ))
            continue

        if matched:
            has_pdf = any(r.get("pdf_path") for r in matched)
            disposition = DUAL if has_pdf else GAP_CLOSER_EXISTING
            eo_id = matched_ids[0]
        elif key in KNOWN_MISSING_KEYS:
            disposition = GAP_CLOSER_MINT
            eo_id = _mint_id(key, year)
        else:
            disposition = NET_NEW
            eo_id = _mint_id(key, year)

        orders.append(OrderPlan(
            key=key, disposition=disposition, items=items,
            corpus_eo_ids=matched_ids, eo_id=eo_id, year=year,
        ))

    orders.sort(key=lambda o: (o.eo_id or ""))
    return ClassifyResult(orders=orders, volumes=volumes, excluded=excluded)


def _mint_id(key: tuple[str | None, bool, str | None], year: int | None) -> str | None:
    """Mint the eo_id for a to-be-created order, or None if the year is unknown."""
    _mayor, is_em, number = key
    if year is None or number is None:
        return None
    return mint_eo_id(year, number, is_em)


# --------------------------------------------------------------------------- #
# Staging validation
# --------------------------------------------------------------------------- #
def staged_pdf_path(staging_dir: str | Path, fileset_id: str) -> Path:
    """Absolute path to a harvested staging file (``gpp-<fileset_id>.pdf``)."""
    name = config.GPP_STAGING_FILENAME_TEMPLATE.format(fileset_id=fileset_id)
    return Path(staging_dir) / name


def is_valid_pdf(path: Path) -> bool:
    """A file is a usable PDF iff it exists, is non-empty, and starts with ``%PDF``."""
    try:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        with path.open("rb") as fh:
            return fh.read(5).startswith(b"%PDF")
    except OSError:
        return False


_STAGED_NAME_RE = re.compile(r"^gpp-(?P<fs>.+)\.pdf$", re.I)


def staged_filesets(staging_dir: str | Path) -> set[str]:
    """The file-set ids actually present in the staging dir (``gpp-<fsid>.pdf``)."""
    out: set[str] = set()
    d = Path(staging_dir)
    if not d.is_dir():
        return out
    for p in d.glob("gpp-*.pdf"):
        m = _STAGED_NAME_RE.match(p.name)
        if m:
            out.add(m.group("fs"))
    return out


@dataclass
class StagingReport:
    """Reconciliation of expected file-set ids vs the staging directory."""

    expected: int
    present: list[str]
    missing: list[str]
    corrupt: list[str]
    extra: list[str]           # staged but not expected (excluded items / leftovers)

    @property
    def present_count(self) -> int:
        return len(self.present)

    @property
    def complete(self) -> bool:
        return not self.missing and not self.corrupt

    def summary(self) -> str:
        return (
            f"{self.present_count} of expected {self.expected} present"
            f" | missing {len(self.missing)} | corrupt {len(self.corrupt)}"
            f" | extra {len(self.extra)}"
        )


def validate_staging(
    fileset_ids: Iterable[str], staging_dir: str | Path
) -> StagingReport:
    """Classify each expected file-set id as present / missing / corrupt, and
    surface EXTRA staged files not in the expected set.

    Tolerates a partially-filled staging dir (the harvest may still be downloading):
    a file that is not yet there is ``missing``, not an error — the caller can
    integrate what is present and resume later. ``extra`` catches the excluded
    items' harvested PDFs and any stray files (reported, never integrated).
    """
    expected = list(dict.fromkeys(fileset_ids))  # de-dupe, preserve order
    present, missing, corrupt = [], [], []
    for fs in expected:
        p = staged_pdf_path(staging_dir, fs)
        if not p.exists():
            missing.append(fs)
        elif is_valid_pdf(p):
            present.append(fs)
        else:
            corrupt.append(fs)
    extra = sorted(staged_filesets(staging_dir) - set(expected))
    return StagingReport(
        expected=len(expected), present=present, missing=missing,
        corrupt=corrupt, extra=extra,
    )


def expected_filesets(result: ClassifyResult) -> list[str]:
    """Every file-set id the integration would place (orders + volumes)."""
    seen: dict[str, None] = {}
    for order in result.orders:
        for fs in order.fileset_ids:
            seen.setdefault(fs, None)
    for vol in result.volumes:
        for fs in vol.fileset_ids:
            seen.setdefault(fs, None)
    return list(seen)


# --------------------------------------------------------------------------- #
# File placement
# --------------------------------------------------------------------------- #
def _repo_rel(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def _place_file(src: Path, dest: Path, *, do_write: bool) -> bool:
    """Copy ``src`` → ``dest`` idempotently. Returns True if bytes were written.

    Skips the copy when ``dest`` already holds identical bytes (size + content),
    so re-runs never rewrite. ``do_write=False`` is a dry-run (no filesystem
    change) that still reports what *would* be written.
    """
    if dest.exists() and dest.stat().st_size == src.stat().st_size \
            and dest.read_bytes() == src.read_bytes():
        return False
    if do_write:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return True


def dual_dest(eo_id: str, year: int, fileset_id: str, sources_dir: Path) -> Path:
    """Parallel-tree destination for a dual GPP copy: ``<sources>/YYYY/<eo_id>--<fsid>.pdf``."""
    return Path(sources_dir) / str(year) / f"{eo_id}--{fileset_id}.pdf"


def volume_dest(fileset_id: str, sources_dir: Path) -> Path:
    """Destination for a parked pre-1974 volume: ``<sources>/volumes/<fsid>.pdf``."""
    return Path(sources_dir) / "volumes" / f"{fileset_id}.pdf"


# --------------------------------------------------------------------------- #
# Integration
# --------------------------------------------------------------------------- #
@dataclass
class IntegrationResult:
    """Aggregate outcome of an integration (or dry-run)."""

    minted: int = 0
    gap_closed: int = 0
    dual_files: int = 0
    volume_files: int = 0
    deferred: list[str] = field(default_factory=list)     # eo_id/gpp_id: file not staged
    files_written: int = 0
    corpus_before: int = 0
    corpus_after: int = 0
    provenance: dict[str, dict] = field(default_factory=dict)
    volumes_manifest: list[dict] = field(default_factory=list)
    records: list[dict] = field(default_factory=list)     # merged corpus (in memory)
    counts: dict[str, int] = field(default_factory=dict)


def _primary_fileset(order: OrderPlan, staging_dir: Path) -> str | None:
    """The first (sorted) file-set id for this order whose staged PDF is valid."""
    for fs in order.fileset_ids:
        if is_valid_pdf(staged_pdf_path(staging_dir, fs)):
            return fs
    return None


def _mint_index_row(order: OrderPlan, primary_fs: str, pdf_relpath: str) -> dict:
    """Build the light index row `parse_record` consumes for a minted order."""
    _mayor, is_em, _num = order.key
    # Title from the primary item (prefer a non-rescued, clean title if present).
    items = sorted(order.items, key=lambda i: (i.rescued, i.gpp_id))
    primary_item = items[0]
    return {
        "eo_id": order.eo_id,
        "number": primary_item.number,
        "year": order.year,
        "is_emergency": is_em,
        "date_signed": primary_item.date_published,
        "title": clean_gpp_title(primary_item.title),
        "source": config.SOURCE_GPP,
        "source_pdf_url": download_url(primary_fs),
        "pdf_path": pdf_relpath,
    }


def _provenance_entry(order: OrderPlan, staging_dir: Path,
                      file_locations: dict[str, str], primary_pdf: str | None) -> dict:
    """Assemble the sidecar provenance entry for one integrated order.

    ``file_locations`` maps each placed file-set id → its repo-relative path (the
    caller knows these); ``primary_pdf`` is the order's primary copy (``pdfs/…`` for
    minted/gap-closer orders, ``None`` for dual). One ``files`` row per (gpp item,
    file-set), recording the documented download URL and whether it is staged yet.
    """
    files = []
    for it in sorted(order.items, key=lambda i: i.gpp_id):
        for fs in it.fileset_ids:
            files.append({
                "gpp_id": it.gpp_id,
                "fileset_id": fs,
                "download_url": download_url(fs),
                "local_path": file_locations.get(fs),
                "staged": is_valid_pdf(staged_pdf_path(staging_dir, fs)),
            })
    return {
        "eo_id": order.eo_id,
        "disposition": order.disposition,
        "corpus_eo_ids": order.corpus_eo_ids,
        "gpp_ids": order.gpp_ids,
        "primary_pdf": primary_pdf,
        "files": files,
    }


def integrate(
    inventory: Iterable[dict],
    corpus: list[dict],
    *,
    staging_dir: str | Path,
    repo_root: str | Path,
    corpus_dir: str | Path,
    sources_dir: str | Path | None = None,
    prior_ledger: dict[str, str] | None = None,
    do_ocr: bool = True,
    ocr_config: OcrConfig | None = None,
    do_write: bool = True,
) -> IntegrationResult:
    """Fold the GPP harvest into ``corpus`` additively; return an :class:`IntegrationResult`.

    ``do_write=False`` computes the full plan and returns the merged records in
    memory WITHOUT touching the filesystem — the dry-run path. Untouched corpus
    records (dual, corpus-only) pass through byte-identical; only gap-closer records
    are re-parsed (they gain text) and net-new/mint records are appended.

    An order whose primary file is not yet staged is DEFERRED (reported, not
    integrated) so a partial harvest can be run now and completed later.
    """
    repo_root = Path(repo_root)
    corpus_dir = Path(corpus_dir)
    sources_dir = Path(sources_dir) if sources_dir else (repo_root / "sources" / "gpp")
    staging_dir = Path(staging_dir)

    result = IntegrationResult(corpus_before=len(corpus))
    plan = classify(inventory, corpus, prior_ledger=prior_ledger)
    result.counts = plan.counts()

    by_eo_id = {r["eo_id"]: i for i, r in enumerate(corpus)}
    merged = [dict(r) for r in corpus]      # shallow copies; untouched → identical
    new_records: list[dict] = []

    for order in plan.orders:
        if order.disposition == DUAL:
            _integrate_dual(order, staging_dir, sources_dir, repo_root, result, do_write)
        elif order.disposition == GAP_CLOSER_EXISTING:
            _integrate_gap_closer(order, staging_dir, sources_dir, repo_root, corpus_dir,
                                  merged, by_eo_id, result, do_ocr, ocr_config, do_write)
        elif order.disposition in _MINT_DISPOSITIONS:
            _integrate_mint(order, staging_dir, sources_dir, repo_root, corpus_dir,
                            by_eo_id, new_records, result, do_ocr, ocr_config, do_write)

    for vol in plan.volumes:
        _integrate_volume(vol, staging_dir, sources_dir, repo_root, result, do_write)

    # Assemble the merged corpus: existing (with in-place gap-closer updates) then
    # new records, sorted by eo_id for a deterministic append (diff-friendly).
    merged.extend(sorted(new_records, key=lambda r: r["eo_id"]))
    # Safety: an additive merge must never shrink the corpus.
    if len(merged) < result.corpus_before:
        raise RuntimeError(
            f"additive merge would shrink corpus {result.corpus_before} -> {len(merged)}"
        )
    result.records = merged
    result.corpus_after = len(merged)

    if do_write:
        _write_corpus(merged, corpus_dir)
    return result


def _integrate_dual(order, staging_dir, sources_dir, repo_root, result, do_write):
    dual_relpaths: dict[str, str] = {}
    for fs in order.fileset_ids:
        src = staged_pdf_path(staging_dir, fs)
        if not is_valid_pdf(src):
            result.deferred.append(f"{order.eo_id} (dual, fs={fs}: not staged)")
            continue
        dest = dual_dest(order.eo_id, order.year or _year_from_eo_id(order.eo_id), fs, sources_dir)
        if _place_file(src, dest, do_write=do_write):
            result.files_written += 1
        result.dual_files += 1
        dual_relpaths[fs] = _repo_rel(dest, repo_root)
    result.provenance[order.eo_id] = _provenance_entry(
        order, staging_dir, dual_relpaths, primary_pdf=None)


def _integrate_gap_closer(order, staging_dir, sources_dir, repo_root, corpus_dir,
                          merged, by_eo_id, result, do_ocr, ocr_config, do_write):
    primary_fs = _primary_fileset(order, staging_dir)
    if primary_fs is None:
        result.deferred.append(f"{order.eo_id} (gap-closer: primary file not staged)")
        return
    idx = by_eo_id.get(order.eo_id)
    if idx is None:
        result.deferred.append(f"{order.eo_id} (gap-closer: corpus record vanished)")
        return
    rec = merged[idx]
    if order.already_integrated and rec.get("pdf_path"):
        # Already closed on a prior run — reconcile files only, record untouched.
        secondary = _reconcile_secondary(order, primary_fs, staging_dir, sources_dir,
                                        repo_root, result, do_write)
        result.provenance[order.eo_id] = _provenance_entry(
            order, staging_dir, {primary_fs: rec["pdf_path"], **secondary},
            primary_pdf=rec["pdf_path"])
        result.gap_closed += 1
        return

    year = int(rec["year"])
    dest = pdf_dest(order.eo_id, year, repo_root / "pdfs")
    src = staged_pdf_path(staging_dir, primary_fs)
    if _place_file(src, dest, do_write=do_write):
        result.files_written += 1
    pdf_relpath = _repo_rel(dest, repo_root)

    row = {
        "eo_id": rec["eo_id"], "number": rec.get("number"), "year": year,
        "is_emergency": bool(rec["is_emergency"]), "date_signed": rec.get("date_signed"),
        "title": rec.get("title"), "source": rec.get("source"),
        "source_pdf_url": rec.get("source_pdf_url"), "pdf_path": pdf_relpath,
    }
    parsed = parse_record(row, repo_root=repo_root, do_ocr=do_ocr, ocr_config=ocr_config)
    merged[idx] = {**parsed.frontmatter, "full_text": parsed.body,
                   "full_text_raw": parsed.raw_body}
    if do_write:
        _write_md(parsed, corpus_dir)

    secondary = _reconcile_secondary(order, primary_fs, staging_dir, sources_dir,
                                    repo_root, result, do_write)
    result.gap_closed += 1
    result.provenance[order.eo_id] = _provenance_entry(
        order, staging_dir, {primary_fs: pdf_relpath, **secondary}, primary_pdf=pdf_relpath)


def _integrate_mint(order, staging_dir, sources_dir, repo_root, corpus_dir,
                    by_eo_id, new_records, result, do_ocr, ocr_config, do_write):
    if order.eo_id is None:
        result.deferred.append(f"{order.key} (mint: no signing year in GPP dp)")
        return
    if order.eo_id in by_eo_id:
        # Already minted on a prior run — reconcile secondary files, skip re-mint.
        primary_fs = _primary_fileset(order, staging_dir)
        secondary = _reconcile_secondary(order, primary_fs, staging_dir, sources_dir,
                                        repo_root, result, do_write) if primary_fs else {}
        existing_pdf = f"pdfs/{order.year}/{order.eo_id}.pdf"
        locations = {primary_fs: existing_pdf, **secondary} if primary_fs else {}
        result.provenance[order.eo_id] = _provenance_entry(
            order, staging_dir, locations, primary_pdf=existing_pdf)
        return
    primary_fs = _primary_fileset(order, staging_dir)
    if primary_fs is None:
        result.deferred.append(f"{order.eo_id} (mint: primary file not staged)")
        return

    dest = pdf_dest(order.eo_id, order.year, repo_root / "pdfs")
    src = staged_pdf_path(staging_dir, primary_fs)
    if _place_file(src, dest, do_write=do_write):
        result.files_written += 1
    pdf_relpath = _repo_rel(dest, repo_root)

    row = _mint_index_row(order, primary_fs, pdf_relpath)
    parsed = parse_record(row, repo_root=repo_root, do_ocr=do_ocr, ocr_config=ocr_config)
    new_records.append({**parsed.frontmatter, "full_text": parsed.body,
                        "full_text_raw": parsed.raw_body})
    if do_write:
        _write_md(parsed, corpus_dir)
    result.minted += 1

    secondary = _reconcile_secondary(order, primary_fs, staging_dir, sources_dir,
                                    repo_root, result, do_write)
    result.provenance[order.eo_id] = _provenance_entry(
        order, staging_dir, {primary_fs: pdf_relpath, **secondary}, primary_pdf=pdf_relpath)


def _reconcile_secondary(order, primary_fs, staging_dir, sources_dir, repo_root,
                         result, do_write) -> dict[str, str]:
    """Place any ADDITIONAL scans of a minted/gap-closed order under sources/gpp/.

    The primary file goes to ``pdfs/``; extra GPP copies (the 278 multi-scan orders)
    are kept under the parallel tree so no file copy is ever dropped (recon §6).
    """
    secondary: dict[str, str] = {}
    year = order.year or _year_from_eo_id(order.eo_id)
    for fs in order.fileset_ids:
        if fs == primary_fs:
            continue
        src = staged_pdf_path(staging_dir, fs)
        if not is_valid_pdf(src):
            continue
        dest = dual_dest(order.eo_id, year, fs, sources_dir)
        if _place_file(src, dest, do_write=do_write):
            result.files_written += 1
        secondary[fs] = _repo_rel(dest, repo_root)
    return secondary


def _integrate_volume(vol: GppItem, staging_dir, sources_dir, repo_root, result, do_write):
    local_paths = []
    staged_any = False
    for fs in vol.fileset_ids:
        src = staged_pdf_path(staging_dir, fs)
        dest = volume_dest(fs, sources_dir)
        if is_valid_pdf(src):
            if _place_file(src, dest, do_write=do_write):
                result.files_written += 1
            result.volume_files += 1
            staged_any = True
            local_paths.append(_repo_rel(dest, repo_root))
        else:
            result.deferred.append(f"volume {vol.gpp_id} (fs={fs}: not staged)")
    result.volumes_manifest.append({
        "gpp_id": vol.gpp_id,
        "title": vol.title,
        "date_published": vol.date_published,
        "description": vol.description,
        "fileset_ids": list(vol.fileset_ids),
        "local_paths": local_paths,
        "download_urls": [download_url(fs) for fs in vol.fileset_ids],
        "staged": staged_any,
    })


def _year_from_eo_id(eo_id: str | None) -> int:
    """Extract YYYY from an ``eo_id`` like ``2020-EO-056`` (fallback for path building)."""
    if eo_id and eo_id[:4].isdigit():
        return int(eo_id[:4])
    return 0


# --------------------------------------------------------------------------- #
# Emit — corpus, sidecar, volumes manifest, report
# --------------------------------------------------------------------------- #
def _write_md(parsed: ParsedEO, corpus_dir: Path) -> None:
    md_path = Path(corpus_dir) / parsed.md_relpath
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(parsed), encoding="utf-8")


def _write_corpus(records: list[dict], corpus_dir: Path) -> None:
    corpus_dir = Path(corpus_dir)
    corpus_dir.mkdir(parents=True, exist_ok=True)
    (corpus_dir / "eo.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def write_provenance(result: IntegrationResult, corpus_dir: str | Path) -> Path:
    """Write the committed GPP provenance sidecar (deterministic, idempotent)."""
    path = Path(corpus_dir) / config.GPP_PROVENANCE_JSON_NAME
    payload = {
        "generated_by": "scripts/run_gpp_integration.py",
        "source": "DORIS Government Publications Portal (a860-gpp.nyc.gov)",
        "orders": {eid: result.provenance[eid] for eid in sorted(result.provenance)},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def write_volumes_manifest(result: IntegrationResult, sources_dir: str | Path) -> Path:
    """Write the pre-1974 volumes manifest (parked, no records this phase)."""
    path = Path(sources_dir) / "volumes.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": "Pre-1974 bound compilations, parked. Per-order splitting is a "
                "later phase (recon report §3). No corpus records minted here.",
        "volumes": sorted(result.volumes_manifest, key=lambda v: v["date_published"] or ""),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def provenance_ledger(corpus_dir: str | Path) -> dict[str, str]:
    """Load ``{eo_id: disposition}`` from a prior sidecar (empty if none)."""
    path = Path(corpus_dir) / config.GPP_PROVENANCE_JSON_NAME
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {eid: e.get("disposition") for eid, e in data.get("orders", {}).items()}


# Accountability-language correction (recon report §5) carried into the report so
# the committed deliverable states it, not just the research doc.
ACCOUNTABILITY_NOTE = (
    "Every one of the 53 current-era orders missing from the Mayor's own web "
    "surfaces exists in the City's records system: DORIS's Government Publications "
    "Portal held them all along. The earlier \"never publicly retrievable\" "
    "language is retired. The gap is not **preservation** — DORIS preserved them — "
    "it is **publication and compilation**: no single, complete, machine-readable "
    "§ 3-113.1 compilation exists on any official surface."
)


def render_report(
    plan: ClassifyResult,
    staging: StagingReport,
    *,
    corpus_before: int,
    result: IntegrationResult | None = None,
    report_date: str = "2026-07-17",
) -> str:
    """Render the committed GPP integration report (Markdown).

    Works for both the pre-merge dry-run (``result=None``) and the post-merge write
    (``result`` present). Includes the disposition summary, the staging
    reconciliation, the corpus-count math, the gaps-after-GPP narrative, and the
    accountability-language correction (recon report §5, task item 6).
    """
    counts = plan.counts()
    minted = counts.get(NET_NEW, 0) + counts.get(GAP_CLOSER_MINT, 0)
    corpus_after = corpus_before + minted

    L: list[str] = []
    L.append("# GPP integration report — DORIS Government Publications Portal (Phase D)")
    L.append("")
    L.append(f"**Report generated:** {report_date}  |  "
             f"**Source:** DORIS Government Publications Portal (a860-gpp.nyc.gov)")
    L.append("")
    L.append("Deterministic, offline fold of the GPP harvest into the corpus "
             "(no network, no cloud, no LLM). Dispositions re-derived from the "
             "committed inventory + corpus; the harvest manifest's class field is "
             "advisory only.")
    L.append("")

    L.append("## Disposition summary (distinct orders)")
    L.append("")
    L.append("| disposition | orders | action |")
    L.append("|---|---:|---|")
    L.append(f"| net-new | {counts.get(NET_NEW, 0)} | mint record, primary PDF → `pdfs/` |")
    L.append(f"| gap-closer (mint) | {counts.get(GAP_CLOSER_MINT, 0)} | mint record (known-missing), primary PDF → `pdfs/` |")
    L.append(f"| gap-closer (existing) | {counts.get(GAP_CLOSER_EXISTING, 0)} | attach PDF to existing no-pdf record, re-parse |")
    L.append(f"| dual | {counts.get(DUAL, 0)} | 2nd lineage → `sources/gpp/`, record byte-identical |")
    L.append(f"| volume | {counts.get('volume', 0)} | park pre-1974 compilation → `sources/gpp/volumes/` |")
    L.append(f"| excluded | {counts.get('excluded', 0)} | non-EO / no-file items, skipped |")
    L.append("")
    L.append(f"**Corpus:** {corpus_before} → **{corpus_after}** "
             f"(+{minted} minted: {counts.get(NET_NEW, 0)} net-new + "
             f"{counts.get(GAP_CLOSER_MINT, 0)} gap-closer mints). "
             f"No-file records drop from {counts.get(GAP_CLOSER_EXISTING, 0)} "
             "toward ~1 (Bloomberg EO 59).")
    L.append("")

    L.append("## Staging reconciliation")
    L.append("")
    L.append(f"Expected file-set ids to place: **{staging.expected}** — "
             f"{staging.summary()}.")
    if staging.missing:
        L.append("")
        L.append(f"- **Not yet staged ({len(staging.missing)}):** the harvest may "
                 "still be downloading; these orders are DEFERRED and integrate on "
                 "a later re-run (resumable).")
    if staging.corrupt:
        L.append(f"- **Corrupt ({len(staging.corrupt)}):** present but not a valid "
                 "PDF (empty or no `%PDF` header) — re-download before merging.")
    if staging.extra:
        L.append(f"- **Extra ({len(staging.extra)}):** staged but not integrated — "
                 "the excluded items' harvested files + any leftovers; reported, "
                 "never placed.")
    L.append("")
    if result is not None:
        L.append("### This run")
        L.append("")
        L.append(f"- minted records: **{result.minted}**")
        L.append(f"- gap-closer records filled: **{result.gap_closed}**")
        L.append(f"- dual copies placed: **{result.dual_files}**")
        L.append(f"- volume files placed: **{result.volume_files}**")
        L.append(f"- files written: **{result.files_written}**")
        L.append(f"- deferred (file not staged): **{len(result.deferred)}**")
        L.append(f"- corpus: **{result.corpus_before} → {result.corpus_after}**")
        L.append("")

    L.append("## Gaps after GPP integration")
    L.append("")
    L.append("- **Bloomberg EO 59** — the sole missing numbered order 1962–present "
             "outside the volumes; absent from GPP too. Chased only via Municipal "
             "Archives / Law Dept FOIL.")
    L.append("- **Both Phase-C dangling supersession targets close:** 2018-EO-031 "
             "and 2020-EO-056 are now real records with text — re-run "
             "`scripts/run_supersede.py` to resolve them.")
    L.append("- **Pre-1974 (14 volumes)** — parked under `sources/gpp/volumes/`; "
             "per-order splitting (bookmark/index segmentation + OCR) is a later "
             "phase, not done here.")
    L.append("")
    L.append("## Accountability correction")
    L.append("")
    L.append(f"> {ACCOUNTABILITY_NOTE}")
    L.append("")
    return "\n".join(L) + "\n"
