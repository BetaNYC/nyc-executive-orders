# How the VLM OCR pipeline runs

A plain-English tour of the machine-reading pipeline in this repository, for a
reader who has never opened it before.

---

## 1. What problem this solves

This repository collects the executive orders of the Mayor of New York City and
publishes them as plain, searchable text.

The orders arrive as PDF files. There are two very different kinds of PDF:

- **Born-digital PDFs.** Someone typed the order in a word processor and saved
  it as a PDF. The letters are stored as letters. A computer can copy the text
  out with no guesswork. This text is perfect, so the pipeline never touches it.
- **Scanned PDFs.** Someone put paper on a scanner. The file holds a
  *photograph* of the page and nothing else. A computer sees pixels, not words.

To publish a scanned order, the pipeline must read the picture and write down
the words. That job is called OCR, which is short for optical character
recognition.

## 2. What "VLM" means here

The repository has **two** OCR engines.

| Engine | Tool | Used for |
|---|---|---|
| The old one | Tesseract, through `ocrmypdf` (`src/nyc_executive_orders/ocr.py`) | The original pass over the post-1974 scans |
| The new one | `dots.ocr`, a vision-language model (`src/nyc_executive_orders/vlm_ocr.py`) | Both jobs described in this document |

A **vision-language model**, or VLM, is an AI model that takes a picture and a
written instruction, and answers in text. Tesseract matches letter shapes
against templates. A VLM instead *reads* the page the way a person does, so it
copes far better with old type, crooked scans, faint carbon copies and
letterhead.

Three facts about how this repository runs it:

1. **It runs on your own machine.** The model file sits on local disk. Nothing
   is sent to Google, Amazon, OpenAI or anyone else. The only network call in
   the whole pipeline is the one-time download of the model weights.
2. **It is deterministic.** The temperature is 0, so the model always picks its
   most likely next word instead of sampling. The same machine and the same
   page give the same answer every time.
3. **It needs a GPU to be practical.** Two interchangeable backends do the same
   work: `mlx` on an Apple Silicon Mac, and `cuda` (PyTorch) on an NVIDIA card.

The model's job stops at "pixels in, text out". It never decides what an order
means, what its number is, or what date it carries. Ordinary rule-based code
makes every one of those decisions.

## 3. The two sets of documents

The pipeline runs over two populations that look nothing alike.

| | **Pre-1974** | **Post-1974** |
|---|---|---|
| What the PDF is | 14 **bound books**, each a compilation of many orders | One PDF **per order** |
| Where it came from | The city archives (DORIS), under `sources/gpp/volumes/` | The primary nyc.gov lineage, under `pdfs/YYYY/` |
| Years | 1946 to 1973 | 1974 to today |
| Size | 2,936 pages | 1,799 pages across 1,086 documents |
| Was there text before? | No. These were never in the corpus at all. | Yes. Tesseract already read them, badly. |
| Which corpus file | `corpus/eo_pre1974.json` | `corpus/eo.json` |

The 1974 line matters because a New York City law, Admin Code § 3-113.1, defines
the official deliverable as orders from 1974 onward. The older books are a
second-source bonus, so they ship in a separate file and cannot disturb the
official one.

---

## 4. The part both sets share: reading one page

Both jobs call the same module, `src/nyc_executive_orders/vlm_ocr.py`. For each
page it does the same six steps:

1. **Render.** Turn the PDF page into a PNG image at 200 dots per inch.
2. **Rotate.** If the page is wider than it is tall, turn it a quarter turn.
   Two of the old books were scanned sideways. The model reports no orientation
   of its own, so the code must fix this before the model sees the page.
3. **Judge blankness.** Measure how many dark pixels the page has and how much
   the brightness varies. If the page is below **both** thresholds
   (`dark_fraction < 0.001` **and** `std < 6.5`), call it blank and skip it.
   Blank versos are extremely common in the bound books.
4. **Read.** Send the image plus a fixed prompt to `dots.ocr`. The model returns
   JSON: a list of regions, each with a bounding box, a category (`Title`,
   `Section-header`, `Text`, `Table`, `Picture`, `Page-header`) and the text
   inside it.
5. **Score.** Compute three quality signals, described in section 8.
6. **Write.** Save one file, `page_XXXX.json`, holding the model's exact output
   plus those signals.

