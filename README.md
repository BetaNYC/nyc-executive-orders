# nyc-executive-orders

An open, complete, machine-readable archive of **New York City mayoral executive orders** — the public compilation the City is *legally required* to maintain.

> **Status: the archive is live.** The full 1974–present corpus — 2,192 orders as per-EO Markdown + bulk JSON, backed by 2,139 source PDFs — is published in [`corpus/`](corpus/). The 2014–2021 (de Blasio) cohort, previously an eight-year hole, was backfilled from the Internet Archive in July 2026 ([Phase B.4](#phase-b4--de-blasio-era-backfill-20142021)). The supersession graph is now populated deterministically from the corpus text ([Phase C](#phase-c--supersession-graph)); coverage and text are still being refined (OCR cleanup, metadata backfill), and known gaps and limits are documented, not hidden. See [Status](#status).

Vibe coded with [Claude](https://claude.ai) by [BetaNYC](https://beta.nyc).

---

## Why this exists

Mayoral executive orders shape how New York City government actually operates — who reports to whom, which offices exist, how agencies handle data, tenants, emergencies, and procurement. Yet they sit *outside* the City's laws, rules, and codes infrastructure, and they have never been reliably available as open, machine-readable data.

**New York City law already requires that they be.** NYC Administrative Code **§ 3-113.1** (added by Local Laws 2020/078 and 2022/040, in effect since June 2023) directs the Corporation Counsel to publish, on a single page of the City's website, *"a true and complete compilation of all mayoral executive orders."* The statute is, in effect, a specification for this project:

| The law requires… | § 3-113.1 |
|---|---|
| **Completeness** | every mayoral executive order issued on or after **January 1, 1974** |
| **One place** | a single page on the City's website |
| **Machine-readable** | a searchable format, downloadable in **bulk** |
| **Supersession** | each order annotated where a later order amended or superseded it |
| **Currency** | each new order posted **within one business day** of signing |
| **Open** | free of charge |

That date — **1974** — is why this project's record begins there. It's the line the law draws.

### The central question

Since the law has required this since June 2023, the first question isn't "how do we build it?" — it's **does the mandated compilation already exist, and does it actually work?**

- **If it exists and meets the spec** → we mirror it, verify it, and this project is largely a preservation-and-access layer on top of the City's own data.
- **If it's missing, broken, or incomplete** → this project becomes both the fix *and* an accountability record: documenting, order by order, the distance between what the law requires and what the public can actually get.

Early signs point toward the second case. A 2026 redesign of nyc.gov **removed** the historical executive-order PDFs that had long lived under `nyc.gov/html/records/` — *after* the law took effect. The current Mayor's Office site publishes only orders from roughly 2022 onward; the deepest historical collection sits in the **NYC Municipal Archives**, catalogued at the folder level with no open, per-order metadata. No single, complete, bulk-downloadable, machine-readable compilation is presently known to be available to the public. Confirming that — precisely, and on the record — is this repository's first task.

**This repository is BetaNYC's effort to build that compilation** — openly, in full, and to the standard the law already describes.

---

## What's in scope

- **Regular mayoral executive orders, 1974–present** — the compilation the law names. (~943 known to exist across five administrations, based on Internet Archive holdings.)
- **Emergency executive orders (EEOs)** — tracked as a separate series (they number in the thousands, largely because emergencies are renewed every few days). *(Scope under discussion.)*
- For each order: a stable identifier, date signed, issuing mayor/administration, title, subject tags, supersession relationships, full text, and a link to the source document.

Out of scope: state (gubernatorial) executive orders; agency rules and the Administrative Code (see [`nyc-charter-laws-rules`](https://github.com/BetaNYC/nyc-charter-laws-rules)).

---

## The data landscape (preliminary)

| Source | Coverage | Format | Notes |
|---|---|---|---|
| Live nyc.gov Mayor's Office | ~2022–present | HTML pages + PDFs | Filterable listing; PDFs often lack a reliable text layer (scanned) |
| Internet Archive (Wayback), `/html/records/` | 1974–~2013 | Archived PDFs | Recovers the historical set removed from nyc.gov; ~801 orders in one CDX query (Phase B) |
| Internet Archive (Wayback), `/assets/home/` | 2014–2021 (de Blasio) | Archived PDFs | The years neither the live API (≥2022) nor `/html/records/` (≤2013) reached; 72 regular + 270 emergency recovered (Phase B.4) |
| NYC Municipal Archives / DORIS | 1600s–present | Finding aids, microfilm, some digitized | Largest collection; folder-level metadata only; access-restricted |

Everything before ~2002 is scanned images requiring OCR; later orders are a mix of clean and scanned files. Full analysis lives in [`docs/`](docs/).

---

## Roadmap

- [x] **Verify** whether the § 3-113.1 mandated compilation currently exists and is usable — *verified 2026-07-15: it does not. No compliant single-page, bulk-downloadable, 1974-complete compilation exists on nyc.gov.*
- [x] **Gather** all available orders locally (live nyc.gov ✅ Phase A + Wayback historical set ✅ Phase B), respecting each source's access rules.
- [x] **Parse** PDFs to text (born-digital extraction with a local OCR fallback for scans).
- [x] **Structure** a clean, machine-readable corpus with metadata — *supersession annotations are the next phase (fields present, not yet populated).*
- [x] **Publish** the corpus (bulk-downloadable JSON + human-readable Markdown, matching the BetaNYC pattern).
- [x] **Annotate supersession** (`supersedes` / `superseded_by` / `in_effect` / `establishes_entity`) — deterministic, rule-based extraction from the corpus text ([Phase C](#phase-c--supersession-graph)); metadata backfill continues.
- [ ] **Maintain** it forward as new orders are signed.
- [ ] *(Explore)* an MCP server, and whether this folds into [`nyc-charter-laws-rules`](https://github.com/BetaNYC/nyc-charter-laws-rules).

---

## Phase A harvester (current-era, live nyc.gov)

This repository now ships the **Phase A** harvester: it collects the *current-era*
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
the window is applied by the year parsed from the URL *path*); (3) **host-duplicate
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
bulk `corpus/eo.json` and `corpus/manifest.csv`.

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
citation in the corpus text. This is what makes the corpus answer *"what's still in force?"* —
the supersession annotation § 3-113.1 requires.

- **Citations resolve year-scoped.** `Executive Order No. {n}, dated {Month} {day}, {year}, is
  hereby REVOKED` (and rescinded / superseded / repealed / amended) resolves to the cited
  *date's* year plus the series (`Emergency` ⇒ EEO, else EO), never the number alone —
  per-mayor numbering resets and emergency numbers collide across administrations.
- **Two edge sources**, tagged in the edge list: `body-citation` (the containing order is the
  actor) and `header-xref` (the OCR'd archival `XREF: AMENDED BY 'EO 18) 1978'` stamp, where
  the citing order is the actor).
- **`in_effect` is conservative and regular-only.** `false` when a resolvable in-corpus order
  *wholly* revokes/supersedes it (a section-scoped partial repeal or an amendment alone does
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

## Status

The archive is **live and published**. Phase A (current-era harvester), Phase B (historical
Wayback backfill), Phase B.2 (current-era gap recovery), and Phase B.4 (de Blasio-era 2014–2021
backfill) are built and offline-tested; each live harvest run is a separate, supervised,
human-run step. The parse → corpus pipeline (probe → extract → OCR → enrich → clean → emit) has
been run against the full corpus, and the result — 2,192 orders — is published in
[`corpus/`](corpus/).

It is the most complete open compilation of NYC mayoral executive orders we know of, but it is
**not yet authoritative**: OCR text of the oldest scans is imperfect (faithful to the source,
not perfected), 53 orders are documented as never publicly retrievable, and while the
supersession graph is now populated (Phase C: 239 edges, 136 regular orders computed out of
force), metadata backfill continues. The de Blasio regular series is
now near-complete — 72 of the ~91 orders issued (numbers run 1–91) were recovered; **19 numbers
were never archived on Wayback and are unrecoverable from any known open source** (the live API
returns nothing before 2022), including EO 31/2018 and EO 56/2020, which later orders cite as
revoked. Those gaps are listed, not hidden. Follow along or contribute —
[open an issue](https://github.com/BetaNYC/nyc-executive-orders/issues).

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
