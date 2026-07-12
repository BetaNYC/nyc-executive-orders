# nyc-executive-orders

An open, complete, machine-readable archive of **New York City mayoral executive orders** — the public compilation the City is *legally required* to maintain.

> ⚠️ **Early-stage stub.** This repository is being scaffolded. It currently holds preliminary research and a roadmap — not yet the full corpus. See [Status](#status).

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
| Internet Archive (Wayback) | 1974–~2021 | Archived PDFs | Recovers the historical set removed from nyc.gov; ~801 orders in one CDX query |
| NYC Municipal Archives / DORIS | 1600s–present | Finding aids, microfilm, some digitized | Largest collection; folder-level metadata only; access-restricted |

Everything before ~2002 is scanned images requiring OCR; later orders are a mix of clean and scanned files. Full analysis lives in [`docs/`](docs/).

---

## Roadmap

- [ ] **Verify** whether the § 3-113.1 mandated compilation currently exists and is usable.
- [ ] **Gather** all available orders locally (live nyc.gov ✅ Phase A + Wayback historical set ✅ Phase B + Archives), respecting each source's access rules.
- [ ] **Parse** PDFs to text (born-digital extraction with an OCR fallback for scans).
- [ ] **Structure** a clean, machine-readable corpus with metadata and supersession annotations.
- [ ] **Publish** the corpus (bulk-downloadable JSON + human-readable Markdown, matching the BetaNYC pattern).
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

### Development

```bash
uv run --with pytest python -m pytest        # offline test suite (no network)
uv run --with pytest python -m pytest -v     # verbose
```

Tests run on a Python 3.11 / 3.14 matrix in CI (`.github/workflows/tests.yml`).
Source PDFs are git-LFS-tracked (`.gitattributes`, `pdfs/**`); run `git lfs
install` once in a fresh clone.

## Status

Preliminary. Phase A (current-era harvester) and Phase B (historical Wayback
backfill) are built and offline-tested; each live harvest run is a separate,
supervised, human-run step. OCR / full-text parsing and supersession graphs are
not yet built. Nothing here should yet be treated as a complete or authoritative
record of NYC executive orders. Follow along or contribute —
[open an issue](https://github.com/BetaNYC/nyc-executive-orders/issues).

## License

[MIT](LICENSE) © 2026 BetaNYC

## A note on sources

Statutory text is quoted from the NYC Administrative Code for informational purposes and is not legal advice; verify against the official source at [codelibrary.amlegal.com](https://codelibrary.amlegal.com). Archived documents are retrieved from the [Internet Archive](https://archive.org) under its access guidelines.