**The per-page JSON file is the real product of the OCR stage.** It is committed
to git as plain, readable JSON. Everything after this point reads those files
and needs no GPU, no model and no waiting. That split is deliberate: the OCR
took roughly 45 to 50 hours for the bound books, and nobody should ever have to
repeat it to fix a bug in the text assembly.

Both sets also share the reader that turns page files into publishable text,
`src/nyc_executive_orders/vlm_pages.py`. It drops `Picture` regions (city seals
and mayoral signatures carry no words), flattens tables into plain lines, joins
the pages, and collects the quality flags.

---

## 5. The pre-1974 path, step by step

**Driver:** `scripts/run_volume_ocr.py`, wrapped by `run_full_vlm_pre_1974.sh`.

**Stage 0 — calibrate (optional but advised).**
`run_volume_ocr.py --volume X --classify-blank-only` renders and scores every
page without loading the model. It takes minutes. It writes its verdicts to a
committed `classify_report.json`. The point is to catch a *false* blank before
the long run starts, because a wrongly skipped page silently deletes a real
order.

**Stage 1 — OCR the book.** One volume at a time, about two minutes per
non-blank page, about two hours per book. Page records land directly in
`sources/gpp/volumes/ocr/<book-name>/page_XXXX.json`. Page numbers are
absolute, so an interrupted run resumes at the first missing page.

**Stage 2 — split the book into orders.** This is the hard part, and it is
100% rule-based code in `src/nyc_executive_orders/volume_split.py`. No model
and no network. It reads literal strings off the page to find document
boundaries:

- A **letterhead** ("CITY OF NEW YORK / OFFICE OF THE MAYOR") starts a new
  order.
- A **page-number ornament** (`-2-`, `- 4 -`, `PAGE 2`) means this page
  continues the order before it.
- **Blank pages** separate documents, because only the front of each leaf is
  printed.
- An **instrument label** ("EXECUTIVE ORDER NO. 14", "MEMORANDUM No. 1") gives
  the number, though the printed wording is inconsistent inside one numbering
  sequence.
- Each book opens with its own **subject index**, which the model returns as an
  HTML table. That index is the book's own list of what it contains.

**Stage 3 — publish.** `src/nyc_executive_orders/build_pre1974.py` runs each
split document through the same `clean` and `enrich` stages every other era
uses, then writes `corpus/YYYY/<id>.md`, `corpus/eo_pre1974.json`,
`corpus/manifest_pre1974.csv`, a provenance sidecar, and the run report
`pre1974_report.md`.

**Selection: there is none.** Every page of all 14 books is OCR'd. The book list
comes from `sources/gpp/volumes.json`. The only pages excluded are the ones the
blank test skips.

---

## 6. The post-1974 path, step by step

**Driver:** `scripts/run_post1974_ocr.py`.

**Why a separate driver exists.** The pre-1974 driver starts a fresh process,
and therefore loads the 3-billion-parameter model, once per book. Fourteen model
loads cost nothing. One thousand and eighty-six of them would burn 9 to 18 hours
before reading a single page. So this driver loads the model **once per worker**
and then loops over documents.

**Step 1 — OCR.** The driver splits the worklist across N parallel workers on
the same GPU card, then runs. Page records land in
`sources/ocr/<year>/<eo_id>/page_XXXX.json`, committed to git. Rendered page
images go to `vlm-ocr-runs/post1974/` and are deleted as the run proceeds unless
the document raised a flag, because 1,799 pages of renders is several gigabytes
of scratch. Logs go to `vlm-logs/post1974/worker-N.log`.

**Step 2 — rebuild the corpus.** `scripts/run_parse.py --ocr-engine vlm` reads
the committed page records and rewrites `corpus/eo.json`. It loads no model and
takes seconds. The `--ocr-engine` flag has three settings:

| Setting | If page records exist | If they do not |
|---|---|---|
| `auto` | Use the VLM text | Keep the old Tesseract text |
| `vlm` | Use the VLM text | Mark the record `ocr-skipped` |
| `tesseract` | Ignore them | Re-run Tesseract (the rollback path) |

`auto` is what you use while the OCR is still grinding, so the published corpus
stays complete the whole time.

**Step 3 — check it.** `scripts/report_post1974_ocr.py --stage diff`, described
in section 9. Step 2 overwrote the Tesseract text, but `corpus/eo.json` is
committed, so git still holds it. The report reads the newest revision in which
no record yet says `ocr-vlm` and compares against that. Nothing has to be frozen
in advance, and there is no ordering hazard.

