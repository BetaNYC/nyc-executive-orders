# Audit — the born-digital PDF parsing pipeline

> **RESOLVED 2026-09-09.** Every finding below is fixed in code; the numbers are
> left as measured on 2026-09-01 so the before/after is legible. Two corrections
> found while fixing them are marked inline. What remains is the OCR run itself
> — 680 documents, prepared and not executed:
> [`ocr-layer-cutover-instructions.md`](ocr-layer-cutover-instructions.md).

Date: 2026-09-01. Scope: `textlayer.py` (the gate) → `extract.py` (the
extraction) → `clean.py` (the tiering) → `build_corpus.py` (the emit).

Every number below is measured. I probed all 1,205 born-digital records in
`corpus/eo.json` against their own PDFs, and rendered the suspect pages to check
them for ink. Nothing here is estimated.

The jank is real. It has one root cause and seven consequences.

---

## The root cause

`textlayer.classify_pdf` decides "born-digital" from **one number**: the mean
count of extracted characters per page (`textlayer.py:104-105`, threshold at
`textlayer.py:35`).

That number cannot separate two very different things:

- a PDF that a word processor wrote, whose text layer **is** the document;
- a **scan** that somebody already ran through OCR, whose text layer is a
  machine's guess at a photograph.

I probed every born-digital PDF for invisible text (PDF render mode 3). That is
the exact signature of an OCR layer stamped over a page image.

| Group | Records | Tiered `clean` |
|---|---:|---:|
| Text layer is ≥90% **invisible** — a scan with somebody else's OCR | **665** | 634 |
| Mixed visible and invisible | 5 | — |
| Fully visible text — genuinely born-digital | 535 | 483 |

**55% of the "born-digital" corpus is not born-digital.**

Two places in the code state the opposite. The `extract.py` module docstring
calls this text "fast, faithful". `vlm_corpus.probe_record`
(`vlm_corpus.py:107-109`) excludes it from the post-1974 VLM run for that
reason. Neither statement holds for those 665 records.

The text itself proves the split. I counted defects in each group:

| Defect | OCR-layer (665) | Genuine (535) |
|---|---:|---:|
| `§` read as `$` | **228** | 8 |
| Soft hyphen U+00AD left inside a word | 0 | 135 |
| Letter-spaced words (`T H E  C I T Y`) | 1 | 21 |

The `§` → `$` split is decisive. A word processor never writes `$ 2.` where
`§ 2.` belongs. An OCR engine does it constantly.

---

## Findings, worst first

### 1. 55 pages of real content are missing from published bodies

`classify_pdf` averages over pages. A 3-page PDF whose page 1 holds 2,000
characters and whose pages 2 and 3 hold none still passes the gate.
`extract_pdf_text` (`extract.py:89`) then emits the empty pages as nothing, and
no code path says so.

I found **48 multi-page documents** holding **59 text-empty pages**.
*(Confirmed: `textlayer.classify_pdf` now reports exactly 48/59 through
`image_only_pages`. 52 of the pages extract zero characters and 7 extract 15-93,
all below the 100-chars/page gate.)* I rendered
each of those pages and measured its ink with the project's own
`vlm_ocr.page_ink_stats`. **Only 4 are blank paper. 55 carry ink.** 47 of the 48
documents are tiered `clean`.

The worst cases:

```
2026-EO-013   6 pages, chars/page [1968, 0, 0, 0, 0, 0]   tier=clean
2026-EO-006   5 pages, chars/page [0, 0, 0, 0, 2272]      tier=clean
2022-EEO-224  3 pages, chars/page [0, 2973, 0]            tier=clean
2022-EO-004   3 pages, chars/page [1775, 0, 0]            tier=clean
2026-EO-016   3 pages, chars/page [1635, 0, 0]            tier=clean
2025-EEO-898  5 pages, chars/page [2189, 0, 3320, 2962, 908]  tier=clean
2025-EO-057   2 pages, chars/page [0, 1185]               tier=clean
```

`2025-EO-057` publishes a body that starts in the middle of the order, at
`$ 2.` Page 1 is gone. The record says `clean`.

### 2. `clean._tier` exempts born-digital text from the check that would catch this

Line 759 of `clean.py` reads:

```python
if not anchor_found and not born_digital:
    return TEXT_QUALITY_REVIEW
```

The anchor test — does the body contain "EXECUTIVE ORDER", "OFFICE OF THE
MAYOR", and so on — is the only **structural** check in the whole tier. It is
switched off for exactly the population that now needs it.

Three born-digital bodies have no anchor at all. All three are broken. None is
tiered `needs-review`:

