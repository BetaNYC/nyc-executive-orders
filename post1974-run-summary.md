# Post-1974 OCR run — 2026-08-31

Source log: `post-1974-full-run.log`. Run directory: `.post1974-run/`.

## What happened

The run made OCR records for 1082 of 1085 documents. 3 documents failed. The GPU
ran out of memory on each of them.

| Phase | Result | Time |
|---|---|---|
| preflight, provision, upload, install | ok | 0h02m13s |
| ocr | FAILED (exit 1) | 4h02m00s |
| teardown | ok, droplet destroyed | 0h00m12s |

Details:

- The script used 4 model replicas on one 47 GB card. Each replica held 10-12 GB.
  When a large page needed a 6.2 GB allocation, only 1.8 GB was free. The
  allocation failed.
- The step exited with code 1 because of the 3 failures. The wrapper rescued all
  page records before teardown, so no good work is lost.
- Totals: 1793 pages OCR'd, 0 parse errors, 0 truncated pages, 0 blank pages.
- Quality flags from the local records: 238 low-coverage pages in 198 documents,
  and 12 no-ink pages in 11 documents. These pages have text. The flags mark them
  for a human check.
- The flagged page PNGs came home. `vlm-ocr-runs/` now holds 13 GB.

## Files that you must re-run

Three documents. Their directories under `sources/ocr/2026/` exist but hold zero
page records.

| EO id | Pages | Cause |
|---|---|---|
| 2026-EEO-1 | 1 | CUDA OOM, 6.17 GiB |
| 2026-EEO-1.4 | 1 | CUDA OOM, 6.04 GiB |
| 2026-EEO-2 | 3 | CUDA OOM, 6.17 GiB |

Use fewer workers, or the pages will fail again for the same reason:

```
./execute-post-1974-ocr.sh --workers 2 \
  --eo-id 2026-EEO-1 --eo-id 2026-EEO-1.4 --eo-id 2026-EEO-2
```

The `--force` flag from the log message is not necessary here. The three
directories are empty, so the resume check does not skip them.

## Files that you can review, but do not need to re-run

The OCR wrote text for these pages. Low coverage means that some ink fell outside
every returned box. The threshold is `MIN_COVERED_FRACTION = 0.98`.

- Worst coverage: `1974-EO-020` p6 (6%), `1976-EO-060` p5 (20%),
  `1998-EO-044` p2 (37%), `1990-EO-004` p2 (42%), `1979-EO-039` p1 (44%).
- Largest count in one document: `1977-EO-091`, 20 flagged pages.
- No-ink pages: `2014-EEO-28` through `2014-EEO-38` (10 documents, 11 pages) and
  `2026-EEO-3.1` p1. The 2014 group is one block of consecutive orders, so a bad
  scan batch is the likely cause.

## Notes

- `sources/ocr/1974/1974-EO-001` holds a page record, but this run's worker logs
  do not list the document. An earlier run made that record.
- To rebuild the per-document flag lists, read `ink_coverage.covered_fraction` and
  `page_stats.dark_fraction` from each `sources/ocr/*/*/page_*.json`.