**Renting a GPU.** `run_full_vlm_post_1974.sh` rents a DigitalOcean GPU box for
step 1 only: preflight, provision, upload, install, OCR, download, and destroy
the box. It destroys the box on every exit path, including a crash or a Ctrl-C.
Steps 2 and 3 stay at home. They need no GPU, and step 3 needs the git history
that the box — an rsync'd file subset, not a clone — does not have.

---

## 7. How post-1974 files are chosen for OCR

This is the question with the most interesting answer, and it lives in
`src/nyc_executive_orders/vlm_corpus.py`, in `select_candidates`.

The driver starts from every record in `corpus/eo.json` — 2,291 of them — and
sorts them by year and id, so a resumed run visits documents in the same order.
For each record it does the following:

1. **Find the PDF.** If the record has no `pdf_path`, or the file is not on
   disk, mark it `no-pdf` and move on.
2. **Open and probe the PDF** with `textlayer.classify_pdf`. This reads the
   file and asks: does this PDF contain a real text layer?
3. **Decide:**
   - a real text layer → `born-digital` → **never OCR it**;
   - image only → `scanned` → **this is the work**;
   - the file cannot be opened at all → `unreadable` → report it as a harvest
     bug.

That yields the **1,086 scanned documents, 1,799 pages** the run targets. The
other roughly 1,205 documents are born-digital and are left alone, because their
text is already byte-perfect and re-reading a picture of it could only introduce
errors.

**The important detail: it re-probes the file rather than trusting the corpus.**
Each record already carries a `text_source` field saying how its text was made.
The selector ignores that field and opens the PDF again. This costs milliseconds
and buys two things. First, a document stubbed out by some earlier partial run
needs no special case; it is simply a scanned PDF. Second, the OCR stage and the
corpus-build stage cannot drift apart about which documents are in scope,
because the build stage branches on the very same probe.

**Skipping work already done.** Before OCR'ing a document, the driver counts the
`page_*.json` files already in its output directory and compares that against
the page count the probe measured. A complete document is skipped outright. A
document killed part-way resumes at its first missing page. So the way to grind
through the backlog is simply to run the command again.

**Narrowing the run by hand.** `--year 2023`, `--since-year`/`--until-year`,
`--eo-id 2023-EEO-302`, and `--limit 20` all cut the worklist down. `--force`
redoes documents that already have output. `--dry-run` prints the plan and rents
nothing. `--status` prints a per-year ledger of done, part-done and remaining.

**Splitting the work across workers.** `bin_pack` sorts documents by page count,
longest first, and hands each one to whichever worker is currently lightest.
On the real population this lands within 0.1% of a perfect split. Because the
split is that good and is computed up front, the workers need no queue, no
locking and no communication with each other at all.

---

## 8. Shared code and divergent code

### Shared

| Module | What it does for both sets |
|---|---|
| `vlm_ocr.py` | Renders pages, rotates them, judges blankness, runs the model, computes QA signals, writes `page_XXXX.json` |
| `vlm_pages.py` | Turns page records into publishable body text and QA flags |
| `clean.py` | Normalizes text and assigns the quality tier |
| `enrich.py` | Derives metadata fields |
| `build_corpus.py` helpers | Builds the identical 21-field YAML front matter every era uses |

`volume_split.py` re-exports `element_text`, `page_lines` and `page_flags` from
`vlm_pages.py`, so the pre-1974 output is unchanged byte for byte by the sharing.

`assemble_body` is shared too, and that one *did* change the pre-1974 output.
Both eras used to glue a page's layout elements together with a single newline,
which Markdown reads as one continuing paragraph, so a page the model split into
six WHEREAS clauses published as one slab; and only the post-1974 path rejoined a
word broken across a line. One function now makes all three decisions — blocks
and pages separated by a blank line, words rejoined across either boundary — so
the pre-1974 bodies gained their paragraph breaks and closed up 192 broken words.
Nothing else moved: every record's text is identical once whitespace is stripped
and those hyphens are closed, and no record changed quality tier.

### Divergent

