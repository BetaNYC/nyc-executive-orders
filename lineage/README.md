# `lineage/` — tracing NYC agencies through the executive orders

Finds where NYC agencies are named in the orders, then reads the sentences that
link one body to another — established, renamed, abolished, transferred — and the
citations that link one order to another. Four passes.

Standalone: this reads `corpus/*.json` as data, writes only into `lineage/out/`,
and never imports `nyc_executive_orders`. Where it reuses logic from that package
the source is named in a comment, so the two stay comparable.

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

## The four passes

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

**Pass three — what the orders DO to agencies** (`reorg.py`). The family tree. See
"The reorganization sentences" below.

**Pass four — what the orders do to EACH OTHER** (`citations.py`). One order
revoking, amending or superseding another. See "Order-to-order supersession".

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

## The reorganization sentences

Pass three reads the sentences that link two bodies. Measured over the 3,202 orders
that carry text: **367 events**, of which **97** pin down a registry agency on every
side, plus **496** sentences kept for review.

| what the order does | events |
|---|---:|
| `establishes` | 272 |
| `continues` | 40 |
| `renames` | 21 |
| `transfers_to` | 21 |
| `abolishes` | 11 |
| `merges_into` | 1 |
| `succeeds` | 1 |

`supersede.py` is the ancestor of this and it catches very little. Its one pattern
reads the existential shape, `there is hereby established a <Name>`, which is 133
sentences in the whole corpus. The far commoner shape puts the name **first** — `The
Office of the Auditor General ... is hereby established in the Office of the Mayor`
— and it sees none of them. It also has nowhere to put a rename or an abolition,
because it writes one scalar field.

**Both sides must be an agency, and that is the whole filter.** The two commonest
verbs are also the two least trustworthy:

* `designated` — 258 hits. `The Honorable Paul R. Screvane is hereby designated
  Vice-Chairman` is a person taking a post. But the word cannot simply be dropped:
  `2022-EO-003` reads *"The Department of Information Technology and
  Telecommunications shall hereafter be designated as the Office of Technology and
  Innovation"*, and that is the DoITT-to-OTI hand-off, the most important edge in
  the corpus.
* `transferred` — 61 hits, and `salaries or wages transferred to "UNCLAIMED"
  account` is money.

So the filter is not a word list. **Every side of a sentence must land on a span
pass one or pass two already found.** A person keeps their post and money keeps
moving, and neither makes an edge, because neither names a body on both sides.

Four rules earn their place, and each has a test:

* **The nearest span wins, not pass one.** Position is the grammatical signal and
  the pass is not. *"There is hereby established in the Office of the Mayor an M/WBE
  Advisory Committee"* names the parent first and the new body second; the new body
  is the one pass two found. Preferring pass one records the Office of the Mayor as
  the thing established.
* **A blank line does not end a sentence.** PDF extraction drops them mid-sentence:
  `2022-EO-003` reads `...and Telecommunications\n\nshall hereafter be designated
  as...`. Treating that gap as a full stop cuts the subject off its own verb and
  loses the DoITT edge outright.
* **A name already given to an earlier sentence cannot become the subject of the
  next one.** *"One shall be designated the First Deputy Mayor, one shall be
  designated the Deputy Mayor for Operations, ..."* is a list of appointments. The
  nearest name before the second verb is the FIRST post, so without this rule the
  roster reads as a chain of renames — **84 false edges**, the single largest wrong
  class measured.
* **A name inside an "in"/"within" phrase is the wrapper, not the subject.** *"The
  Mayor's Reception Committee, in the Office of the Mayor, is hereby consolidated"*
  is about the committee.
* **One verb can govern a list of bodies.** `2022-EO-003` § 3 reads *"The Office of
  Cyber Command ..., the Office of Data Analytics ... and the Office of Information
  Privacy ... shall be continued and established within the Office of Technology and
  Innovation"*. Three offices move into OTI. Taking only the nearest name records
  the Office of Information Privacy and drops two thirds of what the order did. See
  below.

### When one verb governs a list

A name before the verb joins the nearest one while the text **between** them is
nothing but list glue. Four things make a gap glue, and each one stops a different
wrong join:

| The gap must | Or else |
|---|---|
| be short (≤ 90 characters) | a list item carries a phrase, not a clause |
| hold no `.` `;` `:` `§` | the list ended |
| hold a comma, `and`, or `&` | the two names are merely adjacent |
| hold **no finite verb** | *"...hereby is revoked and the Committee ... is hereby abolished"* is otherwise perfect glue, and the revoked order's body gets abolished along with the real one |

