# `lineage/` — finding agency names in the executive orders

Finds where NYC agencies are named in the orders, so their history can be traced
through renames and reorganizations. Two passes only; building the actual family
tree comes later.

Standalone: this reads `corpus/*.json` as data, writes only into `lineage/out/`,
and never imports `nyc_executive_orders`.

## Why it exists

`src/nyc_executive_orders/supersede.py` looks like it already does this. It does
not. Measured against the committed corpus:

| | |
|---|---|
| Orders containing "there is hereby establish" | 83 |
| Orders where the existing pattern actually catches the name | 31 (52 missed) |
| Orders with `establishes_entity` filled in | **2** of 2,291 |
| Orders that mention DoITT | 39 — **not one recorded** |

The reason is built in. There, the registry is only a **check**: one pattern pulls
a name out of a "there is hereby established" sentence, and the registry is asked
whether that name is known. Nothing ever reads the orders asking *where a known
name appears*. Here, the registry does the searching.

## The two passes

**Pass one — names we already know** (`scan.py`). Every agency name from the
registry, looked for in every order, as one big pattern with the longest names
first. Rarely wrong.

**Pass two — names we do not know** (`discover.py`). Phrases shaped like agency
names that pass one did not match, sent to a person to review.

Pass two is **not** a backstop. The registry lists 317 agencies and marks every one
`status: "active"`; its `relations`, `mandates`, `founding_date`, and
`dissolution_date` fields are filled in for **zero** of them. So an agency that was
shut down can only turn up in pass two. That is where DoITT, the Board of Estimate,
the Bureau of the Budget, and the 1998 Technology Steering Committee come from —
and the history is made of exactly those.

## The letterhead

Nearly every order opens with the same stationery:

```
# CITY OF NEW YORK
OFFICE OF THE MAYOR
NEW YORK 7, N. Y.
January 10, 1955.
EXECUTIVE ORDER #15
```

That line is in `full_text` by design: `clean.py` uses `OFFICE OF THE MAYOR` as the
ANCHOR it trims the header **to**, so everything above it goes to `dropped_header`
and the anchor line itself stays. Pass one then finds it, 2,810 times, and `Office
of the Mayor` tops the report ahead of `DOC` at 2,117. That number counts
stationery, not government.

`letterhead.py` marks those finds. Two tests, and both must pass:

1. **The line holds nothing but the name** — after a markdown `#` or a trailing OCR
   dash is set aside. Not enough on its own: extraction drops a real sentence onto
   its own line often enough to matter (`...established in the\nOffice of the
   Mayor.`).
2. **A masthead line sits within three lines** — a US ZIP or `N. Y.` address, or a
   line of six tokens or fewer holding `CITY` + `YORK`, or `MAYOR`, or `EXECUTIVE`
   + `ORDER`/`MEMORANDUM`/`OFFICE`.

Measured over the corpus: **2,560 marked, 250 real references left alone**, and no
wrong mark in a hand check of 40. The rule asks nothing about how far into the
order the find is, because two scanned pages joined together bring the stationery
round again after the signature — 132 finds sit like that.

**Only the mayoral masthead is judged** (`scan.MASTHEAD_AGENCY_IDS`). Letting the
rule judge every agency marks 63 more finds, of which about 30 are plain sentences
that extraction broke onto their own line. A wrong mark deletes a real reference
from the counts, so the list is kept narrow.

**Marked, never deleted.** Each one stays in `mentions.json` carrying
`"in_letterhead": true` — written only when true, so an ordinary find stays short.
Three things depend on that:

* `full_text[start:end] == text` still holds for every record.
* `office-of-the-mayor` keeps its 250 body finds, so it is still a matched agency
  and still carries its registry row.
* Pass two receives the pass-one spans as `already_matched`. Drop them and it would
  put `CITY OF NEW YORK OFFICE OF THE MAYOR NEW YORK` (776 spans) up for review as
  a new agency.

`mention_count` still equals the length of the `mentions` list; `body_mention_count`
and `letterhead_count` split it. The report counts the body and says how many it set
aside.

## The promise

`full_text[start:end] == text`, character for character, for every find in both
passes. Checked over all 23,504 real finds by `test_real_corpus.py`.

`text` is what the order literally says, newlines and all — pulling text out of a
PDF wraps agency names across lines constantly, so `"Cyber\nCommand"` is an
ordinary find, not a fault.

Grouping needs the tidy version, so a proposed name also carries `name` (single
spaces, no trailing possessive). A pass-one find needs no equivalent: its
`agency_id` already gathers every spelling under one agency.

## Run it

```bash
python lineage/run_scan.py --dry-run             # prints the report, writes nothing
python lineage/run_scan.py --pass new-names      # pass two alone, no registry needed
python lineage/run_scan.py                       # both passes, into lineage/out/

# Publish the committed artifact. Run this whenever the corpus or the registry
# moves — the explorer builds its agency pages from this file and nothing else.
python lineage/run_scan.py --mentions-out corpus/mentions.json
```

Pass one reads `../ny-gov-web-registry/data/registry.json` (change it with
`--registry`) and stops with a message telling you what to do when it is missing.
Pass two needs no registry at all.

Results land in `lineage/out/`:

| File | What it is |
|---|---|
| `mentions.json` | The data. Same shape as `corpus/supersession.json` — a `generated_by` line, a count next to every list, and separate lists for what worked and what did not, where each failure says why. |
| `report.md` | The summary to read, including both safety checks and the review list. |

What it currently finds: **15,164** known names and **8,340** proposed new ones
across 3,202 orders, boiling down to **617** names for a person to review.

## The published copy