| Concern | Pre-1974 | Post-1974 |
|---|---|---|
| Driver script | `run_volume_ocr.py` (+ `run_full_vlm_pre_1974.sh`) | `run_post1974_ocr.py` (+ `run_full_vlm_post_1974.sh`) |
| Model loading | Once per book, in a new process | Once per worker, then looped |
| Parallelism | None. One book at a time. | N workers on one GPU, work split by page count |
| Records written to | `sources/gpp/volumes/ocr/<book>/` | `sources/ocr/<year>/<eo_id>/` |
| Is splitting needed? | Yes — `volume_split.py`, the whole hard part | No. One PDF is already one order. |
| Corpus builder | `build_pre1974.py` | `build_corpus.py`, via `run_parse.py` |
| Output file | `corpus/eo_pre1974.json` | `corpus/eo.json` |
| Records created or updated? | **Created.** These orders were not in the corpus. | **Updated.** The record exists; only its text changes. |
| Heading marks in the body | Kept (`#`, `##`) | Dropped, so an order matches its born-digital neighbours |
| Tables in the body | Left as the model's raw HTML | Flattened to plain lines |
| Printed rule lines (`---`) | Kept | Removed, because they corrupt YAML front matter |
| `no-measurable-ink` flag | Off | On |
| Human review app | `qa-ui/`, a local React app | None yet |

Three of those divergences are worth understanding, because they look arbitrary
and are not.

**Why the body-text options differ.** The pre-1974 records are already committed.
Turning table flattening on for them would change published files. So the new
behaviour is switched on only for the post-1974 path, where nothing is committed
yet, and the old path keeps its exact output.

**Why post-1974 turns on `no-measurable-ink`.** The ink-coverage check needs
dark pixels to measure. A grey, low-contrast scan can produce zero of them, and
then the check silently has nothing to say. Real examples exist in the post-1974
set. Without this flag, a page with no coverage check would look exactly like a
page that passed one.

**Why the two sets go to different corpus files.** See section 3: the law scopes
the official deliverable to 1974 onward. Keeping the older records out of
`eo.json` also leaves that file's record count, its shrink guard and its
regression tests untouched.

---

## 9. Quality checks in place

### Checks that both sets run

Every page record carries signals that need no correct answer to compare
against, because there is no correct answer available:

- **`finish_reason`** — `stop` means the model finished its own sentence.
  `length` means it hit the token ceiling, so the page is **truncated** and
  content is missing even though the JSON still parsed.
- **`logprobs`** — the model's own confidence in each word it chose, reported
  in three separate scopes: `content` (what the page says), `layout` (where the
  regions are) and `all`. Layout confidence is routinely low and usually
  harmlessly so, which is why the scopes are kept apart.
- **`ink_coverage`** — how much of the page's ink falls inside a region the
  model actually returned, plus boxes around the ink that does not. **This is
  the only signal that catches dropped content.** A dropped paragraph is
  otherwise invisible: the JSON parses, it just says less than the page does.
- **`parse_error`** — the model's answer was not valid JSON.
- **Blank-page verdicts** — recorded, and cross-checked (see below).

`vlm_pages.page_flags` turns those into named flags: `parse-error`, `truncated`,
`uncovered-ink`, `low-ink-coverage`, `low-confidence`, `decode-mismatch`,
`no-measurable-ink`, plus document-level `no-pages-recorded`, `all-pages-blank`
and `empty-output`.

**Any of these flags on any page forces the whole record to `needs-review`.**
That override exists because the text-quality scoring measures the text that *is*
there. Without the override it would happily call the surviving half of a
truncated order "clean".

Separately, `clean.py` tiers every record as `clean`, `minor-noise` or
`needs-review` from three measures: whether the expected opening anchor phrase
was found, the share of characters that form real words, and the share of junk
characters.

There is also a **blank-page safety net**: if *every* page of a document
classifies as blank, the code overrides the verdict, OCRs the document anyway,
and stamps `blank_override` on the records. A document that reached the corpus
cannot be entirely blank, so a unanimous blank verdict is evidence against the
threshold, not against the document.

### Checks specific to pre-1974

Reported in `pre1974_report.md` and reviewed in the `qa-ui/` app:

- **Index reconciliation.** Each book carries its own subject index. The report
  compares the numbers the index lists against the numbers the splitter
  produced, per book, in `Missing` and `Unindexed` columns. This turns "did the
  split work?" into a number instead of an opinion. On the first book it
  recovered two orders whose numbers the OCR missed.
