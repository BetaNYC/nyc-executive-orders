# EO corpus cleaner — WIDE SAMPLE report (round 2)

Deterministic 58-doc stratified sample of the 916 OCR docs. READ-ONLY on `corpus/`; nothing modified, no full sweep. Selection metrics computed by running the cleaner in memory over every OCR record.

- **Lexicon source (title gate): `system:/usr/share/dict/words`** — REPRODUCIBILITY NOTE: freeze this word list into the repo before any full sweep / publish so title accept/reject is host-independent.
- Sample size: **58** (55 OCR + 3 born-digital).

## text_quality distribution (selected sample)

- clean: 21
- minor-noise: 26
- needs-review: 11

## Title lexicon gate (selected OCR docs with an empty title)

- empty-title docs in sample: 42
- **auto-accepted (all tokens recognized): 14**
- **held as title-uncertain (>=1 unrecognized token): 7**
- no caps subject line found at all: 21

Auto-accepted titles:
  - 1974-EO-005: 'APPOINTMENT OF A SPECIAL ASSISTANT TO THE MAYOR TO EXERCISE THE FUNCTIONS, POWERS AND DUTIES IMPOSED BY SECTION 210 OF THE CIVIL SERVICE LAW'
  - 1974-EO-010: "REGULATIONS GOVERNING CASH PAYMENTS FOR UNUSED ACCRUED ANNUAL LEAVE AND UNUSED ACCRUED COMPENSATORY TIME ON DEATH OF CERTAIN CORRECTION SERVICE EMPLOYEES WHILE IN THE CITY'S EMPLOY"
  - 1974-EO-018: 'ESTABLISHMENT OF AN OFFICE OF ELECTRONIC DATA PROCESSING— MUNICIPAL SERVICE ADMINISTRATION'
  - 1975-EO-047: 'BOARD OF ADVISORS FOR SAILORS’ SNUG HARBOR'
  - 1976-EO-064: "ESTABLISHMENT OF THE MAYOR'S MIDTOWN ACTION OFFICE"
  - 1980-EO-049: 'CETA TRANSITION'
  - 1986-EO-101: 'VOTER ASSISTANCE PROGRAM'
  - 1987-EO-109: 'ANNUAL FINANCIAL REPORTING OF INCOME, ASSETS AND LIABILITIES OF CITY OFFICIALS'
  - 1987-EO-110: "ESTABLISHMENT OF THE MAYOR'S OFFICE FOR HOMELESS AND SINGLE ROOM OCCUPANCY (SRO} HOUSING SERVICES"
  - 1995-EO-018: 'ESTABLISHMENT OF COMMISSION TO COMBAT POLICE CORRUPTION'
  - 1998-EO-044: 'TRANSFER OF FUNCTIONS OF THE DIVISION OF SCHOOL SAFETY OF THE BOARD OF EDUCATION TO THE NEW YORK CITY POLICE DEPARTMENT CONSISTENT WITH THE TERMS AND CONDITIONS OF A MEMORANDUM OF UNDERSTANDING'
  - 1999-EO-046: 'DEPUTY MAYORS'
  - 2001-EO-051: 'ESTABLISHMENT OF THE OFFICE OF HEALTH INSURANCE ACCESS'
  - 2013-EO-429: 'COORDINATION AND IMPLEMENTATION OF MATTERS PERTAINING TO STORMWATER CONTROLS AND MUNICIPAL SEPARATE STORM SEWER SYSTEM PERMIT REQUIREMENTS'

