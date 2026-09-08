# Running VLM OCR on the post-1974 scans

How to replace the Tesseract text for the post-1974 scanned executive orders with
the local `dots.ocr` VLM.

**Scope:** 1,086 documents, 1,799 pages — every post-1974 PDF that is image-only.
Born-digital orders are never touched: they have a real text layer, which is
already byte-faithful, and re-OCR could only add error.

Check what is left at any time:

```bash
uv run python scripts/run_post1974_ocr.py --status
```

---

## 1. Run the OCR

On an NVIDIA card:

```bash
uv run --extra vlm-cuda python scripts/run_post1974_ocr.py \
  --device cuda --quantization none --workers 4 --progress-every 25
```

On Apple Silicon:

```bash
uv run --extra vlm python scripts/run_post1974_ocr.py --device mlx
```

The driver selects the scanned documents itself, loads the model once per worker,
and writes committed page records to `sources/ocr/<year>/<eo_id>/page_XXXX.json`.
Scratch renders go to `vlm-ocr-runs/post1974/` and logs to
`vlm-logs/post1974/worker-N.log`; both are gitignored.

**Interrupting is safe.** Re-run the same command and it resumes at the first
missing page. Grinding through the backlog is just re-running it.

There is no authorization gate. This is local compute over files already on disk;
the only network call is the one-time weight download, which the parent process
does once before the workers start.

---

## 2. Build the corpus

```bash
uv run python scripts/run_parse.py --ocr-engine vlm
```

Loads no model and takes seconds — it only reads the committed page records.

To rebuild while OCR is still running, use `--ocr-engine auto` instead. Documents
that have records use them; documents that do not keep their Tesseract text. The
corpus stays complete, and `corpus/manifest.csv` shows how far the migration has
got.

| `--ocr-engine` | records present | records absent |
|---|---|---|
| `auto` | VLM | Tesseract |
| `vlm` | VLM | `ocr-skipped` (never falls back) |
| `tesseract` | ignored | Tesseract (the rollback path) |

---

## 3. Check it before you keep it

```bash
uv run python scripts/report_post1974_ocr.py --stage diff --samples 20
```

Writes `post1974_ocr_report.md`, and side-by-side diffs for the 20 largest changes
into `sample_vlm_diff/`. It **exits non-zero** if any document lost text it used
to have.

There is nothing to prepare in advance. Step 2 overwrites the Tesseract text in
`corpus/eo.json`, but that file is committed, so git still holds every earlier
version of it. The report reads the newest revision in which no record yet says
`ocr-vlm`, measures the old metrics from it, and takes the Tesseract side of each
sample diff from it.

Two things follow. Run the report **at home**, in the checkout, not on a rented
box that holds an rsync'd file subset with no `.git`. And commit the Tesseract
corpus before you rebuild over it, which it already is.

Name the baseline yourself if the search picks the wrong commit:

```bash
uv run python scripts/report_post1974_ocr.py --stage diff --samples 20 \
  --baseline-rev 29207696
```

---

## Measure the speed before committing a rented card

Run one year and read the live `s/page` from the progress line, then multiply by
1,799.

```bash
uv run --extra vlm-cuda python scripts/run_post1974_ocr.py \
  --year 1974 --device cuda --quantization none --workers 4 --progress-every 5
```

Raise `--workers` and re-check. If `s/page` stops improving you have hit the
memory-bandwidth wall, and more workers only add memory pressure.

**Estimate for an RTX 6000 Ada:** ~2.5-4 hours with 4 workers; ~6-7 hours with
one. Based on measured work per page (5,166 image tokens of prefill, ~700-1,030
generated tokens) against the card's 960 GB/s. Not measured on that card —
measure it yourself with the command above.

**Use `--quantization none`.** The 8-bit default and the "4-bit is faster" note in
this repo both come from a small card where bitsandbytes dequantization dominates.
When 48 GB is not the constraint, bf16 is faster per worker.

