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
- [ ] **Gather** all available orders locally (live nyc.gov + Wayback historical set + Archives), respecting each source's access rules.
- [ ] **Parse** PDFs to text (born-digital extraction with an OCR fallback for scans).
- [ ] **Structure** a clean, machine-readable corpus with metadata and supersession annotations.
- [ ] **Publish** the corpus (bulk-downloadable JSON + human-readable Markdown, matching the BetaNYC pattern).
- [ ] **Maintain** it forward as new orders are signed.
- [ ] *(Explore)* an MCP server, and whether this folds into [`nyc-charter-laws-rules`](https://github.com/BetaNYC/nyc-charter-laws-rules).

---

## Status

Preliminary. This is a public stub to anchor the work and share early research. Nothing here should yet be treated as a complete or authoritative record of NYC executive orders. Follow along or contribute — [open an issue](https://github.com/BetaNYC/nyc-executive-orders/issues).

## License

[MIT](LICENSE) © 2026 BetaNYC

## A note on sources

Statutory text is quoted from the NYC Administrative Code for informational purposes and is not legal advice; verify against the official source at [codelibrary.amlegal.com](https://codelibrary.amlegal.com). Archived documents are retrieved from the [Internet Archive](https://archive.org) under its access guidelines.
