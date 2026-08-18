# Pre-1974 bound volumes

Fourteen bound compilations from DORIS's Government Publications Portal, covering the
**Executive Order and Executive Memoranda series before this project's 1974 corpus start**
(the boundary NYC Admin Code § 3-113.1 itself draws). Together they run **continuous,
January 7, 1946 → November 12, 1973** — five administrations: O'Dwyer, Impellitteri, Wagner,
and Lindsay.

Each file below is a scanned bound volume — one administration-era compilation, with its own
subject index — not an individual order. All 14 are pure image scans with no embedded text
layer.

**Splitting them into per-order records is [Phase E](../../../README.md#phase-e--pre-1974-volume-split).**
Two stages, because the OCR is slow and the post-process must be iterable without repeating it:

```
scripts/run_volume_ocr.py     local dots.ocr (MLX or CUDA) -> ocr/<volume-stem>/page_XXXX.json
scripts/run_pre1974_build.py  segment + clean + emit -> corpus/YYYY/ + corpus/eo_pre1974.json
```

**All 14 volumes have now had their OCR pass** — 2,936 pages — and stage 2 splits them into
**978 records** in `corpus/eo_pre1974.json`. Per-volume record counts are in the table below;
the found-vs-index ledger, the pages OCR could not read, and every volume-level flag are in
[`../../../pre1974_report.md`](../../../pre1974_report.md). Note what that report says plainly:
285 of the 978 records are `needs-review`, 113 pages produced no usable OCR (94 of them in
`1968-01-10_1969-12-29_Lindsay_Orders`), and only 3 of the 14 volumes' subject indexes parsed
into a list the split could be reconciled against. The capture is complete; the review is not.

`ocr/` holds the committed per-page OCR output — plain JSON, not LFS — so anyone can re-run the
segmentation and metadata rules with no GPU and none of the ~45–50 hours the OCR itself took.
Rendered page images and bbox overlays stay scratch under `vlm-ocr-runs/` (gitignored). Review
either one in [`qa-ui/`](../../../qa-ui/).

Before OCR'ing a volume for the first time, calibrate its blank-page thresholds — the defaults
were tuned on one volume's scans, and a false "blank" silently drops a real order:

```
python scripts/run_volume_ocr.py --volume <substring> --classify-blank-only
```

This writes its own `ocr/<volume-stem>/classify_report.json` alongside `page_*.json`, so the
blank/keep verdict for every page is on record even before (or without) a full OCR run.

**Filename scheme:** `<start-date>_<end-date>_<Mayor(s)>_<Orders|Memoranda>.pdf`. Dates and
mayor names are taken verbatim from each volume's own GPP description, not inferred. Two
instrument types exist and sometimes cover overlapping date ranges as *separate* bound
volumes — a numbered **Executive Orders** compilation and an unnumbered **Executive
Memoranda** compilation for the same mayor and years — so both the date range and the type are
part of the filename, not just the range.

| File | Pages | Records | Size | Covers | GPP item id |
|---|---:|---:|---:|---|---|
| [`1946-01-07_1950-10-04_ODwyer-Impellitteri_Memoranda.pdf`](1946-01-07_1950-10-04_ODwyer-Impellitteri_Memoranda.pdf) | 204 | 60 | 65.9 MB | Mayor O'Dwyer & Acting Mayor Impellitteri | `r207tq833` |
| [`1950-11-16_1953-11-24_Impellitteri-Sharkey_Memoranda.pdf`](1950-11-16_1953-11-24_Impellitteri-Sharkey_Memoranda.pdf) | 126 | 40 | 38.2 MB | Mayor Impellitteri & Acting Mayor Sharkey | `cf95jd15r` |
| [`1954-01-04_1957-11-26_Wagner-Theobald_Memoranda.pdf`](1954-01-04_1957-11-26_Wagner-Theobald_Memoranda.pdf) | 189 | 64 | 59.2 MB | Mayor Wagner & Deputy Mayor Theobald | `zw12z708m` |
| [`1958-01-01_1961-12-22_Wagner_Memoranda.pdf`](1958-01-01_1961-12-22_Wagner_Memoranda.pdf) | 242 | 73 | 56.6 MB | Mayor Wagner | `fn107061s` |
| [`1962-01-23_1963-12-26_Wagner_Orders.pdf`](1962-01-23_1963-12-26_Wagner_Orders.pdf) | 334 | 100 | 88.0 MB | Mayor Wagner | `ht24wm250` |
| [`1964-01-10_1965-12-21_Wagner_Orders.pdf`](1964-01-10_1965-12-21_Wagner_Orders.pdf) | 264 | 149 | 39.5 MB | Mayor Wagner | `hh63sx60k` |
| [`1966-01-04_1967-12-22_Lindsay_Orders.pdf`](1966-01-04_1967-12-22_Lindsay_Orders.pdf) | 192 | 141 | 37.8 MB | Mayor Lindsay | `wp988m593` |
| [`1966-01-01_1968-05-13_Lindsay_Memoranda-Unnumbered.pdf`](1966-01-01_1968-05-13_Lindsay_Memoranda-Unnumbered.pdf) | 10 | 8 | 5.3 MB | Mayor Lindsay — a short list of un-numbered memoranda | `3b591b37f` |
| [`1966-01-06_1968-12-16_Lindsay_Memoranda.pdf`](1966-01-06_1968-12-16_Lindsay_Memoranda.pdf) | 149 | 84 | 25.2 MB | Mayor Lindsay | `9w0324914` |
| [`1968-01-10_1969-12-29_Lindsay_Orders.pdf`](1968-01-10_1969-12-29_Lindsay_Orders.pdf) | 389 | 77 | 46.4 MB | Mayor Lindsay — **94 pages unreadable by OCR**, the worst volume in the set | `w0892c63v` |
| [`1969-01-16_1971-12-27_Lindsay_Memoranda.pdf`](1969-01-16_1971-12-27_Lindsay_Memoranda.pdf) | 174 | 51 | 29.8 MB | Mayor Lindsay | `bn999852s` |
| [`1970-01-01_1971-12-09_Lindsay_Orders.pdf`](1970-01-01_1971-12-09_Lindsay_Orders.pdf) | 422 | 72 | 48.9 MB | Mayor Lindsay | `tt44pp54b` |
| [`1972-02-01_1973-11-12_Lindsay_Orders.pdf`](1972-02-01_1973-11-12_Lindsay_Orders.pdf) | 129 | 34 | 27.0 MB | Mayor Lindsay | `1n79h5903` |
| [`1972-02-18_1973-11-05_Lindsay_Memoranda-Incomplete.pdf`](1972-02-18_1973-11-05_Lindsay_Memoranda-Incomplete.pdf) | 112 | 25 | 40.5 MB | Mayor Lindsay — GPP's own description flags this one "possibly incomplete" | `2j62s6696` |

**Total: 2,936 pages, 608 MB → 978 records.** The `Records` column is what the segmenter emitted,
not what the volume is known to contain — it counts instruments found, including unnumbered ones,
and `pre1974_report.md` is where a count is defended (or isn't).

Machine-readable version of this same table (fileset ids, GPP download URLs, staged flags) is
[`../volumes.json`](../volumes.json), generated by `scripts/run_gpp_integration.py` — see
`src/nyc_executive_orders/gpp.py`'s `VOLUME_FILENAMES` map for how the filenames above are
derived (a fixed, hand-verified map, not a parser — this is a closed set of 14 that will not
grow).
