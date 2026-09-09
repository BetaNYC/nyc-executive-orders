# Running VLM OCR on the OCR-layer scans

How to replace the second-hand OCR text for the executive orders that reached the
corpus tagged `born-digital` while being scans.

**This job has NOT been run.** Everything below is prepared and measured; the
last step provisions billed hardware and is the operator's call.

**Scope:** 680 documents, 1,049 pages. 666 of them carry `text_source:
ocr-layer` — a page image with somebody else's OCR stamped over it invisibly —
and 14 are genuinely born-digital documents that nevertheless hold a scanned
page. That is 63% of the first run's document count and 58% of its pages.

Check what is left at any time:

```bash
uv run python scripts/run_post1974_ocr.py --status
```

---

## Why these documents

`textlayer.classify_pdf` used to decide "born-digital" from one number: the mean
extracted characters per page. That cannot separate a word processor's output
from a scan someone already ran through OCR — both are dense in characters.
665 records were mislabelled, and `vlm_corpus.probe_record` excluded them from
the OCR worklist for exactly that reason.

The probe now also reads the PDF text render mode. Mode 3 is invisible, which is
the signature of an OCR overlay, and the share is bimodal: of 1,205 PDFs with a
text layer, 666 measure essentially 1.0 and 539 essentially 0.0, with 5 anywhere
between. See `docs/born-digital-audit.md` and `textlayer.py`.

The text those 666 records publish today is real and readable. It is simply not
the document — it is a machine's guess at a photograph, of unknown vintage. 228
of them read `$` where `§` belongs.

---

## 1. Pre-flight: the blank-page gate

**Run this first. It loads no model and needs no GPU.**

```bash
uv run python scripts/run_post1974_ocr.py --classify-blank-only
```

A page wrongly classified blank is skipped, and its content is lost in silence.
The first run found **zero** blank pages in 1,799. This population is different:

```
3/1049 page(s) across 680 document(s) classify as blank.
  2006-EO-093 page 2      2022-EEO-230 page 2      2022-EO-007 page 2
```

All three are the last page of a two-page order, and **two of the three carry
real content**:

| page | extracted text | dark_fraction |
|---|---|---:|
| `2006-EO-093` p2 | `- 2 -` — a page number, genuinely near-blank | 0.0 |
| `2022-EEO-230` p2 | a signature rule, `Eric Adams`, `Mayor` | 0.00076 |
| `2022-EO-007` p2 | `§ 5. This Order shall take effect immediately.` + signature | 0.0 |

The ink thresholds were calibrated on scanned pages, where a real page is dense.
A sparse born-digital signature page falls under them. `2022-EO-007` is decisive:
it renders `§` correctly, which second-hand OCR does not, so that page is
genuinely born-digital text that the blank classifier would throw away.

**Do one of these before the run:**

1. **Lower the floor.** Verified: `--min-dark-fraction 0` gives
   `0/1049 page(s) ... classify as blank`. Pass the same flag to the real run.
   This is the recommended option — nothing in this population is a scanner
   artefact that the floor was protecting against, because every one of these
   PDFs already has a text layer.
2. Exclude the three with `--eo-id`, leaving their existing text alone.
3. Run as-is and rely on stage 2 catching the shrink. Least preferred: the
   pre-flight is free and the GPU is not.

---

## 2. Run the OCR

Identical to the first run — the driver picks its own worklist and there is no
new orchestration:

```bash
uv run --extra vlm-cuda python scripts/run_post1974_ocr.py \
  --device cuda --quantization none --workers 4 --progress-every 25 \
  --min-dark-fraction 0
```

The first run measured 4h02m for 1,086 documents / 1,799 pages on an RTX 6000
Ada with 4 workers. At 1,049 pages expect roughly **2.5 hours**, and size the
rented time against `MAX_HOURS` in `execute-post-1974-ocr.sh` accordingly.

Interrupting is safe; re-running resumes at the first missing page.

---

## 3. Build the corpus

```bash
uv run python scripts/run_parse.py --ocr-engine vlm
```

Seconds, no model. Records with page records become `ocr-vlm`; records without
keep their current text. Nothing is stubbed out: `parse_record` has a dedicated
`CLASS_OCR_LAYER` branch precisely so a part-finished run cannot replace a real
body with `_No text available_`.

---

## 4. The cutover gate

**Name the baseline explicitly.** The default search finds the newest revision
still holding text tagged with `--baseline-sources`, which is correct — but this
population's baseline is one specific commit and there is no reason to let a
heuristic pick it.

```bash
uv run python scripts/report_post1974_ocr.py --stage diff \
    --baseline-sources ocr-layer \
    --baseline-rev d7a4cbe3 \
    --samples 20
```

`d7a4cbe3` is the corpus rebuild that established `ocr-layer` as a provenance
value. It is the last revision in which these 666 records hold their old text.

The gate hard-fails on any document that had text and lost it
(`LOST_TEXT_CHARS = 200`), and now also on an empty comparison — a report that
compared nothing is not a report that passed.

Read `sample_vlm_diff/` by hand. The cheapest single check that the whole chain
worked:

```bash
uv run python -c "
import json
r = {x['eo_id']: x for x in json.load(open('corpus/eo.json'))}
print(r['2025-EO-057']['full_text'][:60])"
```

`2025-EO-057` publishes a body beginning at `$ 2.` today, because its entire
first page is a scan the text layer could not see. After this run it must begin
at `EXECUTIVE ORDER`. That one string is end-to-end proof that the gate, the
per-page count, the tier and the provenance all did their jobs.

---

## Rollback

Unchanged from the first cutover: the presence of page records is the switch.

| scope | how |
|---|---|
| one document | `rm -r sources/ocr/<year>/<eo_id>/`, then rebuild |
| one year | same, scoped to `sources/ocr/<year>/` |
| the whole migration | `git revert` the build commit — `corpus/` is committed Markdown, so the old text returns byte-exact |

There is no Tesseract fallback for this population and there never was: the text
being replaced comes from the PDF's own layer, so `git revert` is the only
rollback. Keep the cutover as one reviewable commit.

---

## What to expect

**47 records are already `needs-review` for a lost page.** They hold 59 pages
that never reached a published body, and 55 of those pages carry ink by
`vlm_ocr.page_ink_stats`. This run is what recovers them, so expect a large
`needs-review -> clean` migration in the stage 2 table — the opposite direction
from the first cutover.

**Expect the `$`-for-`§` damage to disappear.** 228 of the 666 read `$ 2.` where
`§ 2.` belongs. A word processor never writes that; an OCR engine does it
constantly, and it is the single clearest before/after signal in the diffs.

**Expect run-on paragraphs to get worse, not better, and do not be alarmed.**
`extract.py` recovers paragraph breaks from the printed line leading, and
`vlm_pages` does not — it builds its body from structured page elements. 754 of
the 1,079 existing `ocr-vlm` bodies have no blank line anywhere. These 666 will
join them until `vlm_pages.page_lines` gets the same treatment. That is a known,
separate piece of work, not a regression from this run.
