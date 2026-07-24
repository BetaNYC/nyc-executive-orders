# GPP integration report — DORIS Government Publications Portal (Phase D)

**Report generated:** 2026-07-17  |  **Source:** DORIS Government Publications Portal (a860-gpp.nyc.gov)

Deterministic, offline fold of the GPP harvest into the corpus (no network, no cloud, no LLM). Dispositions re-derived from the committed inventory + corpus; the harvest manifest's class field is advisory only.

## Disposition summary (distinct orders)

| disposition | orders | action |
|---|---:|---|
| net-new | 79 | mint record, primary PDF → `pdfs/` |
| gap-closer (mint) | 20 | mint record (known-missing), primary PDF → `pdfs/` |
| gap-closer (existing) | 53 | attach PDF to existing no-pdf record, re-parse |
| dual | 2129 | 2nd lineage → `sources/gpp/`, record byte-identical |
| volume | 14 | park pre-1974 compilation → `sources/gpp/volumes/` |
| excluded | 7 | non-EO / no-file items, skipped |

**Corpus:** 2291 → **2390** (+99 minted: 79 net-new + 20 gap-closer mints). No-file records drop from 53 toward the 2 known unrecoverable gaps (Bloomberg EO 59, Adams EEO 471).

## Staging reconciliation

Expected file-set ids to place: **2588** — 2587 of expected 2588 present | missing 1 | corrupt 0 | extra 3.

- **Not yet staged (1):** the harvest may still be downloading; these orders are DEFERRED and integrate on a later re-run (resumable).
- **Extra (3):** staged but not integrated — the excluded items' harvested files + any leftovers; reported, never placed.

### This run

- minted records: **0**
- gap-closer records filled: **53**
- dual copies placed: **2369**
- volume files placed: **14**
- files written: **241**
- deferred (file not staged): **1**
- corpus: **2291 → 2291**

## Gaps after GPP integration

- **Bloomberg EO 59 and Adams EEO 471** — the two known missing numbered orders 1962–present outside the volumes; both absent from GPP too (EEO 471 falls between EEO 470 and EEO 472, both held, both signed the same day). Chased only via Municipal Archives / Law Dept FOIL.
- **Both Phase-C dangling supersession targets close:** 2018-EO-031 and 2020-EO-056 are now real records with text — re-run `scripts/run_supersede.py` to resolve them.
- **Pre-1974 (14 volumes)** — parked under `sources/gpp/volumes/`; per-order splitting (bookmark/index segmentation + OCR) is a later phase, not done here.

## Accountability correction

> Every one of the 53 current-era orders missing from the Mayor's own web surfaces exists in the City's records system: DORIS's Government Publications Portal held them all along. The earlier "never publicly retrievable" language is retired. The gap is not **preservation** — DORIS preserved them — it is **publication and compilation**: no single, complete, machine-readable § 3-113.1 compilation exists on any official surface.

