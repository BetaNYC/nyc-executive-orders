# Agency NER and Lineage Tracing — Proposal

Scope: identify named city agencies in the executive-order corpus, then trace
their lineages across orders. Topic modeling is out of scope here. Legislation
merge is future work.

## What you already have

`src/nyc_executive_orders/supersede.py` is most of step 1 in skeleton form. It has
`_ESTABLISH_RE`, a normalizer `_norm_entity`, and an auto-write gate that requires
an exact match against an entity registry. The registry is
`../ny-gov-web-registry/data/registry.json`. **That repository is not checked out
here.** Clone it first. Nothing in the entity path can be tested without it.

Measured over the corpus (2,291 records, 1974+):

| Pattern | Orders |
|---|---|
| `there is hereby established` | 83 |
| loose reorganization verb (`is hereby established/created/continued/renamed/abolished/transferred`) | 181 |
| rename language (`renamed`, `redesignated`, `shall be known as`) | 27 |
| `abolished` | 10 |
| `transferred to` | 30 |
| `successor` | 20 |

This is the important number. The reorganization events are approximately 200
sentences, not 200,000. You can review every one by hand. Design for that.

A probe of the target domain:

```
office of information technology          1992-EO-035
DoITT                                     1998-EO-043 .. 2014-EO-008
dept. of info. technology and telecom     2002-EO-031 .. 2022-EO-003
chief analytics officer                   2013-EO-306 .. 2013-EO-463
office of data analytics                  2013-EO-306 .. 2022-EO-003
chief technology officer                  2014-EO-008 .. 2026-EO-002
cyber command                             2017-EO-028 .. 2022-EO-010
mayor's office of data analytics          2019-EO-050 .. 2026-EO-008
chief privacy officer                     2021-EO-064 .. 2022-EO-003
office of technology and innovation       2022-EEO-224 .. 2026-EO-002
```

The lineage is visible in the text. The 2022 hand-off from DoITT to OTI is present
in the same order (`2022-EO-003`).

---

## 1. Identify named agencies

Use three layers. Each layer has a different precision and recall. Do not use one
model for all of it.

**Layer A — gazetteer match (do this first).** Load the registry names into an
Aho-Corasick matcher (`flashtext`) or a spaCy `EntityRuler`. This is deterministic,
fast, and reproducible. It matches the no-LLM discipline the repository already
states. Precision is near 1.0 on known names.

Add fuzzy match for the pre-1974 set. That text came from VLM OCR. Use `rapidfuzz`
token-set ratio with a threshold. Send near-misses to a review queue. Do not
auto-write a fuzzy match.

**Layer B — pattern mining for unknown names.** Historic offices are absent from
every modern list. Mine candidates with a head-word rule: a capitalized noun phrase
headed by `Department`, `Office`, `Bureau`, `Commission`, `Board`, `Authority`,
`Council`, `Administration`, `Agency`, `Division`, `Task Force`, or `Committee`,
extended through `of`/`for`. Recall is high and precision is moderate. Route the
output to human review, then promote accepted names into a local gazetteer.

**Layer C — model-based extraction (last).** On the current state of the art:

- **GLiNER** is the best fit for zero-shot span NER here. It is a BERT-size
  bidirectional model, runs on CPU, and accepts arbitrary labels at inference time.
  Give it labels such as `city agency`, `mayoral office`, `official title`. Use it
  to find spans that layer B missed.
- **Generic spaCy `ORG`** performs poorly on 1950s municipal prose. Do not rely on
  it alone.
- **LLM structured extraction** (Claude with a JSON schema) is strongest for
  *relations*, not for span discovery. Require the model to return the exact
  character offsets of its evidence. Verify each offset against the source text.
  Discard any output whose span does not match.

Treat layer C as a proposer only. Layers A and B stay the verifier. Never let a
model write a field directly.

Output one artifact: `corpus/mentions.json`, with `eo_id`, `start`, `end`,
`surface`, `layer`, and `registry_id` or `null`.

---

## 2. External data sources

Pull these in. The registry alone has no time dimension, and the corpus alone has
holes.

| Source | Why it matters |
|---|---|
| **`ny-gov-web-registry`** | Your own authoritative list. The auto-write gate needs it. Clone it now. |
| **Green Book** (Official Directory of the City of New York) | **The highest-value source.** It is annual. Each edition names every agency that existed that year. Annual editions give you a name-by-year table, which is exactly the time axis a lineage needs. DORIS GPP and the Internet Archive hold editions. |
| **NYC Charter and Administrative Code** (`nyc-charter-laws-rules`) | The Charter creates most departments and names them exactly. DoITT was created by Charter change, not by an executive order. |
| **DORIS agency-history / authority records** | The Municipal Archives keeps administrative histories per agency. These are hand-written lineages. Use them as ground truth for evaluation. |
| **Mayor's Management Report** (1977+) | Annual, per-agency. Confirms agency existence by year. |
| **Wikidata** (`replaces` / `replaced by` properties) | A weak prior only. Coverage is thin and quality varies. |

The Green Book plus the Charter plus the registry are the three that matter. Get
the Green Book.

---

## 3. Develop lineages

Model this as bitemporal entity resolution, not as name matching.

**Two node types.** A `Mention` is one surface string at one span in one order. An
`Entity` is a canonical organization with a lifespan (`valid_from`, `valid_to`).

**Typed edges, extracted from the text.** Extend the verb-clause machinery that
`supersede.py` already uses. Add sibling patterns to `_ESTABLISH_RE` for these
verbs:

```
establishes        abolishes         renames
transfers_to       merges_into       splits_from
succeeds           places_within     assigns_function
```

Volume is approximately 200 events. Review all of them by hand. Store them the way
you store supersession: a flat edge list in `corpus/entity_edges.json`, with
`eo_id`, span offsets, verb, source entity, and target entity.

**Resolve mentions into entities.** Block candidates by head-word and acronym. Then
score each pair on four signals:

1. An explicit `renames` or `succeeds` edge from the order text.
2. Name similarity.
3. Adjacency in time.
4. A shared parent organization.

Rank signal 1 above all others. Trust `shall hereafter be known as` over any
string-distance score.

**Walk the graph.** A lineage is a connected component over the successor edges,
ordered by date. Load the JSON into NetworkX. Do not start with Neo4j. If the JSON
becomes too large later, move to Kuzu, which is embedded and needs no server.

**Query by point in time.** Give each entity `valid_from` and `valid_to` from the
dates of the establishing and abolishing orders. Then "which office held IT in
2005?" becomes one query.

**One warning.** Executive orders are not the complete lineage. Local Law and the
Charter create and abolish agencies too. Your EO-only graph will have gaps. State
the gaps in the output rather than hide them, the same way the repository handles
known corpus gaps. The Charter join and the Green Book join close most of them.

---

## Recommended build order

1. Clone `ny-gov-web-registry`. Run `scripts/run_supersede.py --dry-run` and read
   the current entity report.
2. Build layers A and B. Emit `corpus/mentions.json` with offsets.
3. Extend the reorganization-verb extractor. Hand-verify the approximately 200
   events.
4. Build the entity clusters and the lineage graph. Test on the IT chain above,
   because you now know the correct answer for it.
5. Join the Green Book year slices to fill the non-EO gaps.
6. Add the GLiNER and LLM layers last, as proposers into a review queue.

Steps 1 through 4 need no model and no network. They will give you the DoITT-to-OTI
lineage on their own.
