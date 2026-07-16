# EO corpus cleaner — SAMPLE report

Deterministic, rule-based, non-destructive cleaner run on a fixed ~17-doc stratified sample. READ-ONLY on `corpus/`; no corpus files were modified. Per-doc diffs are in this directory (`sample_clean_report/<eo_id>.md`).

## Outcome table

| eo_id | kind | source | quality | anchor | hdr chars | marks | title extr | date extr |
|---|---|---|---|---|---|---|---|---|
| 1976-EO-052 | tier1 | ocr | **needs-review** | True | 17 | 0 | False | False |
| 1975-EO-039 | tier1 | ocr | **needs-review** | False | 0 | 0 | False | False |
| 1996-EO-027 | tier1 | ocr | **needs-review** | True | 64 | 0 | False | True |
| 1976-EO-064 | tier1 | ocr | **clean** | True | 0 | 0 | True | True |
| 1979-EO-040 | tier1 | ocr | **needs-review** | False | 0 | 1 | False | False |
| 1980-EO-043 | tier1 | ocr | **needs-review** | True | 46 | 0 | False | False |
| 1998-EO-043 | tier1 | ocr | **needs-review** | False | 0 | 0 | False | False |
| 1978-EO-017 | tier1 | ocr | **minor-noise** | True | 26 | 0 | False | False |
| 1974-EO-018 | tier1 | ocr | **minor-noise** | True | 169 | 0 | True | True |
| 1977-EO-091 | tier1 | ocr | **minor-noise** | True | 149 | 1 | True | True |
| 1974-EO-001 | tier1 | ocr | **minor-noise** | True | 111 | 1 | False | False |
| 1986-EO-101 | tier1 | ocr | **minor-noise** | True | 71 | 0 | False | False |
| 1980-EO-049 | tier2 | ocr | **minor-noise** | True | 59 | 0 | True | True |
| 2013-EO-429 | tier2 | ocr | **clean** | True | 0 | 0 | True | True |
| 2022-EEO-125 | tier2 | ocr | **clean** | True | 0 | 0 | False | False |
| 2022-EEO-295 | control | born-digital | **clean** | True | 0 | 0 | False | False |
| 2008-EO-110 | control | born-digital | **clean** | True | 0 | 0 | True | True |

## Aggregate

- Docs in sample: 17
- OCR docs: 15; born-digital controls: 2
- Anchor found (header trimmed or confirmed clean): 14/17
- Title extracted where it was empty: 5/14
- Date extracted where it was null: 6/14
- text_quality == clean: 5
- text_quality == minor-noise: 6
- text_quality == needs-review: 6

## Controls check (body must be untouched)

A control PASSES when the cleaner relocated NOTHING out of the body (no header trim, no marks) and tiered it `clean`. Filling an *empty* title/date on a born-digital doc from its own body is a correct gap-fill, not a violation — noted separately.

- 2022-EEO-295: PASS (hdr=0, marks=0, quality=clean)
- 2008-EO-110: PASS (hdr=0, marks=0, quality=clean; gap-filled title+date (was empty))

## Docs flagged needs-review

- 1976-EO-052 (tier1): flags=["title-uncertain: 'FEBRUARY a5 1976'", 'date-not-extracted']
- 1975-EO-039 (tier1): flags=["anchor-after-body-start: earliest anchor sits below the order's opening clause; no clean header anchor found; not trimmed (conservative keep)", 'title-not-extracted', 'date-not-extracted']
- 1996-EO-027 (tier1): flags=["title-uncertain: 'TRANSPL OF AMBL'"]
- 1979-EO-040 (tier1): flags=["anchor-after-body-start: earliest anchor sits below the order's opening clause; no clean header anchor found; not trimmed (conservative keep)", 'title-not-extracted', 'date-not-extracted']
- 1980-EO-043 (tier1): flags=["title-uncertain: 'MARY'", 'date-not-extracted']
- 1998-EO-043 (tier1): flags=["anchor-after-body-start: earliest anchor sits below the order's opening clause; no clean header anchor found; not trimmed (conservative keep)", 'title-not-extracted', 'date-not-extracted']
