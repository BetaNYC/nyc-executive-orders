#!/usr/bin/env python3
"""Phase C — populate the supersession graph over the existing corpus, in place.

Post-process ONLY (like ``run_clean_sweep``): reads ``corpus/eo.json``, extracts
the supersession/establishes-entity graph deterministically from text already in
the corpus (no OCR re-run, no network, no LLM), writes the four Phase-C fields
(``supersedes`` / ``superseded_by`` / ``establishes_entity`` / ``in_effect``) back
into ``corpus/eo.json`` + every ``corpus/YYYY/<eo_id>.md``, and emits the edge
list (``corpus/supersession.json``) + a human-readable ``supersession_report.md``.

Idempotent: the fields are rebuilt from scratch each run, and the corpus record
COUNT never changes (this pass annotates; it never adds or drops orders), so the
build_corpus shrink guard is a non-issue by construction. A re-run yields
byte-identical output.

Entity matching needs the ny-gov-web-registry (sibling checkout by default;
override with ``--registry``). Clone/pull it first: it is the authoritative entity
list the auto-write gate matches against.

Run:
    uv run --no-project --with pyyaml python scripts/run_supersede.py --dry-run
    uv run --no-project --with pyyaml python scripts/run_supersede.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders import supersede  # noqa: E402
from nyc_executive_orders.build_corpus import (  # noqa: E402
    FRONTMATTER_FIELDS,
    ParsedEO,
    render_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / "corpus"
EO_JSON = CORPUS_DIR / "eo.json"
SUPERSESSION_JSON = CORPUS_DIR / "supersession.json"
REPORT_MD = REPO_ROOT / "supersession_report.md"
DEFAULT_REGISTRY = REPO_ROOT.parent / "ny-gov-web-registry" / "data" / "registry.json"


def load_registry(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ents = data.get("entities") if isinstance(data, dict) else data
    if not isinstance(ents, list):
        raise SystemExit(f"registry at {path} has no 'entities' list")
    return ents


def reemit_corpus(records: list[dict], corpus_dir: Path) -> None:
    """Rewrite eo.json + per-EO .md from annotated records (manifest untouched).

    Only the four Phase-C fields differ from what is on disk (verified elsewhere
    that a frontmatter projection round-trips byte-identically), so this is a
    minimal, faithful re-emit.
    """
    for r in records:
        fm = {k: r[k] for k in FRONTMATTER_FIELDS}
        parsed = ParsedEO(frontmatter=fm, body=r.get("full_text", ""),
                          classification=None, char_count=0, md_relpath="")
        md_path = corpus_dir / f"{r['year']}/{r['eo_id']}.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text(render_markdown(parsed), encoding="utf-8")
    EO_JSON.write_text(
        json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_supersession_json(records, result: supersede.SupersedeResult) -> None:
    payload = {
        "generated_by": "scripts/run_supersede.py",
        "corpus_records": len(records),
        "edge_count": len(result.edges),
        "edges": [e.as_dict() for e in result.edges],
        "dangling_citations": [d.as_dict() for d in result.dangles],
        "extensions_skipped": result.extensions_skipped,
    }
    SUPERSESSION_JSON.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_report(records, result: supersede.SupersedeResult) -> str:
    by_verb_provenance = Counter((e.verb, e.source) for e in result.edges)
    verb_totals = Counter(e.verb for e in result.edges)
    prov_totals = Counter(e.source for e in result.edges)
    in_effect = Counter(r["in_effect"] for r in records)
    in_effect_reg = Counter(
        r["in_effect"] for r in records if not r.get("is_emergency"))

    dangle_by_reason = Counter(d.reason for d in result.dangles)
    # Distinct dangling targets (the unrecoverable holes worth naming).
    dangle_targets = Counter(
        d.target for d in result.dangles if d.reason == "not-in-corpus")

    # Entities: auto-writes vs review candidates. An order with >1 exact match is
    # NOT auto-written (scalar field) — surface those as review candidates too.
    match_counts = Counter(m.actor for m in result.entity_matches)
    auto = [m for m in result.entity_matches if match_counts[m.actor] == 1]
    multi = [m for m in result.entity_matches if match_counts[m.actor] > 1]

    L: list[str] = []
    L.append("# Supersession graph — extraction report")
    L.append("")
    L.append(f"Corpus records: **{len(records)}**  |  "
             f"Edges: **{len(result.edges)}**  |  "
             f"Dangling citations: **{len(result.dangles)}**  |  "
             f"Extension citations skipped: **{result.extensions_skipped}**")
    L.append("")
    L.append("Deterministic, rule-based extraction (no LLM, no network). Body "
             "citations resolve year-scoped from the cited date; the containing "
             "order is the actor. Header XREFs (`X AMENDED BY Y`) make Y the actor.")
    L.append("")

    partial_ct = sum(1 for e in result.edges if e.partial)
    L.append("## Edges by verb and provenance")
    L.append("")
    L.append("| verb | body-citation | header-xref | total |")
    L.append("|---|---:|---:|---:|")
    for verb in sorted(verb_totals):
        b = by_verb_provenance.get((verb, "body-citation"), 0)
        x = by_verb_provenance.get((verb, "header-xref"), 0)
        L.append(f"| {verb} | {b} | {x} | {verb_totals[verb]} |")
    L.append(f"| **all** | {prov_totals.get('body-citation', 0)} | "
             f"{prov_totals.get('header-xref', 0)} | {len(result.edges)} |")
    L.append("")
    L.append(f"Of these, **{partial_ct}** are section/paragraph-scoped (partial) "
             "edits — recorded as edges but excluded from the `in_effect` "
             "computation (a partial repeal does not take an order out of force).")
    L.append("")

    L.append("## `in_effect` distribution")
    L.append("")
    L.append("Regular EOs only carry a computed value; emergency EOs are `null` "
             "in v1 (expiry by operation of law is out of scope).")
    L.append("")
    L.append("| value | all | regular only |")
    L.append("|---|---:|---:|")
    for val, label in ((False, "false"), (None, "null")):
        L.append(f"| {label} | {in_effect.get(val, 0)} | {in_effect_reg.get(val, 0)} |")
    true_ct = in_effect.get(True, 0)
    L.append(f"| true | {true_ct} | {in_effect_reg.get(True, 0)} |")
    L.append("")
    L.append("`false` regular EOs = wholly revoked/superseded by a resolvable "
             "in-corpus order. No EO is set `true` (we never assert a historical "
             "order is still in force without a principled basis).")
    L.append("")

    L.append("## Dangling citations (resolved target not in corpus)")
    L.append("")
    L.append("Recorded here, never written into the fields. `not-in-corpus` = the "
             "cited order was never archived; `no-year` = the citation carried no "
             "date, so it could not be year-scoped (never guessed by number alone).")
    L.append("")
    for reason, ct in sorted(dangle_by_reason.items()):
        L.append(f"- **{reason}**: {ct}")
    L.append("")
    if dangle_targets:
        L.append("Distinct unrecoverable targets (most-cited first):")
        L.append("")
        for tgt, ct in dangle_targets.most_common():
            L.append(f"- `{tgt}` — cited {ct}×")
        L.append("")

    L.append("## Establishes-entity")
    L.append("")
    L.append(f"Auto-written (exact, unambiguous registry match, one per order): "
             f"**{len(auto)}**. Review candidates: **{len(result.entity_candidates) + len(multi)}**.")
    L.append("")
    if auto:
        L.append("### Auto-written")
        L.append("")
        L.append("| eo_id | matched name | registry id |")
        L.append("|---|---|---|")
        for m in sorted(auto, key=lambda m: m.actor):
            L.append(f"| {m.actor} | {m.raw_name} | `{m.registry_id}` |")
        L.append("")
    if result.entity_candidates or multi:
        L.append("### Review candidates (NOT auto-written)")
        L.append("")
        L.append("| eo_id | extracted name | reason |")
        L.append("|---|---|---|")
        for m in sorted(multi, key=lambda m: m.actor):
            L.append(f"| {m.actor} | {m.raw_name} | multiple-in-order (→ `{m.registry_id}`) |")
        for c in sorted(result.entity_candidates, key=lambda c: c.actor):
            L.append(f"| {c.actor} | {c.raw_name} | {c.reason} |")
        L.append("")

    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Compute + print the summary; write nothing.")
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY,
                    help=f"registry.json path (default: {DEFAULT_REGISTRY})")
    args = ap.parse_args(argv)

    records = json.loads(EO_JSON.read_text(encoding="utf-8"))
    n_before = len(records)
    print(f"Loaded {n_before} records from {EO_JSON}")

    if not args.registry.exists():
        raise SystemExit(
            f"registry not found at {args.registry} — clone/pull "
            "ny-gov-web-registry (sibling of this repo) or pass --registry")
    registry = load_registry(args.registry)
    print(f"Loaded {len(registry)} registry entities from {args.registry}")

    result = supersede.compute(records, registry)
    supersede.annotate_records(records, result)
    assert len(records) == n_before, "record count changed — refusing to write"

    report = render_report(records, result)

    if args.dry_run:
        print("\nDRY-RUN — nothing written\n")
        print(report)
        return 0

    reemit_corpus(records, CORPUS_DIR)
    write_supersession_json(records, result)
    REPORT_MD.write_text(report, encoding="utf-8")

    print("\nWROTE:")
    print(f"  {EO_JSON} ({n_before} records, count unchanged)")
    print(f"  {SUPERSESSION_JSON} ({len(result.edges)} edges)")
    print(f"  {REPORT_MD}")
    print(f"\nedges={len(result.edges)} dangles={len(result.dangles)} "
          f"extensions_skipped={result.extensions_skipped} "
          f"entity_auto={sum(1 for m in result.entity_matches)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