A single bare comma is not enough on its own. Two names joined by one are as likely
to be an apposition: `1955-EO-022` reads *"The Division of Analysis, Bureau of the
Budget, together with its functions and staff, is hereby transferred to..."*, where
the second name is the first one's **parent** and only the Division moves. So a
chain earns its length one of two ways — a gap uses `and`/`&`, or the chain reaches
**three** names. `1976-EO-063` needs the second arm: it abolishes four planning
offices, and its final "and" sits *inside* a pass-two span rather than in a gap.

Asked only of names **before** the verb, which is where the evidence is. After it,
*"established a Committee ... and the Council"* is likelier the body being advised
than a second body being created.

Narrow on purpose: **four** sentences in the whole corpus name a list, and every one
is a list a person would read the same way — `2022-EO-003` (three offices into OTI),
`1976-EO-063` (three planning offices abolished), `1965-EO-181o` (*"a Housing Policy
Board and a Housing Executive Committee"*) and `1966-EO-028c` (*"the Anti-Poverty
Operations Board and the Economic Opportunity Committee are abolished"*).

### One event, one body

A list sentence becomes **one event per body**, each carrying the roles they all
share — the parent they moved into, the place they were transferred to. All of them
keep the same span and the same text, so the sentence is still readable whole from
any one of them.

This is a shape promise, not a convenience: **no event ever fills the same role
twice.** Everything downstream flattens an event to a row, and a repeated role drops
silently when it does. `../nyc-eo-explorer` builds its `agency_events` table with
`new Map(roles.map(r => [r.role, r]))`, which keeps the last of a repeated key — so
an event carrying three `to` roles would have shown one office and lost two without
a word. A test asserts the promise over the whole corpus.

**Both ends can be the same agency, and that is recorded.** It happens when the
name list has already merged two names — the old spelling filed under the new
agency's id — so the edge runs from a node to itself and says nothing changed. Four
events read that way today. The count is in the payload as
`same_agency_event_count` rather than hidden, because each one is a place where the
name list has made a judgement the graph would otherwise make for itself.

`doitt` is deliberately **not** one of them. See below.

## Order-to-order supersession

Pass four is a port of `src/nyc_executive_orders/supersede.py` and
`identity.py::mint_eo_id`, copied rather than imported so this package still stands
alone. **Keep the two in step.** Measured: **294 edges** (121 amended, 87 revoked,
70 repealed, 10 rescinded, 6 superseded), **150 citations that went nowhere**, and
**1,617 extensions counted and skipped** — an emergency order expires by operation
of law, which is not supersession.

Two things here are on purpose, and both differ from `supersede.py`:

* **Every order is read.** `supersede.py` runs over `corpus/eo.json` alone, so the
  978 orders of the bound volumes contribute nothing to `corpus/supersession.json`.
  This reads them, which is most of the difference between 294 edges and 244.
* **An id must match exactly.** Only 526 of the 978 pre-1974 orders carry the plain
  `YYYY-EO-NNN` shape; the rest carry a letter suffix (`1966-EO-019g`) or a
  different series (`EM`, `AM`) that the minter cannot produce, and 86 id stems are
  shared by more than one order — `1966-EO-019` alone covers 50. A citation whose
  minted id is absent, but whose stem does match orders we hold, is recorded as a
  dangle saying `suffix-variant`, never attached to one of them. (Measured today:
  none. Pre-1974 citations overwhelmingly carry no year at all, which is the
  `no-year` reason on 125 of the 150.)

`in_effect` is not computed. That field belongs to the corpus records, and this
package writes only its own output.

## The promise

`full_text[start:end] == text`, character for character, for every find in every
pass — and for every reorganization sentence and each role inside it. Checked over
the real corpus by `test_real_corpus.py`.

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
| `report.md` | The summary to read, including both safety checks and both review lists. |

What it currently finds: **15,331** known names and **8,262** proposed new ones
across 3,202 orders; **367** reorganization events with **496** sentences for a
person to review; and **294** order-to-order edges.

The keys `mentions.json` carries, beyond the name finds:

| key | what it holds |
|---|---|
| `agency_events` | The 367 events. Each carries the sentence, its span, and one `roles` entry per side (`from`, `to`, `parent`) with that side's own span and `agency_id`. A role never appears twice in one event. |
| `unresolved_events` | The 496 sentences that named nothing we could attach, each saying why. |
| `order_edges` | The 294 order-to-order edges: `actor`, `target`, `verb`, `source`, `partial`. |
| `order_dangles` | The 150 citations that resolved to no order we hold, each saying why. |

A `role` whose `agency_id` is `null` came from pass two. That is not a failure — it
says the body is not on the name list yet, which is what `extra_agencies.json` is
for.

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
139 — plus every hand-written body from `extra_agencies.json` that the run found,
which is `doitt` today. 179 rows in all. Each row holds the name, acronym, former names, level, classification,
parent, website and status. 121 also carry the agency's **own** description of
itself, captured verbatim by the registry's crawl and cut to 600 characters.

That text is **CC BY-SA 4.0**. The payload states the credit in
`agency_description_credit`, and anything that displays it has to state it too.

## Tests

```bash
uv run --no-project --with pytest python -m pytest lineage/tests -q
```

212 tests, none of which touch the network — `conftest.py` makes any attempt raise.
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
one. Filling it is the slow part.

**A renamed body gets its own id, never the new body's.** `doitt` is the worked
example and the reason the rule is written down. DoITT and the Office of Technology
and Innovation are one office in law and two in time. Filing the old name under
`oti` looks tidier and quietly destroys the thing being built: the 2022 rename
becomes an edge whose two ends are the same node, and there is nothing left to say
that anything changed. Split, `doitt` runs 1995 to 2022 across 57 orders, `oti`
runs 2022 onward across 6, and `2022-EO-003` is the single order naming both.

So the entry carries `status: "renamed"` and a `dissolution_date` taken from that
order. It is also read by `agencies.py`, so the published file carries a full row
for a body no registry holds — otherwise `doitt` would ship as a bare slug that a
reader cannot label.

One warning that belongs with the entry: MODA's record NYC_GOID_000382 lists both
DoITT names under OTI's alternate-or-former fields, and those two values are the
only ones out of 306 MODA records that `../ny-gov-web-registry` is missing. Fixing
that upstream would hand OTI the DoITT names as `other_names` and silently undo the
split. Keep those two names off the `oti` row.

## The files

| File | What it does |
|---|---|
| `normalize.py` | Tidies a name so two spellings compare equal. Ported from `supersede._norm_entity`; keep the two in step. |
| `textquality.py` | Decides whether a found name is readable. Ported from `clean.py`'s REVIEW cutoffs. |
| `records.py` | Loads the orders. Leaves out the 67 `_No text available_` placeholders. |
| `namelist.py` | Registry + extra agencies + rules → the list of names to search for, and both safety checks. |
| `agencies.py` | The registry's own row for every agency a run matched, plus the hand-written ones, so the file can be read without the registry. |
| `letterhead.py` | Tells the mayoral stationery apart from a real reference to the office. |
| `scan.py` | Pass one. |
| `discover.py` | Pass two. |
| `reorg.py` | Pass three. What an order does to an agency. |
| `citations.py` | Pass four. What an order does to another order. Ported from `supersede.py` + `identity.py`; keep the three in step. |
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
- **An event is only as good as the two passes under it.** A body missing from
  `extra_agencies.json` gives an unresolved sentence, never a silent link. 265 of
  the 361 events name at least one body that has no `agency_id` yet.
- **A list needs its items found separately.** `1976-EO-063` records three bodies
  where the order names four, because pass two returned *"Office of Downtown
  Brooklyn Development and the Upper Manhattan Planning and Development Office"* as
  one span. The list rule can only split what the earlier passes found separately.
- **`renames` and `succeeds` never take a list.** One body becomes one other; a
  list on either side is likelier a mis-read than a real multi-way rename.
- **A sentence stops at a full stop**, so `Dept. of Health` cuts a window short and
  loses an edge. Short is the safe way to be wrong: a short window never invents an
  edge, it only misses one.
- **`renames` is a review-grade list, not a finished one.** 21 events, and a hand
  check of 5 found 2 wrong (`designated as the administering agency` is a job, not
  a rename). Every one is meant to be read.

## Next

The edges exist; the graph does not. What is left:

1. **Work the review lists.** 496 unresolved sentences and 617 proposed names, and
   the `one-side-only` half of the first list is the part that pays: each row names
   a body that belongs in `extra_agencies.json`. Every name moved across turns
   unresolved sentences into edges on the next run.
2. **Give each agency a lifespan.** `valid_from` and `valid_to` from the dates of
   the establishing and abolishing orders, so "which office held IT in 2005?"
   becomes one query.
3. **Walk the graph.** A lineage is a connected component over the `renames`,
   `succeeds` and `merges_into` edges, ordered by date. Load the JSON into NetworkX;
   there is no reason for a graph database at this size.
4. **State the gaps.** Executive orders are not the whole lineage — Local Law and
   the Charter create and abolish agencies too, and DoITT itself was created by a
   Charter change rather than by an order. The EO-only graph WILL have holes. Say
   so in the output, the way this repository states its other known gaps.
