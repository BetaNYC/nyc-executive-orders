# nyc-executive-orders

> 🚧 **Work in progress.** This project is in process: the corpus is usable today, but text quality, metadata, and coverage are still being refined, and data shapes may change. Nothing here should yet be treated as a complete or authoritative record. [Issues](../../issues) and contributions welcome.

An open, complete, machine-readable archive of **New York City mayoral executive orders** — the public compilation the City is _legally required_ to maintain.

> **Status: the archive is live.** The full 1974–present corpus — **2,291 orders** as per-EO Markdown + bulk JSON, backed by 2,291 source PDFs plus 2,435 second-source scans from DORIS's Government Publications Portal — is published in [`corpus/`](corpus/). The 2014–2021 (de Blasio) cohort, previously an eight-year hole, was backfilled from the Internet Archive in July 2026 ([Phase B.4](#phase-b4--de-blasio-era-backfill-20142021)) and is now complete (91/91). The GPP integration ([Phase D](#phase-d--doris-gpp-integration)) closed all but 2 known-missing numbered orders. The supersession graph is populated deterministically from the corpus text ([Phase C](#phase-c--supersession-graph)). **All 14 pre-1974 bound volumes are now OCR'd** — 2,936 pages of image scan, 1946–1973 — and split into **978 additional per-order records** published separately as `corpus/eo_pre1974.json` ([Phase E](#phase-e--pre-1974-volume-split)). Coverage and text are still being refined (OCR cleanup, metadata backfill, pre-1974 segmentation review), and known gaps and limits are documented, not hidden. See [Status](#status).

Vibe coded with [Claude](https://claude.ai) by [BetaNYC](https://beta.nyc).

---

## Why this exists

Mayoral executive orders shape how New York City government actually operates — who reports to whom, which offices exist, how agencies handle data, tenants, emergencies, and procurement. Yet they sit _outside_ the City's laws, rules, and codes infrastructure, and they have never been reliably available as open, machine-readable data.

**New York City law already requires that they be.** NYC Administrative Code **§ 3-113.1** (added by Local Laws 2020/078 and 2022/040, in effect since June 2023) directs the Corporation Counsel to publish, on a single page of the City's website, _"a true and complete compilation of all mayoral executive orders."_ The statute is, in effect, a specification for this project:

| The law requires…    | § 3-113.1                                                            |
| -------------------- | -------------------------------------------------------------------- |
| **Completeness**     | every mayoral executive order issued on or after **January 1, 1974** |
| **One place**        | a single page on the City's website                                  |
| **Machine-readable** | a searchable format, downloadable in **bulk**                        |
| **Supersession**     | each order annotated where a later order amended or superseded it    |
| **Currency**         | each new order posted **within one business day** of signing         |
| **Open**             | free of charge                                                       |

That date — **1974** — is why this project's record begins there. It's the line the law draws.

### The central question

Since the law has required this since June 2023, the first question isn't "how do we build it?" — it's **does the mandated compilation already exist, and does it actually work?**

- **If it exists and meets the spec** → we mirror it, verify it, and this project is largely a preservation-and-access layer on top of the City's own data.
- **If it's missing, broken, or incomplete** → this project becomes both the fix _and_ an accountability record: documenting, order by order, the distance between what the law requires and what the public can actually get.

Early signs point toward the second case. A 2026 redesign of nyc.gov **removed** the historical executive-order PDFs that had long lived under `nyc.gov/html/records/` — _after_ the law took effect. The current Mayor's Office site publishes only orders from roughly 2022 onward; the deepest historical collection sits in the **NYC Municipal Archives**, catalogued at the folder level with no open, per-order metadata. No single, complete, bulk-downloadable, machine-readable compilation is presently known to be available to the public. Confirming that — precisely, and on the record — is this repository's first task.

**This repository is BetaNYC's effort to build that compilation** — openly, in full, and to the standard the law already describes.

---

## What's in scope

- **Regular mayoral executive orders, 1974–present** — the compilation the law names. (~943 known to exist across five administrations, based on Internet Archive holdings.)
- **Emergency executive orders (EEOs)** — tracked as a separate series (they number in the thousands, largely because emergencies are renewed every few days). _(Scope under discussion.)_
- For each order: a stable identifier, date signed, issuing mayor/administration, title, subject tags, supersession relationships, full text, and a link to the source document.
- **Pre-1974 orders and memoranda (1946–1973)** — outside what § 3-113.1 mandates, but recoverable from DORIS's 14 bound compilations, so they are captured and published as a separate set ([Phase E](#phase-e--pre-1974-volume-split): 978 records, all 14 volumes OCR'd).

Out of scope: state (gubernatorial) executive orders; agency rules and the Administrative Code (see [`nyc-charter-laws-rules`](https://github.com/BetaNYC/nyc-charter-laws-rules)).

---

## What's in this repository

| Path                                                                         | What it is                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`corpus/`](corpus/)                                                         | **The published dataset** — 2,291 orders. One Markdown file per order (`corpus/YYYY/<eo_id>.md`: YAML frontmatter + full text) plus the bulk `corpus/eo.json`. `corpus/supersession.json` is the edge list from [Phase C](#phase-c--supersession-graph) (244 edges); `corpus/gpp_provenance.json` is the [Phase D](#phase-d--doris-gpp-integration) sidecar mapping each order to its GPP source item(s) — kept out of `eo.json` so the locked record schema holds. [Phase E](#phase-e--pre-1974-volume-split) adds the pre-1974 set alongside — **978 records, all 14 volumes**: `corpus/eo_pre1974.json` + `corpus/pre1974_provenance.json`, with the Markdown in `corpus/1946/`…`corpus/1973/`. `corpus/mentions.json` is the agency-name scan from [`lineage/`](lineage/) — 15,164 registry-confirmed mentions, 8,340 proposed ones, each with exact character offsets into `full_text`, plus the registry record for all 178 agencies matched. It is what `nyc-eo-explorer` builds its agency pages from.                                                                                                                                                           |
| [`pdfs/`](pdfs/)                                                             | **Primary source PDFs**, one per order (`pdfs/YYYY/<eo_id>.pdf`, git-LFS) — what every corpus record's text was extracted or OCR'd from.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| [`sources/gpp/`](sources/gpp/)                                               | **Second-source lineage from DORIS's Government Publications Portal.** `sources/gpp/YYYY/<eo_id>--<fileset_id>.pdf` (2,435 files) holds a GPP-scanned copy for orders that already have a primary PDF elsewhere — kept for provenance diversity, not as the record of truth (some orders have 2–3 GPP scans on file). [`sources/gpp/volumes/`](sources/gpp/volumes/) holds the 14 bound pre-1974 compilation scans (1946–1973, image-only) — human-readably named and indexed in [its own README](sources/gpp/volumes/README.md) — plus `volumes/ocr/`, the committed per-page OCR output [Phase E](#phase-e--pre-1974-volume-split) splits into records (now complete: all 14 volumes, 2,936 pages). `sources/gpp/inputs/` is the committed GPP inventory + overlap-analysis snapshot the integration re-derives from, so the merge is reproducible and CI-checkable. |
| [`src/nyc_executive_orders/`](src/nyc_executive_orders/)                     | The Python package: harvesters (`fetch.py`, `enumerate.py`, `download.py`), the parse pipeline (`textlayer.py`, `extract.py`, `ocr.py`, `enrich.py`, `clean.py`, `build_corpus.py`), the supersession engine (`supersede.py`), the GPP integration (`gpp.py`), and the pre-1974 volume split (`vlm_ocr.py`, `volume_split.py`, `build_pre1974.py`).                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| [`scripts/`](scripts/)                                                       | Supervised entry points for each live harvest phase, plus the offline `run_parse.py`, `run_supersede.py`, and the Phase E pair (`run_volume_ocr.py`, `run_pre1974_build.py`) plus its `run_picture_clips.py` / `build_web_crops.py` side-cars. `run_full_vlm_pipeline.sh` (repo root) drives the Phase E stages end-to-end, one volume at a time. All live-network scripts are gated — see each Phase section below before running one. Phase E is not: it is local compute, not a harvest.                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| [`tests/`](tests/)                                                           | 496 offline tests (no live network calls); a real-data cross-check runs against the committed corpus + GPP inventory as a regression guard.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| [`qa-ui/`](qa-ui/)                                                           | A local React+Vite app for reviewing [Phase E](#phase-e--pre-1974-volume-split) VLM OCR output — page image with bboxes, rendered Markdown, and the QA signals (ink coverage, token confidence, finish reason) side by side. Reads the committed `sources/gpp/volumes/ocr/` directly, so it works from a fresh clone; see [its own README](qa-ui/README.md).                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `gpp_integration_report.md`, `supersession_report.md`, `pre1974_report.md`   | Generated summaries from the latest merge/supersede runs — regenerated on every run, not hand-edited.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| [`sample_clean_report/review_queue.md`](sample_clean_report/review_queue.md) | **A live worklist** — orders whose title the automated cleaner couldn't confidently extract and needs a human to set by hand from the source PDF. See [Want to help?](#want-to-help) below.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |

---

## The data landscape (preliminary)

| Source                                       | Coverage              | Format                                  | Notes                                                                                                                         |
| -------------------------------------------- | --------------------- | --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Live nyc.gov Mayor's Office                  | ~2022–present         | HTML pages + PDFs                       | Filterable listing; PDFs often lack a reliable text layer (scanned)                                                           |
| Internet Archive (Wayback), `/html/records/` | 1974–~2013            | Archived PDFs                           | Recovers the historical set removed from nyc.gov; ~801 orders in one CDX query (Phase B)                                      |
| Internet Archive (Wayback), `/assets/home/`  | 2014–2021 (de Blasio) | Archived PDFs                           | The years neither the live API (≥2022) nor `/html/records/` (≤2013) reached; 72 regular + 270 emergency recovered (Phase B.4) |
| NYC Municipal Archives / DORIS               | 1600s–present         | Finding aids, microfilm, some digitized | Largest collection; folder-level metadata only; access-restricted                                                             |

Everything before ~2002 is scanned images requiring OCR; later orders are a mix of clean and scanned files.

---

## Roadmap

- [x] **Verify** whether the § 3-113.1 mandated compilation currently exists and is usable — _verified 2026-07-15: it does not. No compliant single-page, bulk-downloadable, 1974-complete compilation exists on nyc.gov._
- [x] **Gather** all available orders locally (live nyc.gov ✅ Phase A + Wayback historical set ✅ Phase B), respecting each source's access rules.
- [x] **Parse** PDFs to text (born-digital extraction with a local OCR fallback for scans).
- [x] **Structure** a clean, machine-readable corpus with metadata.
- [x] **Publish** the corpus (bulk-downloadable JSON + human-readable Markdown, matching the BetaNYC pattern).
- [x] **Annotate supersession** (`supersedes` / `superseded_by` / `in_effect` / `establishes_entity`) — deterministic, rule-based extraction from the corpus text ([Phase C](#phase-c--supersession-graph)); metadata backfill continues.
- [x] **Cross-verify against the City's own deposits** — DORIS Government Publications Portal integration ([Phase D](#phase-d--doris-gpp-integration)): 79 net-new orders recovered, 73 of 74 previously-unrecoverable gaps closed, dual-provenance scans added for 2,129 orders.
- [x] **Split the pre-1974 volumes** (14 bound compilations, 1946–1973) into per-order records — **all 14 volumes OCR'd** (2,936 pages) and split into **978 records** ([Phase E](#phase-e--pre-1974-volume-split)); segmentation review and index reconciliation continue.
- [ ] **Maintain** it forward as new orders are signed.
- [ ] _(Explore)_ an MCP server, and whether this folds into [`nyc-charter-laws-rules`](https://github.com/BetaNYC/nyc-charter-laws-rules).

---

## Phase A harvester (current-era, live nyc.gov)

This repository now ships the **Phase A** harvester: it collects the _current-era_
executive orders (roughly 2022 → present) from live nyc.gov and downloads their
source PDFs, plus a light metadata index. It is deliberately scoped:

**In scope (Phase A):** enumerate EOs (regular **and** emergency) via nyc.gov's
`articlesearch.json` API, resolve each order's source PDF URL from its article
page, download the PDFs (git-LFS), and emit a light metadata index + manifest.

**Deferred (not in this build):** OCR and full-text parsing (**OCR is deferred
pending data context** — we get all the files down first, then decide the OCR
engine and parse depth once we can see what the corpus actually looks like) and
supersession graphs. The historical Wayback backfill (1974 → ~2022) is now built
— see [Phase B](#phase-b-harvester-historical-wayback-backfill).

### How it works

1. **Enumerate** — page `articlesearch.json?types=executive-orders` by year. The
   `title` carries the EO number and the `Emergency` flag; `articleDate` is the
   signing date.
2. **Resolve PDF** — the JSON does not carry the PDF filename, so each article
   page is fetched and its "dam" PDF `<a href>` extracted
   (`.../downloads/pdf/executive-orders/YYYY/<file>.pdf`).
3. **Download** — each PDF is saved to `pdfs/YYYY/<eo_id>.pdf` (git-LFS), skipped
   if already present (idempotent, safe to resume).
4. **Index + manifest** — `index/eo_index.json` + `index/eo_index.csv` (the
   locked light-metadata fields), plus `manifest.csv` and `gaps.md`.

**`eo_id` scheme.** Per-mayor numbering resets, so the raw number isn't unique.
Phase A mints a synthetic id prefixed by signing year and series:
`YYYY-EO-NNN` (regular) / `YYYY-EEO-NNN` (emergency) — e.g. `2024-EEO-718`.

### The WAF / fetch layer

nyc.gov fronts its content with a WAF that rejects plain non-browser HTTP (403).
The fetch layer (`src/nyc_executive_orders/fetch.py`) is an abstraction with two
live backends: a `requests` client sending **browser-like headers** (fast path),
falling back to **headless Playwright** (real browser network stack) on a WAF
block. Playwright is an **optional** dependency (`pip install
'nyc-executive-orders[live]'` + `python -m playwright install chromium`) and is
imported lazily, so the package and its offline tests don't require it. Tests
inject a fake fetcher; an autouse guard blocks real sockets, so the suite never
touches the network.

### Running the harvest

Harvesting makes **live calls to nyc.gov** and must be run by a human under
BetaNYC's go-slow authorization — never by CI or an agent. The supervised entry
point refuses to run without an explicit acknowledgement flag:

```bash
# Download every current-era EO PDF, go-slow (default 2.5s between calls):
python scripts/run_harvest_live.py --from-year 2022 --to-year 2026 \
    --i-am-a-human-running-this-supervised

# Live dry-run first (enumerate + resolve PDF URLs, NO downloads):
python scripts/run_harvest_live.py --from-year 2022 --to-year 2026 --dry-run \
    --i-am-a-human-running-this-supervised
```

The library CLI (`python -m nyc_executive_orders harvest --from-year 2022
--to-year 2026`) defaults to a dry-run; `--download` opts into fetching.

**Exit codes** (both the supervised runner and the library CLI): `0` on a fully
clean run, `1` if the run completed but hit any errors (so `&&` chaining and CI
gate on "finished clean"), and — for `run_harvest_live.py` — `2` when the
`--i-am-a-human-running-this-supervised` flag is missing.

### Dry-run pipeline wrapper

`scripts/run_pipeline.py` chains a sequence of **dry-run** validation passes and
then **halts before any download**. It advances to the next step only if the
previous finished clean (exit 0 and zero errors); on a failing step it stops
immediately and skips the rest. After all steps pass it prints a consolidated
inventory (per-step enumerated / resolved / errors, plus pointers to `index/`,
`manifest.csv`, `gaps.md`) and the exact manual command to run the real download.
It never downloads anything itself. Same human gate as the live runner.

```bash
python scripts/run_pipeline.py --i-am-a-human-running-this-supervised
```

The default sequence is a `2024` dry-run (high-volume Adams-era validation) then
a `2022-2026` dry-run (full current-era inventory); edit `DEFAULT_STEPS` at the
top of the script to change it.

## Phase B harvester (historical Wayback backfill)

The 2026 nyc.gov redesign **removed** the historical executive-order PDFs that had
long lived under `nyc.gov/html/records/pdf/executive_orders/`. Phase B recovers
them from the **Internet Archive (Wayback Machine)**, covering 1974 → ~2022, and
merges them into the current-era corpus.

**In scope (Phase B):** enumerate the historical EO PDF captures on Wayback,
parse each captured filename into an `eo_id`, download the archived PDFs
(git-LFS, same `pdfs/YYYY/<eo_id>.pdf` layout), and merge into the index —
preferring the live-nycgov rows on any collision. Fetch + index only; **OCR stays
deferred**, same as Phase A.

### The Wayback engine (a dependency, not a reimplementation)

Phase B does **not** reimplement Wayback logic. The engine is BetaNYC's
[`ny-gov-web-archiver`](https://github.com/BetaNYC/ny-gov-web-archiver) — a
throttled EDGI-[`wayback`](https://github.com/edgi-govdata-archiving/wayback)
orchestrator (CDX enumeration, go-slow memento fetch, `Retry-After`/429 backoff).
It is wired here as an **editable path dependency** to the sibling `~/Code/`
checkout (see `[tool.uv.sources]` in `pyproject.toml`); its `wayback` pin rides in
transitively. Phase B is the EO-specific layer on top: the URL prefix, the
`filename → eo_id` parse, the `pdfs/YYYY/` layout, and the prefer-live merge.

### How it works

1. **Enumerate** — one CDX **prefix** query (via the archiver) over
   `nyc.gov/html/records/pdf/executive_orders/`, filtered to `application/pdf` +
   HTTP 200. We enumerate what CDX returns and parse each captured filename; the
   documented old pattern is `YYYYEO0NN.pdf` (regular) / `YYYYEEO0NN.pdf`
   (emergency). A filename that doesn't parse is **flagged, never dropped**.
2. **Select** — one capture per source URL (the latest snapshot).
3. **Parse + mint** — `filename → eo_id`, reusing Phase A's identity scheme
   (`YYYY-EO-NNN` / `YYYY-EEO-<label>`).
4. **Fetch** — download each archived PDF (go-slow) to `pdfs/YYYY/<eo_id>.pdf`,
   skip-if-present (idempotent).
5. **Merge** — combine with the existing `index/eo_index.json`, dedup by `eo_id`.
   An order present from **both** `live-nycgov` and `wayback` keeps the live row
   (fresher, richer metadata) and drops the wayback duplicate (logged). The merged
   `manifest.csv` + `gaps.md` then span the full 1974 → present corpus.

### Running the Wayback harvest

Same **go-slow** posture and same human/operator authorization gate as Phase A —
Internet Archive is a nonprofit on constrained infrastructure; the archiver's
client is throttled below IA's shared ~30 req/min budget and `--delay` adds
further margin (default 2.5s between downloads).

```bash
# Live dry-run first (enumerate + parse + merge, NO downloads):
python scripts/run_wayback_harvest_live.py --from-year 1974 --to-year 2022 \
    --dry-run --i-am-a-human-running-this-supervised

# Real download (go-slow) of the historical set:
python scripts/run_wayback_harvest_live.py --from-year 1974 --to-year 2022 \
    --i-am-a-human-running-this-supervised
```

Exit codes match the Phase A runner: `0` clean, `1` completed with fetch errors,
`2` when neither authorization gate flag is present.

## Phase B.2 — current-era gap recovery (Wayback)

A subset of the current-era (Phase A) orders are recorded in the index but have
**no PDF on disk**: the live-nycgov harvest saw the order, but its PDF URL 404'd
(the file was pulled from live nyc.gov), pointed at an internal host the public
can't reach (`nyc-csg-web.csc.nycnet`), was served from an alternate edge
(`www1.nyc.gov`), or was never resolved at all. Phase B.2 recovers those PDFs
from the Internet Archive.

For each gap it derives an **ordered list of public-equivalent candidate URLs**
and tries them in order, stopping at the first with a usable snapshot:

1. **DAM candidate** — the recorded URL host-normalized to `www.nyc.gov`, or, for
   the one order with no recorded URL, reconstructed from the documented DAM path
   shape (`/content/dam/.../executive-orders/{year}/{file}.pdf`).
2. **Legacy-assets candidate** — the same `{year}/{filename}` tail under the
   pre-redesign path `/assets/home/downloads/pdf/executive-orders/{year}/{file}`
   (`{filename}` = the recorded URL's basename lowercased, or the reconstructed
   `<series>-<number>.pdf`). A CDX prefix sweep found **24 of the 59** gap orders
   archived only here (as `www1.nyc.gov` 200 `application/pdf`) — their DAM URL has
   just 404 text/html captures. CDX urlkey canonicalization folds `www`/`www1`
   together, so the `www.nyc.gov` form of this path matches the `www1` captures.

For each candidate it queries Wayback for an **exact-URL** `application/pdf`
snapshot (newest wins); the first candidate that yields one wins (later candidates
are not queried). It downloads the matched snapshot go-slow, **validates the bytes
are really a PDF** (magic-byte check; a soft-404 HTML interstitial is rejected),
and stamps the row `source: "wayback-gap"`. The row keeps its original recorded
URL; the matched candidate and the Wayback playback URL of the recovered bytes are
surfaced in the gap-recovery report + logs, so which route recovered each order
stays auditable. Orders still missing after the pass are listed in `gaps.md` under
**"Unrecoverable after Wayback pass"** with a per-order reason (no snapshot for
either the dam or legacy-assets candidate / snapshot not a PDF / fetch error).

This pass also fixes an index-truthfulness bug: the Phase-B merge could leave
`pdf_path: null` on rows whose PDF was actually on disk. `reconcile_pdf_paths`
now backfills `pdf_path` from disk before computing the gap set, so the only rows
reported as missing are the ones truly absent (currently **59**).

### Running gap recovery

Same **go-slow** posture and the same human/operator authorization gate as the
other live runners.

```bash
# Live dry-run first (find Wayback snapshots, NO downloads):
python scripts/run_gap_recovery_live.py --dry-run \
    --i-am-a-human-running-this-supervised

# Real recovery (go-slow) of every gap:
python scripts/run_gap_recovery_live.py \
    --i-am-a-human-running-this-supervised
```

Exit codes match the other runners: `0` clean, `1` completed with lookup/fetch
errors, `2` when neither authorization gate flag is present.

## Phase B.4 — de Blasio-era backfill (2014–2021)

Every executive order signed **2014–2021** (all of Mayor de Blasio's) fell into a
harvest gap: the live source (Phase A, `articlesearch.json`) reaches back only to
~2022, and the historical Wayback path (`/html/records/...`) stops at 2013 — so
**neither side ever queried those years**, and the entire cohort was absent from
the corpus. (A 2022+ order revoking "Executive Order No. 31, dated March 7, 2018"
cited a target that did not exist in the dataset.)

A 2026-07-16 CDX discovery located the de Blasio EO PDFs on the Internet Archive
under the **pre-redesign `/assets/home/...` path**, with the signing **year in the
directory** (not the filename) and a series+number filename whose separator drifts
by era:

```
www.nyc.gov/assets/home/downloads/pdf/executive-orders/{year}/{eo|eeo}[-_]{n}.pdf
    e.g.  2014/eeo_1.pdf   2018/eo-34.pdf   2021/eeo-173.pdf
```

This is the **same** `/assets/home/...` root Phase B.2 uses for current-era gap
recovery, but a **different filename convention** (Phase B.2's 2022+ gap files are
`eeo-290.pdf`; de Blasio's are `eo_34.pdf`) — a per-era trap the parser handles
explicitly. A live `articlesearch.json` probe of 2016/2018/2021 returned zero
results, confirming the live API cannot supply these years; **Wayback is the only
source.** Non-EO documents that ride the same directory (Mayoral Personnel Orders
`mpo-*.pdf`, election proclamations) are flagged, never minted as EOs.

Phase B.4 reuses the Phase B machinery — the `ny-gov-web-archiver` engine, the
`eo_id` mint scheme, the `pdfs/YYYY/<eo_id>.pdf` layout, and the prefer-live merge
— differing only where the path demands it: (1) the `/assets/home/...` prefix;
(2) **no capture-year filter** (a 2014 order may only be archived years later, so
the window is applied by the year parsed from the URL _path_); (3) **host-duplicate
collapse by minted identity** — the same file is archived under both `www.nyc.gov`
and `www1.nyc.gov`, so captures are folded to one per `eo_id` before rows are built
(otherwise every www/www1 pair would read as a same-id conflict); (4) the
year-in-path parser and a distinct `source: "wayback-deblasio"` provenance tag.

CDX found the regular EO numbers forming a clean **1..91 sequence** across the term
(**72 of 91 archived**; the 19 never captured — including EO 31 — are listed in
`gaps.md`, not dropped) plus the emergency (EEO) series on the same path. The one
supervised harvest recovers **72 regular + 270 emergency** de Blasio orders.

### Running the de Blasio backfill

Same **go-slow** posture and the same human/operator authorization gate as the
other live runners.

```bash
# Live dry-run first (enumerate + parse + merge, NO downloads):
python scripts/run_deblasio_harvest_live.py --dry-run \
    --i-am-a-human-running-this-supervised

# Real go-slow download of the 2014–2021 set:
python scripts/run_deblasio_harvest_live.py \
    --i-am-a-human-running-this-supervised
```

Exit codes match the other runners: `0` clean, `1` completed with fetch errors,
`2` when neither authorization gate flag is present.

### Development

```bash
uv run --with pytest python -m pytest        # offline test suite (no network)
uv run --with pytest python -m pytest -v     # verbose
```

Tests run on a Python 3.11 / 3.14 matrix in CI (`.github/workflows/tests.yml`).
Source PDFs are git-LFS-tracked (`.gitattributes`, `pdfs/**`); run `git lfs
install` once in a fresh clone.

## Parse → publishable corpus

Once the source PDFs are gathered, a **six-stage pipeline** (`src/nyc_executive_orders/`,
run via `scripts/run_parse.py`) turns them into the published corpus in [`corpus/`](corpus/):
one Markdown file per order (`corpus/YYYY/<eo_id>.md` — YAML frontmatter + full text), plus a
bulk `corpus/eo.json`.

1. **Text-layer probe** (`textlayer.py`) — classify each PDF as born-digital vs scanned (decides what needs OCR).
2. **Extract** (`extract.py`) — PyMuPDF full text for born-digital PDFs.
3. **OCR** (`ocr.py`) — local `ocrmypdf`/Tesseract for scanned PDFs. **Hard cloud gate: local only, no network, no cloud fallback ever** — unreadable pages are flagged for review, never auto-escalated.
4. **Enrich** (`enrich.py`) — derive `mayor` / `administration` from the signing year.
5. **Clean** (`clean.py`) — deterministic, non-destructive cleanup of OCR'd docs: relocate scan-stamp and letterhead noise out of the body (into `dropped_header` / `dropped_marks`, never deleted), and backfill `title` / `date_signed` from the body **only** when a frozen-dictionary gate confirms every word — otherwise the field is left empty and flagged for human review. **No stage ever rewrites the order text**; the verbatim OCR is preserved in `full_text_raw`.
6. **Emit** (`build_corpus.py`) — write the per-EO Markdown, bulk JSON, and manifest.

Every record carries a `text_source` (`born-digital` / `ocr` / none) and a `text_quality`
tier (`clean` / `minor-noise` / `needs-review`) so consumers know exactly what they are
getting. Supersession annotations are populated by [Phase C](#phase-c--supersession-graph).

## Phase C — supersession graph

The four graph fields the schema reserves (`supersedes`, `superseded_by`, `in_effect`,
`establishes_entity`) are populated by a **deterministic, rule-based** post-process
(`src/nyc_executive_orders/supersede.py`, run via `scripts/run_supersede.py`) — the same
discipline as the clean stage: **no LLM, no network**, every edge traceable to a literal
citation in the corpus text. This is what makes the corpus answer _"what's still in force?"_ —
the supersession annotation § 3-113.1 requires.

- **Citations resolve year-scoped.** `Executive Order No. {n}, dated {Month} {day}, {year}, is
hereby REVOKED` (and rescinded / superseded / repealed / amended) resolves to the cited
  _date's_ year plus the series (`Emergency` ⇒ EEO, else EO), never the number alone —
  per-mayor numbering resets and emergency numbers collide across administrations.
- **Two edge sources**, tagged in the edge list: `body-citation` (the containing order is the
  actor) and `header-xref` (the OCR'd archival `XREF: AMENDED BY 'EO 18) 1978'` stamp, where
  the citing order is the actor).
- **`in_effect` is conservative and regular-only.** `false` when a resolvable in-corpus order
  _wholly_ revokes/supersedes it (a section-scoped partial repeal or an amendment alone does
  not flip it); `null` otherwise (we never assert a 1970s order is still in force). Emergency
  `in_effect` stays `null` in v1 (expiry by operation of law is out of scope), and EEO
  extension chains are not treated as supersession edges.
- **`establishes_entity` auto-writes only exact, unambiguous matches** against the
  [`ny-gov-web-registry`](https://github.com/BetaNYC/ny-gov-web-registry); fuzzy candidates go
  to the report for human review.

Outputs: the four fields written back into `corpus/eo.json` + every `corpus/YYYY/<eo_id>.md`
(record count unchanged), the edge list `corpus/supersession.json` (verb + provenance per
edge), and a human-readable `supersession_report.md`. Idempotent — a re-run yields identical
output. Needs the registry cloned as a sibling (or `--registry <path>`):

```bash
uv run --no-project --with pyyaml python scripts/run_supersede.py --dry-run   # report only
uv run --no-project --with pyyaml python scripts/run_supersede.py             # write in place
```

## Phase D — DORIS GPP integration

The **DORIS Government Publications Portal** ([a860-gpp.nyc.gov](https://a860-gpp.nyc.gov), a
Samvera Hyrax repository) holds the City's deposited copies of executive orders. A browser-session
harvest of every file under Report Type "Executive Orders" (2,587 items) is folded into the corpus
by a **deterministic, offline, additive** integration (`src/nyc_executive_orders/gpp.py`, run via
`scripts/run_gpp_integration.py`) — **no network, no cloud, no LLM** (it reads staged PDFs the
harvest already downloaded). Dispositions are re-derived from the committed inventory + corpus:

- **net-new (79)** and **known-missing gap-closers (20)** → mint a record, primary PDF → `pdfs/`.
  This closes both Phase-C dangling supersession targets (2018-EO-031, 2020-EO-056) and Koch EO 9.
- **gap-closer existing (53)** → attach the GPP PDF to an existing no-pdf record and re-parse for
  text; the record's metadata is byte-preserved.
- **dual (2,129)** → the GPP copy is a _second_ source lineage under `sources/gpp/YYYY/`; the
  primary `pdfs/` file and the corpus record are byte-identical.
- **volumes (14)** → pre-1974 bound compilations parked under `sources/gpp/volumes/` (per-order
  splitting is a later phase); **excluded (7)** non-EO / no-file strays are skipped.

Every touched order's GPP lineage (item ids, file-set ids, download URLs, local paths) lands in a
sidecar, `corpus/gpp_provenance.json` — kept out of `eo.json` so the locked frontmatter schema and
dual byte-identity hold. Idempotent + resumable: the sidecar pins already-integrated orders, so a
re-run over the same (or a more-complete) staging dir never duplicates. The merge carries the same
human/operator authorization gate as the harvest runners; `--dry-run` previews with no gate. When
run, the corpus grows **2,192 → 2,291**.

```bash
# Preview (read-only, no gate) — validate staging + print the plan, write nothing:
uv run python scripts/run_gpp_integration.py --dry-run
# Merge (born-digital only, fast). Gated; --operator-authorized or -i-am-…-supervised:
uv run python scripts/run_gpp_integration.py --no-ocr --operator-authorized
```

## Phase E — pre-1974 volume split

The 14 bound compilations under [`sources/gpp/volumes/`](sources/gpp/volumes/) cover
**1946-01-07 → 1973-11-12** — O'Dwyer, Impellitteri, Wagner, Lindsay — in 2,936 pages of pure
image scan with no text layer. Phase E reads them locally and splits them into per-order records.
**All 14 are now OCR'd and split: 978 records**, published in `corpus/eo_pre1974.json`.

**The OCR is a local vision-language model**, [dots.ocr](https://github.com/rednote-hilab/dots.ocr)
run at temperature 0 (`src/nyc_executive_orders/vlm_ocr.py`) through either of two interchangeable
local backends — `mlx-vlm` on Apple Silicon (the `vlm` extra) or `torch`/`transformers` on an
NVIDIA GPU (the `vlm-cuda` extra, `--device cuda`; setup, quantization and VRAM notes in
[`run-on-cuda.md`](run-on-cuda.md)). The full 14-volume run was driven on an NVIDIA card by
`run_full_vlm_pipeline.sh` at that script's 4-bit default. No cloud OCR, no API, no per-page cost — the same §7 rule the Tesseract path follows. Records it produces are
stamped `text_source: ocr-vlm`, distinct from the `ocr` (ocrmypdf) lineage.

**The model's job ends at pixels → text.** Every downstream decision — where one instrument ends
and the next begins, its number, its date, its subject — is deterministic rule code
(`volume_split.py`) reading literal strings off the page, reusing the same `clean` → `enrich` →
frontmatter stages as every other era. Nothing here infers content that is not printed.

### Setup — only for stage 1 (the OCR)

Everything downstream of the OCR — the segmentation, the corpus build, the picture clips, the QA
UI — reads the **committed** per-page JSON in `sources/gpp/volumes/ocr/` and needs no GPU, no
model, and no extra install. You only need this if you are re-running or extending the OCR
itself:

```bash
uv pip install -e '.[vlm]'        # Apple Silicon (mlx-vlm)
uv pip install -e '.[vlm-cuda]'   # NVIDIA GPU (torch/transformers) — see run-on-cuda.md
```

The two extras are **mutually exclusive** (declared as conflicting in `pyproject.toml`): they run
the same model on hardware that is never the same machine, and their transitive `transformers`
pins don't share a resolvable range. Pick the one that matches the box.

First run downloads the dots.ocr weights into `~/.cache/huggingface`. That one-time fetch is the
**only** network touch in the whole phase — inference itself never calls out, per the same §7
rule the Tesseract path follows.

**Smoke-test one page before committing to a volume** — this is where a bad model load, an API
mismatch, or an OOM shows up in a minute instead of after an hour:

```bash
python -m nyc_executive_orders.vlm_ocr \
    sources/gpp/volumes/1962-01-23_1963-12-26_Wagner_Orders.pdf \
    --device cuda --quantization 4bit --start-page 88 --pages 1 \
    --output-dir /tmp/vlmtest --profile-memory
```

Pick a page with body text on it, not page 1: volume front matter is classified blank and
skipped, which exercises none of the generation path and will happily "pass" against a broken
model load. Check `memory_profile.json`'s `accel_peak_gb` fits your card before starting a real
volume.

`python scripts/run_volume_ocr.py --status` prints per-volume progress (pages OCR'd, whether the
blank-page calibration has been run) any time, including while a job is grinding.

### Running the two stages

Two stages, because the OCR ran ~45–50 hours for all 14 volumes and the post-process has to be
iterable without repeating it:

```bash
# Stage 1 — calibrate blank-page detection on a volume you have not run before.
# Renders and scores pages; loads no model. A false "blank" silently drops an order.
python scripts/run_volume_ocr.py --volume 1962-01-23 --classify-blank-only

# Stage 1 — OCR it (~2 min/page; resumable with --start-page). No authorization
# gate: this is local compute on files already on disk, not a live harvest.
python scripts/run_volume_ocr.py --volume 1962-01-23

# Stage 2 — segment + emit. Offline, idempotent, no GPU; re-run it freely.
uv run python scripts/run_pre1974_build.py --dry-run
uv run python scripts/run_pre1974_build.py
```

`./run_full_vlm_pipeline.sh` chains all of it for one volume — optional blank-page calibration,
then the CUDA OCR pass, then the (always full-corpus) stage-2 build — logging per volume under
`vlm-logs/` and resuming at the first missing page, so the way to grind through 14 volumes is to
keep re-running it:

```bash
./run_full_vlm_pipeline.sh --volume Wagner_Orders
./run_full_vlm_pipeline.sh --volume 1962-01-23 --start-page 88   # resume
./run_full_vlm_pipeline.sh --dry-run                            # print the commands
```

Stage 1 writes each volume's per-page JSON straight into `sources/gpp/volumes/ocr/` — committed,
plain JSON, not LFS — so stage 2 is reproducible by anyone, with no GPU and none of the ~45–50
hours the OCR itself took. A `--classify-blank-only` calibration pass writes its blank/keep verdicts there too, as
`classify_report.json`, so even that pre-OCR judgment call is auditable. That directory is the
only place page records live: pages land in it one at a time as a run proceeds, so what is on
disk is exactly the progress, with no second copy to reconcile against and no publish step that
can lag behind or overwrite newer work. The rest of a run (page PNGs, bbox overlays, memory
profile) stays local scratch under `vlm-ocr-runs/` (gitignored) — regenerable, not source of
truth. Nothing whole-document is stored: `scripts/render_volume_document.py` stitches a volume's
records into Markdown (or one combined JSON array) on demand, since both are functions of the
per-page files.

```bash
uv run python scripts/render_volume_document.py --volume 1962-01-23 | less
```

A volume whose OCR is wrong rather than merely incomplete — as the two sideways-scanned Lindsay
Orders volumes (1968–1969, 1970–1971) were before `--rotate` existed — is redone by clearing it
first: `./run_full_vlm_pipeline.sh --clean --volume 1968-01-10`, which removes its page records,
run scratch, crops, log and corpus records, then exits. Both were re-OCR'd that way; page
orientation now defaults to `--rotate auto`.

**Clip the `Picture` regions out** with [`scripts/run_picture_clips.py`](scripts/run_picture_clips.py).
dots.ocr tags part of each page as `Picture` — city seals, mayoral signatures, the odd real
figure — and the corpus path throws those regions away (`volume_split.py` drops them from
reading order). This cuts each one back out of the original PDF at whatever DPI you ask for, so
the picture layer can be looked at rather than inferred:

```bash
python scripts/run_picture_clips.py --dry-run          # per-volume Picture census
python scripts/run_picture_clips.py --volume 1962-01-23
```

Crops land in `vlm-pics/<volume-stem>/page_NNNN_pic_KK.png` (gitignored — pixels, and
regenerable) alongside a `manifest.json` that joins each crop back to the page JSON it came
from. Crops get a **`_sig` or `_seal` suffix** when they look like the signature at the foot of
an order or the city seal in the letterhead — dots.ocr has one `Picture` category and no more,
so a signature, a seal, the library's date stamp, a scanner artifact and a printed ornament all
arrive indistinguishable. Signatures are found by position (under the body text, where the
others never sit), shape, ink density and place in reading order; seals by shape and size alone
(the seal is near-square and small, where the date stamp is near-square and twice as wide).
Anything neither rule recognises stays untagged rather than being forced into a bucket. On the
O'Dwyer volume: **38 signatures, 56 seals, 15 untagged**, out of 109 pictures. Across all 14
volumes now clipped: **2,147 pictures — 650 signatures, 1,028 seals, 469 untagged**. Every measurement
behind a tag is written to the manifest beside it, so a wrong call is visible rather than baked
in — these are filters for a human, not ground truth, and the thresholds are calibrated on one
volume's scans like the blank-page ones. It reads only the committed
`sources/gpp/volumes/ocr/`, so a volume with no OCR output yet is reported as such rather than
silently skipped. No GPU and no model —
safe to run while an OCR job is grinding. Note `--ocr-dpi` must match the DPI the OCR run
rendered at (200 by default): the model reports coordinates in a resized image space, not in the
rendered page's pixels, and the script undoes that mapping.

**Publish the crops for the web** with
[`scripts/build_web_crops.py`](scripts/build_web_crops.py). The 300 dpi PNGs are the right
format to keep and the wrong one to serve — 472 MB, and the explorer's gallery paints 120 at
once. This re-encodes them to `vlm-pics-web/<volume-stem>/page_NNNN_pic_KK.jpg` at q85, 53 MB
for the same 2,147 crops, with a copy of the manifest whose `image` fields point at the JPEGs:

```bash
python scripts/build_web_crops.py            # all volumes; re-running writes nothing
python scripts/build_web_crops.py --force -v # re-encode from scratch
```

Unlike `vlm-pics/`, **this tree is committed** — as plain git, not LFS. It is what
[nyc-eo-explorer](https://github.com/BetaNYC/nyc-eo-explorer) copies into its GitHub Pages
build, and Pages will not resolve an LFS pointer. Keeping it out of LFS also means that build
can check this repo out without `lfs: true`, so serving the picture layer to the public costs
no LFS bandwidth at all. Encoding is fixed-parameter and skips anything already written, so a
re-run adds no git objects; `--max-edge` (2000px default) downscales the handful of crops where
dots.ocr called most of a page a `Picture`, which is otherwise most of the byte count.

**Review OCR output in [`qa-ui/`](qa-ui/)** — page image (with bboxes) next to rendered
Markdown and the QA signals (ink coverage, token confidence, finish reason), one volume at a
time, reading straight from the committed `sources/gpp/volumes/ocr/`:

```bash
cd qa-ui && npm install && npm run dev
```

**The volumes carry their own subject indexes**, and Phase E reconciles against them: how many
instruments the compilers listed vs how many the segmenter produced, per volume, in
`pre1974_report.md`. That is what turns "did the split work?" from a judgement call into a
number. On the first volume it recovered two instruments whose numbers the OCR missed, and
caught a real `7-A` that would otherwise have collided with `7`.

**Across the full run that check is thinner than the design assumed**, and the report says so
rather than papering over it: index pages parsed into entries on only **3 of the 14 volumes**
(78 entries — the O'Dwyer, Impellitteri–Sharkey and Wagner–Theobald memoranda), so the other 11
volumes' record counts stand on the segmenter alone. Every volume's `Missing` / `Unindexed`
columns, its OCR-failed pages, and its volume-level flags (orphan pages, duplicate numbers,
series taken from the catalogue over the printed labels) are listed per volume in
`pre1974_report.md`. The open items worth knowing before using this set:

- **113 pages produced no usable OCR** out of 2,936, and **94 of them are in one volume** —
  `1968-01-10_1969-12-29_Lindsay_Orders` (389 pages). Every record covering a failed page is
  forced to `needs-review`.
- **Text quality across the 978 records: 653 clean, 40 minor-noise, 285 needs-review.**
- Records by mayor: Lindsay 492, Wagner 386, O'Dwyer 58, Impellitteri 42.
- Index reconciliation on the volumes that have one is not clean either — the O'Dwyer volume
  reports index numbers the segmenter never produced, and every volume reports numbers the
  index does not list. These are findings to chase, not suppressed errors.

Records land in `corpus/YYYY/` with the identical locked frontmatter, but in
**`corpus/eo_pre1974.json`, not `eo.json`** — § 3-113.1 scopes the statutory deliverable to
1974-onward, and these are older, second-source, and OCR'd from bound compilations. Page spans,
QA flags, the verbatim printed label, the signer, and the index's competing date all ride in the
`corpus/pre1974_provenance.json` sidecar, on the Phase D precedent, so the locked 21-field
schema does not move.

**Status:** complete first pass. All **14 volumes are OCR'd** (2,936 pages) and split into
**978 records** spanning 1946-01-07 → 1973-11-12, with the picture layer clipped and published
for all of them. What remains is review, not capture: the 285 `needs-review` records, the 94
unreadable pages in the 1968–1969 Lindsay Orders volume, and the 11 volumes whose subject index
did not parse into a reconcilable list.

## Status

The archive is **live and published**. Phase A (current-era harvester), Phase B (historical
Wayback backfill), Phase B.2 (current-era gap recovery), Phase B.4 (de Blasio-era 2014–2021
backfill), and Phase D (DORIS GPP integration) are all built, offline-tested, and have completed
their live harvest + merge — each live run is a separate, supervised, human-run step. The parse →
corpus pipeline (probe → extract → OCR → enrich → clean → emit) has been run against the full
corpus, and the result — **2,291 orders** — is published in [`corpus/`](corpus/). Phase E
(pre-1974 volume split) is likewise built, offline-tested, and has completed its full OCR run —
local compute, not a harvest — adding **978 more records** for a repository total of **3,269
documents**.

It is the most complete open compilation of NYC mayoral executive orders we know of, but it is
**not yet authoritative**: OCR text of the oldest scans is imperfect (faithful to the source,
not perfected), and while the supersession graph is populated (Phase C: 244 edges, 140 regular
orders computed out of force), metadata backfill continues. The Phase D merge closed all but
**2 known-missing numbered orders** — Bloomberg EO 59 and Adams EEO 471 — confirmed absent from
every source we've checked, including GPP's own deposits; both are now accountability findings
(the City's own records system doesn't have them either) rather than harvest gaps. The de Blasio
regular series is complete (91 of 91 signed orders recovered, including EO 31/2018 and EO
56/2020, which later orders cite as revoked).

**The pre-1974 record now exists too.** All 14 bound volumes (1946–1973) are harvested, and
[Phase E](#phase-e--pre-1974-volume-split) has now OCR'd **every one of them** — 2,936 pages of
image scan, on local GPUs, no cloud — and split them into **978 per-order records**
(1946-01-07 → 1973-11-12; Lindsay 492, Wagner 386, O'Dwyer 58, Impellitteri 42). They are
published as `corpus/eo_pre1974.json`, deliberately separate from the statutory 1974-onward
`eo.json`. This is a **first pass, not a finished set**: 653 of the 978 records are `clean`,
285 are `needs-review`, 113 pages produced no usable OCR (94 of them in a single Lindsay
volume), and only 3 of 14 volumes' subject indexes parsed well enough to reconcile the split
against. All of that is enumerated per volume in `pre1974_report.md`. Gaps are listed, not
hidden. See [Want to help?](#want-to-help) below.

## Want to help?

This is early-stage, openly built infrastructure — there's concrete work ready to pick up, not just "look around and see."

**Two things ready right now:**

- **[`sample_clean_report/review_queue.md`](sample_clean_report/review_queue.md)** — 69 orders whose title the automated cleaner couldn't confidently extract from a scanned PDF. The fix is reading the source PDF and hand-setting the title; no code changes needed. Bodies are already correct and untouched (`full_text_raw` preserves the verbatim OCR) — this is purely a title-review pass.
- **Open issues:** [#1](https://github.com/BetaNYC/nyc-executive-orders/issues/1) (a scanned order misclassified as born-digital) and [#6](https://github.com/BetaNYC/nyc-executive-orders/issues/6) (28 orders with a bad title pulled from a caps-line header) — both root-cause bugs in `src/nyc_executive_orders/`, not one-off data fixes.
- **Pre-1974 review ([Phase E](#phase-e--pre-1974-volume-split))** — the 14 bound volumes are all OCR'd, and the 978 records they produced now need eyes: 285 are flagged `needs-review`, and 11 of 14 volumes' subject indexes never parsed into a list the split could be reconciled against. `pre1974_report.md` is the worklist; [`qa-ui/`](qa-ui/) shows the page image next to what the model read off it, straight from the committed OCR — no GPU, no re-run.

**The one hard rule:** never invent or guess at content. Every title, date, and body must trace to the source PDF or an official record — an empty or flagged field is correct; a guess is a bug. This corpus is only as trustworthy as its provenance discipline (see [AI use in this project](#ai-use-in-this-project) below).

**Getting set up:**

```bash
git clone https://github.com/BetaNYC/nyc-executive-orders.git && cd nyc-executive-orders
git lfs install && git lfs pull        # source PDFs are LFS-tracked
uv run --with pytest python -m pytest  # 496 tests, offline, no network
```

That is everything you need for the corpus, the tests, and all of the Phase E work *except*
re-running the OCR itself — that one needs a GPU and an extra install, see
[Phase E setup](#setup--only-for-stage-1-the-ocr).

**Live harvests are not something to run casually.** Every `run_*_live.py` script under
`scripts/` makes real, rate-limited calls to nyc.gov, the Internet Archive, or DORIS's GPP
portal, and refuses to run without an explicit human-supervision flag — see each Phase section
above before running one. Contributions to the code, tests, or corpus review don't need this;
only fetching new source data does.

Questions, ideas, or a gap you've spotted: [open an issue](https://github.com/BetaNYC/nyc-executive-orders/issues) or hello@beta.nyc.

## Part of BetaNYC's civic data tools

This archive is the first application of a family of free, open civic data assets [BetaNYC](https://beta.nyc) builds and stewards:

- [`ny-gov-web-registry`](https://github.com/BetaNYC/ny-gov-web-registry) — which NY government entities exist, and where they live on the web (the target of this corpus's `establishes_entity` links).
- [`ny-gov-web-archiver`](https://github.com/BetaNYC/ny-gov-web-archiver) — the throttled Wayback Machine harvester behind this archive's historical backfills.
- [`nyc-boundaries`](https://github.com/BetaNYC/nyc-boundaries) — NYC administrative boundaries, mapped and queryable.
- Seven MCP servers giving AI agents direct access to NYC/NYS civic data: [Council legislation](https://github.com/BetaNYC/nyc-council-mcp), [City Record](https://github.com/BetaNYC/nyc-record-mcp), [Checkbook spending](https://github.com/BetaNYC/nyc-checkbook-mcp), [311](https://github.com/BetaNYC/nyc-311-mcp), [Charter/Code/Rules](https://github.com/BetaNYC/nyc-charter-laws-rules), [NYS legislation](https://github.com/BetaNYC/nys-openlegislation-mcp), and the [Council budget](https://github.com/BetaNYC/New-York-City-Budget).

## AI use in this project

BetaNYC uses AI tools openly and with human accountability. This repository was built by AI agents (Anthropic's Claude) working under the direction and review of BetaNYC staff — the "vibe coded" tooling above was written by AI, but every live harvest run is a separate, supervised, human-run step, and the roadmap and scope decisions are human-made.

One commitment about the content: **the archived executive orders are not AI-generated.** Order text is retrieved verbatim from official City sources and the Internet Archive — never summarized, paraphrased, or invented — and each document's provenance is recorded. Where the corpus is still incomplete, that gap is stated plainly rather than filled in by the machine.

Questions about our approach: hello@beta.nyc.

## License

Two licenses, split by what the file is:

- **Code** (`src/`, `scripts/`, `tests/`): [MIT](LICENSE) © 2026 BetaNYC — use and modify freely with attribution preserved.
- **Data and documentation** (`corpus/`, `pdfs/`, `docs/`): [Creative Commons Attribution-ShareAlike 4.0](LICENSE-DATA) — reuse and adapt with credit to BetaNYC, and share adaptations under the same license.

The underlying executive orders are public records of the City of New York; these licenses cover this compilation and its tooling.

## A note on sources

Statutory text is quoted from the NYC Administrative Code for informational purposes and is not legal advice; verify against the official source at [codelibrary.amlegal.com](https://codelibrary.amlegal.com). Archived documents are retrieved from the [Internet Archive](https://archive.org) under its access guidelines.