Held (surfaced for human review, NOT written):
  - 1974-EO-004: title-uncertain: 'IN ORDER TO CARRY OUT AND PROTECT THE PRINCIPLES WHICH'
  - 1974-EO-008: title-uncertain: 'REGULATIONS GOVERNING CASH RAYMENTS FOR UNUSED ACCRUED ANNUAL LEAVE AND UNUSED ACCRUED COMPENSATORY TIME ON DEATH OF CERTAIN UNIFORMED FORCES EMPLOYEES WHILE IN THE CITY’S EMPLOY'
  - 1974-EO-009: title-uncertain: 'REGULATIONS GOVERNING CASH, PAYMENTS FOR UNUSED ‘AGCRUED ANNUAL LEAVE AND UNUSED ACCRUED COMPENSATORY: TIME ON DEATH OF CERTAIN SANITATION SERVICE EMPLOYEES WHILE IN THE CITY’S EMPLOY'
  - 1977-EO-091: title-uncertain: 'Cray ENVIRONMENTAL QUALITY. REVIEW'
  - 1980-EO-043: title-uncertain: 'MARY'
  - 1994-EO-014: title-uncertain: 'TERMINATION OF CONTRACT WITH'
  - 1996-EO-027: title-uncertain: 'TRANSPL OF AMBL'

## Date extraction (selected OCR docs with null date)

- null-date docs: 42; extracted: 24

## Strata (each doc labelled by first-matched stratum)

- ambiguous: 1
- empty-title: 8
- era-2022-2026: 12
- extra-control: 1
- heavy-noise: 19
- orig-control: 2
- orig-tier1: 12
- orig-tier2: 3

## Controls (born-digital — body must be untouched)

- 1978-EO-010: CHECK (hdr=64, marks=0, quality=minor-noise; gap-filled title)
- 2008-EO-110: PASS (hdr=0, marks=0, quality=clean; gap-filled title+date)
- 2022-EEO-295: PASS (hdr=0, marks=0, quality=clean)

## Outcome table (sorted by eo_id)

