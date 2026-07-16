# EO cleaner — human hand-title worklist (post hybrid-lexicon sweep)

**69 docs** remain tiered `needs-review` after the 2026-07-16 hybrid resolution
of the prior 86-doc queue. Held titles are **NOT** written to frontmatter — a human
sets them. Bodies are intact (verbatim in `full_text_raw` + the source PDF); the
review action is *set the title*, not *fix the text*.

What changed: 16 born-digital + 1 OCR doc auto-recovered once the title-gate lexicon
gained 9 verified real words (women, specified, theatre, coordinator, coordinated,
controlled, adjudicatory, pre, HHS). The mangle guards (Cray, TRANSPL, AMBL, MARY,
RAYMENTS, AGCRUED) still reject — the gate was not widened.

## Pile 2 — OCR docs (41): hand-title from the source PDF

The primary worklist. Each held candidate is an OCR caps line too mangled to trust,
or an `anchor-after-body-start` doc (legible body, no clean header anchor). Verify
against the PDF before setting the title.

| eo_id | reason | held-title candidate |
|---|---|---|
| 1974-EO-004 | title-uncertain | IN ORDER TO CARRY OUT AND PROTECT THE PRINCIPLES WHICH |
| 1974-EO-008 | title-uncertain | REGULATIONS GOVERNING CASH RAYMENTS FOR UNUSED ACCRUED ANNUAL LEAVE AND UNUSED ACCRUED COMPENSATORY TIME ON DEATH OF CERTAIN UNIFORMED FORCES EMPLOYEES WHILE IN THE CITY’S EMPLOY |
| 1974-EO-009 | title-uncertain | REGULATIONS GOVERNING CASH, PAYMENTS FOR UNUSED ‘AGCRUED ANNUAL LEAVE AND UNUSED ACCRUED COMPENSATORY: TIME ON DEATH OF CERTAIN SANITATION SERVICE EMPLOYEES WHILE IN THE CITY’S EMPLOY |
| 1974-EO-011 | title-uncertain | ESTABLISHMENT OF THE CRIMINAL JUSTICE COORDINATING COUNCIL |
| 1975-EO-036 | title-uncertain | RESTRUCTURING YOUTII SERVICES |
| 1975-EO-039 | anchor-after-body-start |  |
| 1975-EO-041 | title-uncertain | DELEGATION OF AUTHORITY TO DEFUTY MAYOR FOR FINANCE |
| 1976-EO-059 | title-uncertain | ESTABLISHMENT OF PROCEDURES FOR THE CENTRALIZED COORDINATION AND DIRECTION OF THE MEDICAID PROGRAM |
| 1977-EO-084 | title-uncertain | IN RELATION TO THE AWARDING OF CONTRACTS UNDER THE EMER- GENCY REPAIR PROGRAM UNDER PUBLIC EMERGENCY CONDITIONS AND IN SPECIAL CASES |
| 1977-EO-089 | title-uncertain | INFORMATION REGARDING APPRENTICESHIP PROGRAMS AND HEALTH, WELFARE AND PENSION PLANS TO BE SUBMITTED WITH EACH BID ON NEW YORK CITY CONSTRUCTION CONTRACTS |
| 1977-EO-091 | title-uncertain | Cray ENVIRONMENTAL QUALITY. REVIEW |
| 1977-EO-096 | title-uncertain | DESIGNATION OF THE EMERGENCY MEDICAL SERVICE AS THE COORDINATING AGENCY FOR EMERGENCY MEDICAL CARE FOR |
| 1978-EO-003 | title-uncertain | DESIGNATION OF THE DEPUTY MAYOR IFOR FINANCE TO SUCCEED TO THE POWERS AND RESPONSIBILITIES OF THE HEALTH SERVICES ADMINIS. TRATION AND THE DEPUTY MAYOR FOR INTERGOVERNMENTAL RELA- TIONS TO SERVE ON THE BOARD OF DIRECTORS OF THE NEW YORK CITY HEALTH AND HOSPITALS CORPORATION |
| 1978-EO-005 | title-uncertain | ESTABLISHMENT OF A SOUTH BRONX COORDINATING COUNCIL |
| 1978-EO-016 | title-uncertain | MMISSIONER OF INVESTIGATION, INSPECTORS GENERAL |
| 1978-EO-020 | title-uncertain | TEE TO |
| 1978-EO-022 | title-uncertain | EFFECTIVENESS OF LOCAL LAW NO. 438 OF 1978 |
| 1978-EO-025 | title-uncertain | SOUTH BRONX COORDINATING COUNCIL |
| 1979-EO-029 | title-uncertain | FEBRURAY 6, 1979 ESTABLISHMENT OF AN AUDIT COMMITTEE FOR THE CITY OF NEW YORK |
| 1979-EO-032 | title-uncertain | OFFICE OF ADMININSTRATIVE TRIALS AND HEARINGS |
| 1979-EO-040 | anchor-after-body-start |  |
| 1980-EO-043 | title-uncertain | MARY |
| 1980-EO-045 | title-uncertain | WAYS |
| 1980-EO-053 | title-uncertain | OFFICE |
| 1980-EO-054 | title-uncertain | CONTRACTS POR CITY OWNED OR MANAGED BUILDINGS UNDER PUBLIC EMERGENCY CONDITIONS |
| 1986-EO-091 | title-uncertain | ANNUAL PINANCIAL REPORTING OF INCOME, ASSETS AND LIABILITIES |
| 1988-EO-115 | title-uncertain | BIAS RESPONSE COORDINATING COMMITTEE |
| 1990-EO-022 | title-uncertain | MAYOR'S OFFICE OF VETERANS! AFFAIRS (MOVA |
| 1992-EO-039 | title-uncertain | TABLISHM |
| 1993-EO-047 | title-uncertain | BROADCASTING |
| 1993-EO-048 | title-uncertain | DOMESTIC PARTNERSHIP REGCISTRATICN PROGRAM |
| 1993-EO-052 | title-uncertain | TABLISHME |
| 1993-EO-057 | title-uncertain | THE MAYOR'S DOMESTIC VIOLENCE COORDINATING COUNCIL |
| 1993-EO-058 | title-uncertain | THE MAYOR’S DOMESTIC VIOLENCE COORDINATING COUNCIL |
| 1994-EO-009 | title-uncertain | OFFICE OF CONTRACTS, AND OF |
| 1994-EO-014 | title-uncertain | TERMINATION OF CONTRACT WITH |
| 1995-EO-020 | title-uncertain | RATOR |
| 1996-EO-027 | title-uncertain | TRANSPL OF AMBL |
| 1996-EO-029 | title-uncertain | DESIGNATION OF FIRE DEPARTMENT AS THE AGENCY RESPONSIBLE FOR THE FILING OF CERTIFICATES OF EXPENSES RELATING TO COSTS INCURRED BY THE DEPARTMENT IN RESPONDING TO EMERGENCIES INVOLVING THE RELEASE OR THREAT OF RELEASE OF HAZARDOUS SUBSTANCES INTO THE ENVIRONMENT |
| 1996-EO-033 | anchor-after-body-start |  |
| 1998-EO-043 | anchor-after-body-start |  |