`corpus/mentions.json` is the same file, committed, and it is what everything
downstream reads — `../nyc-eo-explorer` builds its agency pages from it during a
GitHub Pages deploy, where no registry and no Python exist. `lineage/out/` stays
gitignored scratch space.

It is a derived artifact, exactly like `corpus/supersession.json`: **regenerate
and commit it whenever the corpus or the registry moves**, or the site will show
last month's answer.

### `agencies` — why the file carries names

`mentions` records an `agency_id` and nothing else. That is enough here, where
only ids are ever compared, and useless anywhere else: handed
`"office-of-the-mayor"`, a reader cannot label it.

So a run carries the registry's own record for every agency it matched — 178 of
the registry's 317, never all of them, because the corpus does not name the other
139. Each row holds the name, acronym, former names, level, classification,
parent, website and status. 121 also carry the agency's **own** description of
itself, captured verbatim by the registry's crawl and cut to 600 characters.

That text is **CC BY-SA 4.0**. The payload states the credit in
`agency_description_credit`, and anything that displays it has to state it too.

## Tests

```bash
uv run --no-project --with pytest python -m pytest lineage/tests -q
```

122 tests, none of which touch the network — `conftest.py` makes any attempt raise.
`test_real_corpus.py` pins the numbers against the committed corpus and skips
cleanly when the corpus or the registry is missing.

The repo's `pyproject.toml` says `testpaths = ["tests"]`, so a bare `pytest` will
not pick these up. Either give the path as above, or change that line to
`testpaths = ["tests", "lineage/tests"]`.

## The two files you edit, and the two points where you stop

Both live in `lineage/data/` and are meant for a person.

### `name_rules.json` — first stop

Short acronyms are a trap. 168 of the 592 registry names are 5 characters or fewer.
Measured over the orders:

| Name | Found ignoring capitals | Found respecting capitals | Verdict |
|---|---:|---:|---|
| `LAW` | 4,949 | 14 | all wrong — "CIVIL SERVICE LAW" |
| `Law` | 4,949 | 2,900 | all wrong — "Executive Law" |
| `UP` | 221 | 5 | all wrong — "PICK-UP" |
| `DOC` | 2,118 | 2,117 | real — Department of Correction |

So by default anything 5 characters or shorter must be found with the same
capitals, and this file records the exceptions together with the measurement behind
each one. Three settings: `same-case`, `any-case`, and `never` (a name ruled
`never` is not matched on its own, but a longer name containing it still is).

**The check:** `namelist.names_needing_a_rule()` finds any short name that is hit
more often than `max_hits_without_a_rule` with nothing written about it, and
`test_real_corpus.py` fails when that list is not empty. The decision cannot stay
unspoken.

### `extra_agencies.json` — second stop, and the real work

The registry cannot hold an agency that no longer exists, so this file does. Same
shape as a registry record, plus a `provenance` block that **must** name at least
one `eo_id` whose text shows the name in use — a name with no source order does not
belong here ("a guess is a bug").

How it goes: run both passes, read the review list in `out/report.md`, and move the
real ones across by hand. Running again then shifts them from pass two into pass
one. It starts empty, and filling it is the slow part.

## The files

| File | What it does |
|---|---|
| `normalize.py` | Tidies a name so two spellings compare equal. Ported from `supersede._norm_entity`; keep the two in step. |
| `textquality.py` | Decides whether a found name is readable. Ported from `clean.py`'s REVIEW cutoffs. |
| `records.py` | Loads the orders. Leaves out the 67 `_No text available_` placeholders. |
| `namelist.py` | Registry + extra agencies + rules → the list of names to search for, and both safety checks. |
| `letterhead.py` | Tells the mayoral stationery apart from a real reference to the office. |
| `scan.py` | Pass one. |
| `discover.py` | Pass two. |
| `mentions.py` | What a run records, and how it is written out. |
| `run_scan.py` | The command. |

## Choices worth knowing about

**Nothing new was installed.** `flashtext`, `pyahocorasick`, `rapidfuzz`, and
`spacy` are none of them already used here, and this repo keeps its dependencies
few and pinned loosely (engineering-standards §2). 592 names over roughly 12 MB of
text is one pass of one compiled pattern — about 4 seconds — and standard-library
code keeps §7 (local, repeatable, no network) obviously true. Worth revisiting only
if the name list passes a few thousand entries.

**Rules only, no LLM, no network** (§7), the same discipline as `supersede.py` and
`clean.py`.

**Same answer every time** (§6). Every list is sorted on a key that cannot tie, so
running again on unchanged input writes an identical `mentions.json`.

## What it does not do

- **Pass one matches names exactly.** OCR damage causes quiet misses, and 285 of
  the 978 pre-1974 orders are already flagged `needs-review`. Approximate matching
  is left out on purpose; measure how much is missed before adding it.
- **The review list starts at 3 sightings.** Genuine one-off bodies fall below
  that, so they are kept in the `rare` list rather than thrown away.
- **The letterhead rule judges only the mayoral masthead.** A department's own
  stationery (Brooklyn Public Library, Department of Investigation) is still
  counted as an ordinary find. Widening the rule costs more in wrong marks than it
  gains; see "The letterhead" above.
- **`english_like` rejects any run of 5 consonants**, which costs a few real words
  ("strengths"). Inherited from `clean.py` and left alone to match it.

## Next

The family tree. `mentions.json` gives the agencies; the links between them come
from reorganization verbs — renamed, abolished, transferred, merged, succeeded —
pulled out the way `supersede.py` pulls out revocations. That is roughly 200
sentences across the whole corpus, so every one can be checked by hand.