| eo_id | stratum | src | quality | anc | hdr | marks | title | date |
|---|---|---|---|---|---|---|---|---|
| 1974-EO-001 | orig-tier1 | ocr | minor-noise | True | 111 | 1 | - | - |
| 1974-EO-002 | empty-title | ocr | minor-noise | True | 32 | 0 | - | - |
| 1974-EO-003 | empty-title | ocr | minor-noise | True | 45 | 0 | - | - |
| 1974-EO-004 | heavy-noise | ocr | needs-review | True | 69 | 0 | unc | - |
| 1974-EO-005 | empty-title | ocr | clean | True | 0 | 0 | acc | y |
| 1974-EO-006 | empty-title | ocr | clean | True | 0 | 1 | - | - |
| 1974-EO-007 | empty-title | ocr | clean | True | 20 | 0 | - | y |
| 1974-EO-008 | empty-title | ocr | needs-review | True | 0 | 0 | unc | y |
| 1974-EO-009 | empty-title | ocr | needs-review | True | 23 | 0 | unc | - |
| 1974-EO-010 | empty-title | ocr | minor-noise | True | 41 | 0 | acc | y |
| 1974-EO-013 | heavy-noise | ocr | minor-noise | True | 59 | 0 | - | y |
| 1974-EO-014 | heavy-noise | ocr | minor-noise | True | 77 | 0 | - | y |
| 1974-EO-018 | orig-tier1 | ocr | minor-noise | True | 169 | 0 | acc | y |
| 1974-EO-025 | heavy-noise | ocr | minor-noise | True | 54 | 0 | - | y |
| 1975-EO-039 | orig-tier1 | ocr | needs-review | False | 0 | 0 | - | - |
| 1975-EO-046 | heavy-noise | ocr | minor-noise | True | 88 | 0 | - | - |
| 1975-EO-047 | heavy-noise | ocr | minor-noise | True | 68 | 0 | acc | y |
| 1976-EO-052 | orig-tier1 | ocr | clean | True | 17 | 0 | - | - |
| 1976-EO-060 | heavy-noise | ocr | minor-noise | True | 60 | 0 | - | y |
| 1976-EO-061 | heavy-noise | ocr | minor-noise | True | 46 | 0 | - | - |
| 1976-EO-064 | orig-tier1 | ocr | clean | True | 0 | 0 | acc | y |
| 1977-EO-091 | orig-tier1 | ocr | needs-review | True | 149 | 1 | unc | y |
| 1977-EO-093 | heavy-noise | ocr | minor-noise | True | 55 | 0 | - | - |
| 1978-EO-006 | heavy-noise | ocr | minor-noise | True | 54 | 0 | - | y |
| 1978-EO-010 | extra-control | born | minor-noise | True | 64 | 0 | acc | - |
| 1978-EO-017 | orig-tier1 | ocr | minor-noise | True | 26 | 0 | - | - |
| 1979-EO-040 | orig-tier1 | ocr | needs-review | False | 0 | 1 | - | - |
| 1980-EO-043 | orig-tier1 | ocr | needs-review | True | 46 | 0 | unc | - |
| 1980-EO-049 | orig-tier2 | ocr | minor-noise | True | 59 | 0 | acc | y |
| 1986-EO-097 | heavy-noise | ocr | minor-noise | True | 53 | 0 | - | y |
| 1986-EO-101 | orig-tier1 | ocr | minor-noise | True | 71 | 0 | acc | - |
| 1987-EO-109 | heavy-noise | ocr | minor-noise | True | 47 | 0 | acc | y |
| 1987-EO-110 | heavy-noise | ocr | minor-noise | True | 103 | 0 | acc | y |
| 1994-EO-002 | heavy-noise | ocr | minor-noise | True | 66 | 0 | - | y |
| 1994-EO-014 | heavy-noise | ocr | needs-review | True | 75 | 1 | unc | y |
| 1995-EO-018 | heavy-noise | ocr | minor-noise | True | 76 | 1 | acc | - |
| 1996-EO-027 | orig-tier1 | ocr | needs-review | True | 64 | 0 | unc | y |
| 1996-EO-033 | ambiguous | ocr | needs-review | False | 0 | 0 | - | - |
| 1998-EO-043 | orig-tier1 | ocr | needs-review | False | 0 | 0 | - | - |
| 1998-EO-044 | heavy-noise | ocr | minor-noise | True | 80 | 0 | acc | y |
| 1999-EO-046 | heavy-noise | ocr | minor-noise | True | 65 | 0 | acc | y |
| 2001-EO-051 | heavy-noise | ocr | minor-noise | True | 51 | 0 | acc | y |
| 2008-EO-110 | orig-control | born | clean | True | 0 | 0 | acc | y |
| 2013-EO-429 | orig-tier2 | ocr | clean | True | 0 | 0 | acc | y |
| 2022-EEO-125 | orig-tier2 | ocr | clean | True | 0 | 0 | kept | kept |
| 2022-EEO-167 | era-2022-2026 | ocr | clean | True | 3 | 0 | kept | kept |
| 2022-EEO-196 | era-2022-2026 | ocr | clean | True | 3 | 0 | kept | kept |
| 2022-EEO-238 | era-2022-2026 | ocr | clean | True | 15 | 0 | kept | kept |
| 2022-EEO-274 | era-2022-2026 | ocr | clean | True | 13 | 0 | kept | kept |
| 2022-EEO-275 | era-2022-2026 | ocr | clean | True | 9 | 0 | kept | kept |
| 2022-EEO-295 | orig-control | born | clean | True | 0 | 0 | kept | kept |
| 2022-EEO-298 | era-2022-2026 | ocr | clean | True | 9 | 0 | kept | kept |
| 2023-EEO-357 | era-2022-2026 | ocr | clean | True | 15 | 0 | kept | kept |
| 2023-EEO-380 | era-2022-2026 | ocr | clean | True | 6 | 0 | kept | kept |
| 2023-EO-034 | era-2022-2026 | ocr | clean | True | 5 | 0 | kept | kept |
| 2025-EO-054 | era-2022-2026 | ocr | clean | True | 5 | 0 | kept | kept |
| 2025-EO-064 | era-2022-2026 | ocr | clean | True | 13 | 0 | kept | kept |
| 2026-EO-014 | era-2022-2026 | ocr | clean | True | 8 | 0 | kept | kept |