Workers scale **sub-linearly**. Decode re-reads the whole model per token per
process, so four batch-1 processes want four times the bandwidth. Expect roughly
2-2.5× from 4 workers. Budget ~10 GB of VRAM each: 4 on an RTX 6000 Ada, 8 on an
H100 or RTX 6000 Blackwell.

---

## Useful flags

| Flag | Use |
|---|---|
| `--year 2023` | one signing year (repeatable) |
| `--since-year 1974 --until-year 2001` | a range |
| `--limit 20` | try a few documents first |
| `--dry-run` | print the worklist and shard split, then exit |
| `--status` | per-year ledger of done / part-done / left |
| `--eo-id 2023-EEO-302 --force` | redo one document |
| `--workers auto` | size from free VRAM |
| `--gpus 0,1` | spread workers over several cards |
| `--overlays` | write bbox overlay PNGs (off by default) |
| `--keep-renders all` | keep every page render (default keeps only flagged) |

`--status` and `--dry-run` import no backend, so they run anywhere.

---

## Rollback

The presence of page records is the switch.

| scope | how |
|---|---|
| one document | `rm -r sources/ocr/<year>/<eo_id>/`, then rebuild with `--ocr-engine auto` |
| one year | same, scoped to `sources/ocr/<year>/` |
| the whole migration | `git revert` the build commit — `corpus/` is committed plain Markdown, so the Tesseract text comes back byte-exact |
| re-derive | `run_parse.py --ocr-engine tesseract --operator-authorized` |

Because of the last row, do **not** delete `ocr.py`, the `ocr` extra, or
`tests/test_ocr.py`. Re-running Tesseract would not reproduce the old text anyway
across Tesseract and Ghostscript version drift, so the git history is the real
rollback — which is why the cutover should be one reviewable commit.

---

## Notes on what to expect

**Blank-page detection needs no tuning.** A sweep over all 1,799 pages found zero
pages classified blank, with the p05 dark-fraction 15× above the threshold. The
shipped values stand. If you re-run the sweep
(`--classify-blank-only`), the gate is that no page is called blank in a document
that already has Tesseract text — a blank page cannot have produced text.

**A one-page order cannot be lost to a false blank.** 764 of the 1,086 targets are
a single page. If every page of a document classifies blank, the driver clears the
skips, OCRs it anyway, and stamps `blank_override` on the records.

**`low-ink-coverage` may be noisy here, and it forces `needs-review`.** On the two
pilot documents the uncovered ink was a handwritten archival annotation and the
letterhead's own printed rules — ink on the page, but not order text. Both
documents improved on word-ratio and junk-ratio yet still migrated
`minor-noise` → `needs-review`. Watch the tier-migration table in the report. If
most documents regress this way, the flag is the problem, not the text.

The printed-rules half of that is now handled: rule ink is cut out of the mask
before regions are grouped, and out of the coverage fraction. A masthead line or
a column border is ink the model was right not to transcribe. Detection is
local, because these rules are scanned and wander — measured on real pages, a
rule is inked across 100% of a 144px window with a 2-5px stroke, while a line of
body type covers 65-94% with a 5-9px stroke. Cutting the rule early matters
twice: it also stops an intact rule chaining distant ink into one group whose
box spans half the page. Reported boxes now hug their own ink rather than the
24px cell grid. About 30% of all uncovered ink on the flagged post-1974 pages
turned out to be rules. The handwritten annotations are not handled, and they
became MORE visible at the same time — coverage now measures
the full page height, so a header or footer counts in both the numerator and the
denominator instead of being cropped out of both. `MIN_COVERED_FRACTION` in
`vlm_pages.py` was set against the old cropped measurement and may want
re-tuning against the new one.

Records written before both changes are brought up to date without re-OCR — the
metric needs only the page raster and the bboxes already in the record:

```bash
uv run python scripts/recompute_ink_coverage.py --dry-run   # see the effect
uv run python scripts/recompute_ink_coverage.py             # rewrite the records
```
