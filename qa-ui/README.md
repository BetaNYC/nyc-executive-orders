# qa-ui

A local web UI for reviewing VLM OCR output. The page on the left, its text and
QA signals on the right.

Both OCR runs are finished, so this is not a progress monitor — it is the
review surface for the pages the runs flagged.

```bash
cd qa-ui
npm install     # once
npm run dev     # opens http://localhost:5274
```

## Two collections

The project OCR'd its scans in two runs, with two directory layouts. The `Set`
dropdown picks between them. Everything below one volume or one document — the
page rail, the page image, the text tabs — is identical for both, except that
only post-1974 has a Corpus tab.

| Set | Units | Page records | Source PDF |
|---|---|---|---|
| **Post-1974 documents** | 1,086 EOs | `sources/ocr/<year>/<eo-id>/page_XXXX.json` | `pdf_path` from `corpus/eo.json` |
| **Pre-1974 volumes** | 14 bound volumes | `sources/gpp/volumes/ocr/<stem>/page_XXXX.json` | `local_paths[0]` from `sources/gpp/volumes.json` |

Both are committed and durable, so the UI works from a fresh clone with no
local render step. `vlm_ocr` writes page records per page as a run proceeds
(`--json-dir`), so a document being OCR'd right now shows its pages as they
land. The unit list is fetched when you pick a collection and the page list
when you pick a unit — reload to pull in newer pages.

Where the unit lists come from:

- Post-1974: `corpus/eo.json`, the same worklist `run_post1974_ocr.py` selects
  its candidates from. A scanned document with no page record is therefore
  still listed, as `not started` — that is how the 3 CUDA-OOM failures from the
  2026-08-31 run (`2026-EEO-1`, `2026-EEO-1.4`, `2026-EEO-2`) stay visible
  instead of being absent, and you can still read their scans. Born-digital
  documents have a PDF text layer, were never OCR'd, and are not listed. Any
  directory on disk with no matching record is listed too: a directory is never
  invisible.
- Pre-1974: `sources/gpp/volumes.json`, so an unstarted volume is visible
  rather than absent.

Override either OCR root if your layout differs:

```bash
POST1974_OCR_DIR=... EO_RECORDS=... OCR_DIR=... npm run dev
```

## The page image is the PDF

The page you see is the **source PDF**, rendered in the browser by pdf.js
(`src/pdfPage.jsx`). The runs also write page PNGs, but into a scratch
directory (`vlm-ocr-runs/`) that they prune as they go, so a PNG is there for
some pages, in some clones, some of the time. The PDF is committed (git-LFS)
and always there. Nothing has to be rendered locally first.

The boxes are drawn live from the page record, so the viewer must reproduce the
run's own geometry exactly. It does:

- **scale** = `dpi/72`, where `dpi` is what the run rendered at
  (`vlm_ocr.DEFAULT_DPI` = 200, set once in `vite.config.js`). Verified against
  the run's own PNGs: pdf.js and PyMuPDF agree to within 1 pixel.
- **rotation** = the PDF page's own `/Rotate` plus `rotation.applied_cw` from
  the page record. `vlm_ocr` renders pages already upright, so two of the
  fourteen volumes are turned 90° before the model sees them, and every bbox on
  those pages is relative to the upright raster.
- **element bboxes** are in the smart-resized image's pixel space — dots.ocr's
  image processor rounds its input to multiples of 28px — so they are rescaled
  exactly as `vlm_ocr.rescale_bbox` does. `smartResize()` in `src/pdfPage.jsx`
  mirrors `vlm_ocr.smart_resize()`, half-to-even rounding included; the two
  agree on 3,334 test dimensions.
- **`ink_coverage.uncovered_regions`** are already in raster pixels, and are
  drawn last, in red, like the pipeline's own overlays.

Box colours are `vlm_ocr.CATEGORY_COLORS`. Keep the two in sync if that map
changes.

The server sends the PDF with byte-range support, because pdf.js asks for the
pieces it needs and a bound volume is 63 MB.

## What you get

- **Set / Year / Document (or Volume) dropdowns** — each unit shows its status
  (`not started` / `classified only` / `incomplete` / `OCR'd`), its page count
  and how many of its pages carry QA flags. Post-1974 adds a **Year** filter,
  because 1,086 documents in one list is not a list.
- **needs review (N)** — the checkbox filters the dropdown to the units that
  still want a human: any page flagged, or the document never finished. The
  count beside it is the whole collection's total (313 for post-1974: 310
  documents with flagged pages, plus the 3 that failed).
- **Left** — the page, with **Boxes**/**Plain** (draw the model's boxes or
  not), zoom (`−`/`Fit`/`+`, pinch, ctrl+wheel) and pan.
- **Right** — Markdown / QA / JSON for the page on screen, and Corpus for the
  whole document:
  - **Markdown** renders the page's elements the same way
    `vlm_ocr.py`'s `records_to_markdown()` would.
  - **QA** shows the three signals the model doesn't emit itself: ink
    coverage (did every mark on the page land inside a returned box?), token
    confidence (text vs. layout, since layout is routinely less certain and
    that's fine), and finish reason (`length` = truncated output). A page
    that only has a `classify-blank-only` verdict and no real OCR yet shows
    that verdict here and on the Markdown tab instead.
  - **JSON** is the page's raw record.
  - **Corpus** (post-1974 only) is the text the published corpus carries for
    this document *today* — Tesseract's, for everything the VLM run has not
    replaced — so you can read the new output against what it would replace.
    `corpus/eo.json` stores one string per order with no page boundaries in it,
    so this tab is the whole document while every other tab is the page on
    screen, and it says so. **Clean**/**Raw** switch between `full_text` and
    `full_text_raw` (before the microfilm-header and stray-mark cleanup, shown
    only when the two differ); the badge is the corpus's own `text_quality`.
- **Left rail** — one row per page, status dot + QA-flag stripe. `←`/`→` (or
  `k`/`j`) step pages; the URL hash
  (`#collection=post1974&unit=1977-EO-091&page=3`) is a shareable deep link.
  The old `#volume=STEM&page=N` links still resolve, to the pre-1974 set.

Where to start reading: [`post1974-run-summary.md`](../post1974-run-summary.md)
for the post-1974 run (worst coverage: `1974-EO-020` p6, `1976-EO-060` p5;
most flagged pages in one document: `1977-EO-091`), and
[`pre1974_report.md`](../pre1974_report.md) for the volumes.

## Layout

- `vite.config.js` — declares the two collections (id, label, `ocrRoot`, unit
  source, run DPI) and mounts `server/api.js` into the Vite dev server, so
  `npm run dev` is the only process.
- `server/api.js` — unit discovery per collection, then one shared
  implementation for everything below a unit: `/api/collections`,
  `/api/collections/:c/units`, `/api/collections/:c/units/:u`,
  `/api/collections/:c/units/:u/pages/:n`,
  `/api/collections/:c/units/:u/text` (the corpus text, from the same cached
  `corpus/eo.json` parse), and `/files/:c/:u/pdf`. It derives
  each page's QA flags and status live, so the rail and the page view can't
  disagree about what is wrong with a page. The unit list reads every page
  record it has (~1,800 for post-1974, ~0.3 s) rather than caching, so a run
  writing records right now stays visible on reload.
- `src/App.jsx` — the shell, pickers, rail, and image pane; `src/pdfPage.jsx` —
  the PDF renderer and bbox overlay; `src/qa.jsx` — the QA tab;
  `src/corpus.jsx` — the Corpus tab;
  `src/markdown.js` mirrors `vlm_ocr.py`'s `element_to_markdown()`, so keep the
  two in sync if that mapping changes.
