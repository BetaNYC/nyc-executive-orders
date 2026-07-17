# Supersession graph — extraction report

Corpus records: **2192**  |  Edges: **239**  |  Dangling citations: **142**  |  Extension citations skipped: **1536**

Deterministic, rule-based extraction (no LLM, no network). Body citations resolve year-scoped from the cited date; the containing order is the actor. Header XREFs (`X AMENDED BY Y`) make Y the actor.

## Edges by verb and provenance

| verb | body-citation | header-xref | total |
|---|---:|---:|---:|
| amended | 90 | 1 | 91 |
| repealed | 58 | 0 | 58 |
| rescinded | 5 | 0 | 5 |
| revoked | 81 | 0 | 81 |
| superseded | 4 | 0 | 4 |
| **all** | 238 | 1 | 239 |

Of these, **55** are section/paragraph-scoped (partial) edits — recorded as edges but excluded from the `in_effect` computation (a partial repeal does not take an order out of force).

## `in_effect` distribution

Regular EOs only carry a computed value; emergency EOs are `null` in v1 (expiry by operation of law is out of scope).

| value | all | regular only |
|---|---:|---:|
| false | 136 | 136 |
| null | 2056 | 817 |
| true | 0 | 0 |

`false` regular EOs = wholly revoked/superseded by a resolvable in-corpus order. No EO is set `true` (we never assert a historical order is still in force without a principled basis).

## Dangling citations (resolved target not in corpus)

Recorded here, never written into the fields. `not-in-corpus` = the cited order was never archived; `no-year` = the citation carried no date, so it could not be year-scoped (never guessed by number alone).

- **implausible-year**: 2
- **no-year**: 88
- **not-in-corpus**: 52

Distinct unrecoverable targets (most-cited first):

- `1968-EO-071` — cited 5×
- `1970-EO-023` — cited 4×
- `1971-EO-031` — cited 3×
- `1965-EO-178` — cited 3×
- `1971-EO-074` — cited 2×
- `1970-EO-021` — cited 2×
- `1968-EO-084` — cited 2×
- `1970-EO-020` — cited 2×
- `1973-EO-074` — cited 2×
- `1966-EO-009` — cited 2×
- `1967-EO-038` — cited 2×
- `1969-EO-109` — cited 2×
- `1970-EO-007` — cited 2×
- `2018-EO-031` — cited 1×
- `2020-EO-056` — cited 1×
- `1973-EO-075` — cited 1×
- `1957-EO-038` — cited 1×
- `1962-EO-024` — cited 1×
- `1970-EO-030` — cited 1×
- `1966-EO-005` — cited 1×
- `1968-EO-070` — cited 1×
- `1966-EO-027` — cited 1×
- `1967-EO-051` — cited 1×
- `1970-EO-004` — cited 1×
- `1972-EO-057` — cited 1×
- `1970-EO-028` — cited 1×
- `1964-EO-116` — cited 1×
- `1973-EO-083` — cited 1×
- `1969-EO-099` — cited 1×
- `1976-EO-038` — cited 1×
- `1986-EO-014` — cited 1×
- `1973-EO-081` — cited 1×

## Establishes-entity

Auto-written (exact, unambiguous registry match, one per order): **1**. Review candidates: **29**.

### Auto-written

| eo_id | matched name | registry id |
|---|---|---|
| 1984-EO-077 | Office of Payroll) Administration | `office-of-payroll-administration` |

### Review candidates (NOT auto-written)

| eo_id | extracted name | reason |
|---|---|---|
| 1975-EO-027 | Commission for Cultural Affairs | no-match |
| 1976-EO-059 | Medicaid Task Force | no-match |
| 1976-EO-065 | Interagency Committee on Minority Business Development | no-match |
| 1977-EO-088 | special commission of inquiry into energy failures | no-match |
| 1991-EO-032 | Increase the Peace Corps | no-match |
| 1992-EO-037 | Advisory Board to Promote Fair Housing | no-match |
| 1992-EO-042 | Commission | no-match |
| 1992-EO-046 | MEAP Consortium | no-match |
| 1993-EO-054 | Business Emergency Task Force | no-match |
| 1993-EO-055 | Vendor Advisory Board | no-match |
| 1993-EO-057 | yor's Domestic Violence Coordinating Council | no-match |
| 1993-EO-058 | Mayor’s Domestic Violence Coordinating Council | no-match |
| 1993-EO-060 | Office for Sexual Harassment Prevention | no-match |
| 1993-EO-063 | Mayor’s Small Business Advisory Board | no-match |
| 1998-EO-043 | City of New York Technology Steering Committee | no-match |
| 2000-EO-050 | City of New York Charter School Improvement Fund Committee | no-match |
| 2002-EO-030 | Mayor's Advisory Commission on the Adoption of the International Building Code | no-match |
| 2003-EO-039 | Police Commission | no-match |
| 2003-EO-043 | Commission on Latin Media and Entertainment | no-match |
| 2003-EO-044 | Commission on Latin Media and Entertainment | no-match |
| 2004-EO-053 | Lower Manhattan Construction Command Center | no-match |
| 2019-EO-043 | BQE Expert Panel | no-match |
| 2020-EO-051 | NYPD BQE Truck Enforcement Task Force | no-match |
| 2021-EO-071 | Center for Faith and Community Partnerships | no-match |
| 2023-EO-038 | interagency Housing at Risk Task Force | no-match |
| 2024-EO-043 | interagency City Housing Activation Taskforce | no-match |
| 2025-EO-063 | Office of Rodent Mitigation | no-match |
| 2026-EO-009 | interagency Citywide Junk Fee Task Force | no-match |
| 2026-EO-014 | Office of LGBTQIA+ Affairs | no-match |

