# qa-ui

A local web UI for reviewing VLM OCR output for the 14 pre-1974 bound volumes
(see [`sources/gpp/volumes/README.md`](../sources/gpp/volumes/README.md)).
Page image on the left (bbox overlay or raw), the page's Markdown/QA/JSON on
the right. Ported from an early prototype viewer, but pointed at the 14
canonical volumes and their **committed** output instead of arbitrary
experiment run directories.

**All 14 volumes are now OCR'd** (2,936 pages), so this is no longer a
progress monitor for a running job — it is the review surface for finished
output: the 285 records tiered `needs-review`, the 113 pages that produced
nothing usable, and any page whose QA signals look off. See
[`pre1974_report.md`](../pre1974_report.md) for what to go looking at.

```bash
cd qa-ui
npm install     # once
npm run dev     # opens http://localhost:5274
```

## Two data sources

- **`sources/gpp/volumes/ocr/<stem>/`** — committed, durable: per-page OCR
  JSON (`page_XXXX.json`) plus, for a volume that's only had its blank-page
  calibration pass run, `classify_report.json`. This is what drives the
  volume list, per-page status, and the Markdown/QA/JSON tabs. Works from a
  fresh clone with no setup.
- **`vlm-ocr-runs/<stem>/{raw,overlays}/`** — local scratch page renders,
  **not committed** (PNGs are regenerable from the source PDF, not source of
  truth). Without these, a volume still shows full QA data, just no page
  image.
  Page records are read from `sources/gpp/volumes/ocr/<stem>/` and nowhere
  else: `vlm_ocr` writes them there per page as a run proceeds (`--json-dir`),
  so a volume being OCR'd right now shows its pages as they land. The page list
  is fetched when you pick a volume — reload to pull in pages the run has
  produced since.

To actually see pixels for a volume, render it locally first:

```bash
python scripts/run_volume_ocr.py --volume <name-substring> --classify-blank-only
```

(this renders `raw/` as a side effect even before/without a full OCR run — see
the root `scripts/run_volume_ocr.py` docstring for the full pipeline).

Override either root if your layout differs:

```bash
OCR_DIR=/path/to/ocr RENDERS_DIR=/path/to/runs npm run dev
```

## What you get

- **Volume dropdown** — the 14 volumes from `sources/gpp/volumes.json`, each
  showing its status (`not started` / `classified only` / `OCR'd`) and page
  counts, so an unstarted volume is visible rather than absent from a list.
- **Left** — the page image: bbox+category overlay (only present for really
  OCR'd pages) or the raw render, with zoom (`−`/`Fit`/`+`, pinch, ctrl+wheel)
  and pan.
- **Right** — Markdown / QA / JSON tabs per page:
  - **Markdown** renders the page's elements the same way
    `vlm_ocr.py`'s `records_to_markdown()` would.
  - **QA** shows the three signals the model doesn't emit itself: ink
    coverage (did every mark on the page land inside a returned box?), token
    confidence (text vs. layout, since layout is routinely less certain and
    that's fine), and finish reason (`length` = truncated output). A page
    that only has a `classify-blank-only` verdict and no real OCR yet shows
    that verdict here and on the Markdown tab instead.
  - **JSON** is the page's raw record.
- **Left rail** — one row per page, status dot + QA-flag stripe. `←`/`→` (or
  `k`/`j`) step pages; the URL hash (`#volume=STEM&page=N`) is a shareable
  deep link.

## Layout

- `vite.config.js` — mounts `server/api.js` into the Vite dev server, so
  `npm run dev` is the only process; computes `ocrRoot`/`rendersRoot` from
  `OCR_DIR`/`RENDERS_DIR` env vars.
- `server/api.js` — reads `sources/gpp/volumes.json` for the volume list and
  `ocrRoot`/`rendersRoot` for per-page data: `/api/volumes`,
  `/api/volumes/:volume`, `/api/volumes/:volume/pages/:n`, and
  `/files/:volume/{overlays,raw}/page_XXXX.png`. It also derives each page's
  QA flags and status live, so the rail and the page view can't disagree
  about what's wrong with a page.
- `src/App.jsx` — the shell, rail, and image pane; `src/qa.jsx` — the QA tab;
  `src/markdown.js` mirrors `vlm_ocr.py`'s `element_to_markdown()`, so keep
  the two in sync if that mapping changes.