## Born-digital residue (28): not lexicon-recoverable

These born-digital docs stayed held for reasons lexicon growth cannot fix:

- **Single-token furniture grab** (`CITY`, `OFFICE`): the caps line captured is
  letterhead, not a subject title — 1 meaningful token, below the 2-token floor.
  (1978-EO-010 is a scan misclassified `born-digital`; see the sweep journal.)
- **Encoding mangle** (`OFr1CE`, `OFnCE OF THE MAYOR`): garbled letterhead in the
  PDF text layer — a mangle, correctly held.
- **Truncated extraction** (2004-EO-052): only the first caps line captured; the real
  title continues (`...WNYE TELEVISION AND RADIO STATIONS...`) and carries a call sign.
- **Source typo** (2007-EO-108): the PDF itself misspells `PROCLOMATION` — a genuine
  document defect, deliberately NOT added to the lexicon.

| eo_id | reason | held-title candidate |
|---|---|---|
| 1978-EO-010 | title-uncertain | CITY |
| 2004-EO-052 | title-uncertain | TRANSFER OF FUNCTIONS WITH RESPECT TO THE |
| 2007-EO-108 | title-uncertain | PROCLOMATION OF A STATE OF EMERGENCY |
| 2012-EO-163 | title-uncertain | CITY |
| 2012-EO-169 | title-uncertain | CITY |
| 2012-EO-194 | title-uncertain | CITY |
| 2013-EO-245 | title-uncertain | CITY |
| 2013-EO-247 | title-uncertain | CITY |
| 2013-EO-248 | title-uncertain | CITY |
| 2013-EO-260 | title-uncertain | CITY |
| 2013-EO-262 | title-uncertain | CITY |
| 2013-EO-287 | title-uncertain | CITY |
| 2013-EO-288 | title-uncertain | OFFICE |
| 2013-EO-289 | title-uncertain | OFFICE |
| 2013-EO-290 | title-uncertain | OFr1CE |
| 2013-EO-291 | title-uncertain | OFnCE OF THE MAYOR |
| 2013-EO-292 | title-uncertain | CITY |
| 2013-EO-304 | title-uncertain | CITY |
| 2013-EO-318 | title-uncertain | CITY |
| 2013-EO-319 | title-uncertain | CITY |
| 2013-EO-322 | title-uncertain | CITY |
| 2013-EO-330 | title-uncertain | CITY |
| 2013-EO-360 | title-uncertain | CITY |
| 2013-EO-378 | title-uncertain | CITY |
| 2013-EO-386 | title-uncertain | CITY |
| 2013-EO-406 | title-uncertain | CITY |
| 2013-EO-412 | title-uncertain | CITY |
| 2013-EO-427 | title-uncertain | CITY |