- **The report states the limits of that check.** Only 3 of the 14 books had an
  index the parser could read, so the other 11 books' counts rest on the
  splitter alone. The report says so plainly rather than hiding it.
- **Book-level flags:** `orphan-page` (a page with content that belongs to no
  order), `duplicate-number` (two orders claiming one number), and series names
  taken from the catalogue rather than the printed label.
- **A census of unreadable pages.** 113 of 2,936 pages produced no usable text,
  and 94 of them are in one book. Every record that covers a failed page is
  forced to `needs-review`.
- **A published quality count.** 653 clean, 40 minor-noise, 285 needs-review,
  out of 978 records.
- **`qa-ui/`** shows the page image with the model's boxes drawn on it, next to
  the text and the QA signals, so a human can check any page.

### Checks specific to post-1974

All in `scripts/report_post1974_ocr.py`. This set has something the older set
does not: **an existing Tesseract transcription to compare against.** Every check
below exploits that.

- **The baseline is a git revision, not a file.** `corpus/eo.json` is committed,
  so the Tesseract text survives its own overwrite. The report reads the newest
  revision in which no record says `ocr-vlm`; `--baseline-rev` names one instead.
  This is also why the report runs at home and not on the rented box.
- **`--stage classify` — the calibration oracle.** A page called blank inside a
  document that Tesseract already got text out of is a **proven** false blank,
  because a blank page cannot produce text. The gate is zero of those, and it is
  a hard failure. The last sweep found **no page classified blank at all**, with
  the fifth-percentile darkness 15 times above the threshold.
- **`--stage ocr`** reports coverage per year and counts every flag raised. A
  document with page records but no usable text is a hard failure.
- **`--stage diff` — the cutover gate.** For each document it compares old
  against new:
  - **Hard failure:** the document had 200 or more characters and now has fewer
    than 200. It lost its text.
  - **Flagged for a human:** the length changed by more than half, either way.
  - **A quality-tier migration table**, marking every document that fell from
    `clean` to `needs-review`. The bar to beat is Tesseract's 923 of 1,019, or
    90.6% clean.
  - **An upside-down-render detector.** 924 of the 1,799 pages carry a stored
    rotation angle. PyMuPDF obeys that angle, so a page whose stored angle is
    *wrong* renders 180 degrees out, and no shape test can see it. A word ratio
    that collapses from above 0.8 to below 0.5 is the tell.
  - **Side-by-side text diffs** for the largest changes, written to
    `sample_vlm_diff/` for a human to read.
- **The script exits non-zero on any hard failure**, so it can gate the cutover
  automatically.
- **The corpus shrink guard** in `build_corpus.py` refuses to overwrite
  `corpus/eo.json` with fewer records than it already holds, unless someone
  passes `--allow-shrink` on purpose.
- **Automated tests:** `tests/test_vlm_pages.py`,
  `tests/test_vlm_ocr_classify.py`, `tests/test_vlm_ocr_rotation.py`,
  `tests/test_vlm_ocr_api.py`, `tests/test_run_post1974_ocr.py`,
  `tests/test_report_post1974_ocr.py`, `tests/test_build_corpus_vlm.py` and
  `tests/test_build_corpus_shrink_guard.py`.

### Rollback, which is a quality control too

The presence of page records is the switch. Delete one document's directory and
rebuild with `--ocr-engine auto`, and its Tesseract text returns. Delete a
year's directory for the same effect at that scale. For the whole migration,
revert the build commit: `corpus/` is committed as plain Markdown, so the old
text comes back byte for byte. That is why the cutover is meant to be one
reviewable commit.

---

## 10. Where each set stands today

**Pre-1974: complete first pass.** All 14 books are OCR'd — 2,936 pages — and
split into **978 records** covering 1946-01-07 to 1973-11-12. The picture layer
(seals and signatures) is clipped and published. What remains is review, not
capture: the 285 `needs-review` records, the 94 unreadable pages in one Lindsay
book, and the 11 books whose index could not be parsed.

**Post-1974: the pipeline is built and the run is the remaining work.** The
selector, the driver, the corpus wiring, the report and the rented-GPU script
all exist and are tested. In this checkout **1 document of the 1,086 has page
records**, so `post1974_ocr_report.md` currently shows one recorded document and
two QA flags on it. The full run is estimated at 2.5 to 4 hours on an RTX 6000
Ada with 4 workers.