| Record | Problem | Tier |
|---|---|---|
| `2025-EEO-853` | Broken font map. The body reads `Jo,(uIi{ / slu€pv cug / *wY:1?` for 2,354 characters. | `minor-noise` |
| `2025-EEO-854` | The same broken font map. | `minor-noise` |
| `2025-EO-057` | The missing first page above. | `clean` |

### 3. The quality metrics cannot see any of this

`_word_ratio` and `_junk_ratio` are saturated. They give the same answer for
clean text, for second-hand OCR, and for Tesseract output:

```
ocr-layer      n= 665   word_ratio median=0.989   junk median=0.0000
genuine        n= 535   word_ratio median=0.987   junk median=0.0005
tesseract-ocr  n=1019   word_ratio median=0.988   junk median=0.0000
```

> **Correction.** The conclusion is right and the remedy was not obvious: I first
> concluded no text statistic separates these populations, having tested six
> hand-rolled ones. The repo's own frozen 234,547-word lexicon — already used for
> the title gate — does separate them, 0.0129 / 0.0262 / 0.0115 out-of-vocabulary
> against word-ratio's flat 0.987 / 0.989 / 0.988, and it rates the two
> broken-font-map records 0.873 and 0.826 where word-ratio rates them 0.848.

`_english_like` accepts any token that holds a vowel and has no run of five
consonants. `Intergovemmental`, `em1ss1ons` and `Di.Pease` all pass it. The
metric separates nothing. That is why total garbage scores 0.848 — still above
the 0.70 review floor, and still below the 0.15 junk ceiling.

### 4. 671 bodies render as one run-on paragraph

PyMuPDF returns one line per printed line. `extract.clean_text` collapses three
or more newlines into one blank line, but it never **adds** one. Where the
source PDF puts no extra leading between paragraphs, the body ends up with zero
`\n\n`, and Markdown then joins every line into a single block.

- **671** of the 1,203 bodies over 400 characters have **zero** blank lines.
- **334** more have fewer than one blank line per 1,500 characters.

`2022-EO-023` is 7,337 characters with 3 blank lines. Every WHEREAS clause
renders as one paragraph.

### 5. Soft hyphens are never handled, in either direction

> **Correction.** The second bullet below is misattributed. The glued
> `person-toperson` form is not a soft-hyphen fault — it is `_DEHYPHEN_RE`
> deleting a hyphen that belongs to a genuine compound, because the rule tested
> the next character's capitalization instead of asking whether the two halves
> are words. Its own docstring's example was broken the same way:
> `clean_text("a public-\nprivate deal")` returned `a publicprivate deal`. Both
> are fixed by joining only when `lexicon.recognize` rejects at least one half.

`_DEHYPHEN_RE` (`extract.py:39`) matches `-\n` only. U+00AD is invisible and is
not `-`. Two outcomes follow, and both are wrong:

- **135 documents** publish the character verbatim, so a search for
  `person-to-person` misses the text;
- **30 documents** publish the glued form `person-toperson`, because the layout
  put no newline at that point.

### 6. The record's own provenance is misleading

665 records carry `text_source: born-digital`. The rollback logic, the VLM
selector and any downstream consumer read that tag as "byte-faithful, never
re-OCR". Those records are in fact second-hand OCR of unknown vintage and
unknown quality.

### 7. Titles are thin, and nothing flags it

| Title state | Records |
|---|---:|
| A real subject line | 612 |
| The generic fallback `Executive Order N` | 385 |
| `null` | 208 |

Only half the born-digital records carry the printed subject. No flag
distinguishes "this order has no caption" from "the extractor missed it".

---

## What to fix, in order

1. **Split the gate.** Add a second probe beside the character count: the
   invisible-text share and the full-page image coverage. Return a third class,
   `scanned-with-ocr-layer`. This is about 15 lines in `textlayer.py`, and it
   makes every finding above tractable.
2. **Make the gate per-page, not per-file.** A page under the threshold inside a
   passing document must raise a flag, not vanish. This step alone recovers the
   55 lost pages.
3. **Remove the `born_digital` exemption in `_tier`** (`clean.py:759`). Or
   restrict the exemption to the genuinely-visible-text group, once step 1 gives
   you that label.
4. **Send the 665 through the VLM pipeline.** They are scans. The post-1974
   machinery already handles exactly this case, and
   `report_post1974_ocr.py --stage diff` gives the same before-and-after gate.
   This is the largest single quality win available, and the code already
   exists.
5. **Strip U+00AD in `clean_text`** and join the two halves of the word. One
   line.
6. **Insert a paragraph break** where the source PDF's line leading jumps. The
   `dict` extraction mode already carries the line geometry needed to do this.

## Test coverage today

`tests/test_extract.py` holds 5 cases. `tests/test_textlayer.py` holds 6. All
run against synthetic fixtures. **None** covers a scan with an OCR layer, a
mixed-page PDF, or a broken font map — the three failure modes this audit
found.
