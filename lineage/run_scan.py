#!/usr/bin/env python3
"""Find where NYC agencies are named in the executive orders.

Reads ``corpus/eo.json`` + ``corpus/eo_pre1974.json`` and the agency registry, runs
the known-name pass and the new-name pass, and writes the results plus a report a
person can read. Reads the corpus; never writes to it.

Same answer every time: every list is sorted on a key that cannot tie, and the run
depends on nothing but the corpus, the registry, the extra-agencies file, and the
name rules — so running it again on unchanged input gives an identical
``mentions.json``.

Entirely local — no network, no LLM (engineering-standards §7). Every find records
where it sits, so ``full_text[start:end]`` gives back exactly what was found.

Run:
    python lineage/run_scan.py --dry-run
    python lineage/run_scan.py --pass new-names --dry-run   # no registry needed
    python lineage/run_scan.py
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

# Make the repo root importable so `lineage` resolves without an install step —
# the same shape scripts/*.py use for `src` (they insert parents[1] / "src").
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lineage import agencies as agencies_mod
from lineage import citations as citations_mod
from lineage import discover as discover_mod
from lineage import namelist as namelist_mod
from lineage import records as records_mod
from lineage import reorg as reorg_mod
from lineage import scan as scan_mod
from lineage.mentions import (
    REASON_SUFFIX_VARIANT,
    WHY_ONE_SIDE_ONLY,
    RunResult,
    build_payload,
    dumps,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
LINEAGE_DIR = REPO_ROOT / "lineage"
CORPUS_FILES = [REPO_ROOT / "corpus" / "eo.json",
                REPO_ROOT / "corpus" / "eo_pre1974.json"]
DEFAULT_REGISTRY = REPO_ROOT.parent / "ny-gov-web-registry" / "data" / "registry.json"
# The agency self-descriptions sit beside the registry in the same checkout, so
# they are found from it rather than asked for separately. Absent is fine.
DESCRIPTIONS_NAME = "descriptions.json"
EXTRA_AGENCIES = LINEAGE_DIR / "data" / "extra_agencies.json"
NAME_RULES = LINEAGE_DIR / "data" / "name_rules.json"
DEFAULT_OUT = LINEAGE_DIR / "out"

GENERATED_BY = "lineage/run_scan.py"
DEFAULT_REVIEW_FROM = 3

KNOWN_NAMES, NEW_NAMES, BOTH = "known-names", "new-names", "both"


def render_report(result: RunResult, payload: dict, *,
                  needing_a_rule: list[tuple[str, int]],
                  unused_rules: list[str],
                  most_hit: list[tuple[str, int]]) -> str:
    """Build the Markdown report. Depends on nothing but the run's results."""
    L: list[str] = []
    L.append("# Agency names found in the executive orders")
    L.append("")
    L.append(f"Orders in the corpus: **{result.corpus_records}**  |  "
             f"Read: **{result.orders_read}**  |  "
             f"Skipped, no text: **{result.orders_without_text}**  |  "
             f"Passes: **{', '.join(result.passes_run)}**")
    L.append("")
    L.append("Found by rules, entirely on this machine (no network, no LLM). Every "
             "find records where it sits in `full_text`, so each one can be checked "
             "against the order it came from.")
    L.append("")

    L.append("## The name list")
    L.append("")
    if result.name_list_counts:
        L.append("| | count |")
        L.append("|---|---:|")
        for k, v in result.name_list_counts.items():
            L.append(f"| {k.replace('_', ' ')} | {v} |")
    else:
        L.append("_The known-name pass did not run._")
    L.append("")

    L.append("## Pass one — names we already knew")
    L.append("")
    L.append(f"Found: **{payload['body_mention_count']}** in the order  |  "
             f"**{payload['letterhead_count']}** more in the letterhead, marked "
             "and left out of the counts below")
    L.append("")
    if payload["letterhead_count"]:
        L.append("Nearly every order opens with the mayoral stationery — `CITY OF "
                 "NEW YORK` / `OFFICE OF THE MAYOR` / an address. That line is in "
                 "`full_text` on purpose: the cleaner trims the header TO it, so "
                 "the line itself stays. Counting it would put `Office of the "
                 "Mayor` at the top of the table on the strength of its "
                 "letterhead. Each one is marked `in_letterhead` in "
                 "`mentions.json` and kept, never deleted.")
        L.append("")
    if most_hit:
        L.append("Most-found names, letterhead left out:")
        L.append("")
        L.append("| name | times found |")
        L.append("|---|---:|")
        for name, n in most_hit:
            L.append(f"| {name} | {n} |")
        L.append("")

    if payload["agency_count"]:
        with_words = sum(1 for a in payload["agencies"] if a.get("description"))
        L.append(f"Carried with the finds: the registry's own record for all "
                 f"**{payload['agency_count']}** agencies matched — name, acronym, "
                 f"former names, level, classification and website. "
                 f"**{with_words}** also carry the agency's own description of "
                 f"itself. So `mentions.json` can be read, and displayed, without "
                 "the registry beside it.")
        L.append("")

    L.append("### Short-name safety check")
    L.append("")
    if needing_a_rule:
        L.append(f"**{len(needing_a_rule)} short names are found more often than "
                 "`max_hits_without_a_rule` and have no rule written for them.** "
                 "Each is slipping in on the default and needs a decision recorded "
                 "in `lineage/data/name_rules.json`.")
        L.append("")
        L.append("| name | times found |")
        L.append("|---|---:|")
        for name, n in needing_a_rule:
            L.append(f"| {name} | {n} |")
    else:
        L.append("Clean — every short name that is found often has a rule written "
                 "for it.")
    L.append("")
    if unused_rules:
        L.append(f"**{len(unused_rules)} rules match no name** — either the "
                 "registry dropped the name, or another spelling of it hid this "
                 "one: " + ", ".join(f"`{r}`" for r in unused_rules))
        L.append("")

    L.append("## Pass two — names we did not know")
    L.append("")
    L.append(f"Found: **{payload['proposed_count']}**  |  "
             f"To review: **{payload['to_review_count']}** names seen "
             f"{payload['review_from']}+ times  |  "
             f"Seen rarely: **{payload['rare_count']}**")
    L.append("")
    L.append("These are phrases shaped like agency names that are not on the list. "
             "The registry holds only agencies that still exist, so a body that was "
             "shut down can appear ONLY here. Move the real ones by hand into "
             "`lineage/data/extra_agencies.json`.")
    L.append("")
    if payload["to_review"]:
        L.append("### To review (top 60)")
        L.append("")
        L.append("| times seen | name | example orders |")
        L.append("|---:|---|---|")
        for row in payload["to_review"][:60]:
            orders = ", ".join(f"`{o}`" for o in row["example_orders"])
            L.append(f"| {row['times_seen']} | {row['name']} | {orders} |")
        L.append("")

    if payload["discarded"]:
        by_why = collections.Counter()
        for d in payload["discarded"]:
            by_why[d["why"]] += d["count"]
        L.append("### Thrown out")
        L.append("")
        L.append("Dropped before the review list. Counted, never quietly deleted.")
        L.append("")
        L.append("| why | count |")
        L.append("|---|---:|")
        for why, n in by_why.most_common():
            L.append(f"| `{why}` | {n} |")
        L.append("")

    L.extend(_agency_event_section(payload))
    L.extend(_order_edge_section(payload))

    return "\n".join(L) + "\n"


def _one_line(text: str, width: int = 150) -> str:
    """A sentence from an order, fit for a table cell.

    The order text is wrapped across lines by the PDF, and a pipe would break the
    Markdown table, so both are taken out. For reading only — the exact text stays
    in `mentions.json`.
    """
    flat = " ".join(text.split()).replace("|", "/")
    return flat if len(flat) <= width else flat[:width - 1] + "…"


def _agency_event_section(payload: dict) -> list[str]:
    """Pass three: what the orders do to agencies."""
    L = ["## Pass three — what the orders do to agencies", ""]
    L.append(f"Found: **{payload['agency_event_count']}**  |  "
             f"Every side a known agency: "
             f"**{payload['fully_resolved_event_count']}**  |  "
             f"Both sides the SAME agency: "
             f"**{payload['same_agency_event_count']}**  |  "
             f"To review: **{payload['unresolved_event_count']}**")
    L.append("")
    L.append("These are the sentences that link two agencies. Every side of a "
             "sentence must land on a name one of the passes above already found, "
             "which is what keeps out `is hereby designated Vice-Chairman` (a "
             "person taking a post) and `transferred to \"UNCLAIMED\" account` "
             "(money). A sentence that named nothing is kept below rather than "
             "thrown away: it says which body is missing from "
             "`lineage/data/extra_agencies.json`.")
    L.append("")
    if payload["agency_events_by_kind"]:
        L.append("| what the order does | events |")
        L.append("|---|---:|")
        for kind, n in sorted(payload["agency_events_by_kind"].items(),
                              key=lambda t: (-t[1], t[0])):
            L.append(f"| `{kind}` | {n} |")
        L.append("")
    if payload["unresolved_events"]:
        by_why = collections.Counter(e["why"] for e in payload["unresolved_events"])
        L.append("| why | sentences |")
        L.append("|---|---:|")
        for why, n in by_why.most_common():
            L.append(f"| `{why}` | {n} |")
        L.append("")
        L.append("### Sentences to review (top 60)")
        L.append("")
        L.append("`one-side-only` comes first, and it is the list worth working "
                 "through: the sentence DID name a body, and the other side is "
                 "missing from the name list. `no-agency-named` is mostly prose "
                 "that happens to share a verb — a target date is established, a "
                 "leave allowance is established — and it is counted above rather "
                 "than worked through. Both are in `mentions.json` in full.")
        L.append("")
        L.append("| order | verb | why | the sentence |")
        L.append("|---|---|---|---|")
        rows = sorted(payload["unresolved_events"],
                      key=lambda e: (e["why"] != WHY_ONE_SIDE_ONLY, e["eo_id"],
                                     e["start"]))
        for e in rows[:60]:
            L.append(f"| `{e['eo_id']}` | {e['verb']} | `{e['why']}` | "
                     f"{_one_line(e['text'])} |")
        L.append("")
    return L


def _order_edge_section(payload: dict) -> list[str]:
    """Pass four: what the orders do to each other."""
    L = ["## Pass four — what the orders do to each other", ""]
    L.append(f"Edges: **{payload['order_edge_count']}**  |  "
             f"Citations that went nowhere: "
             f"**{payload['order_dangle_count']}**  |  "
             f"Extensions skipped: **{payload['extensions_skipped']}**")
    L.append("")
    L.append("One order revokes, amends or supersedes another by citing it. The "
             "cited year scopes the number, because per-mayor numbering resets and "
             "a number on its own names several orders. An extension is counted "
             "and skipped: an emergency order expires by operation of law, which is "
             "not supersession.")
    L.append("")
    by_verb = collections.Counter(e["verb"] for e in payload["order_edges"])
    if by_verb:
        L.append("| verb | edges |")
        L.append("|---|---:|")
        for verb, n in by_verb.most_common():
            L.append(f"| {verb} | {n} |")
        L.append("")
    by_reason = collections.Counter(d["reason"] for d in payload["order_dangles"])
    if by_reason:
        L.append("Citations that produced no edge, and why.")
        if REASON_SUFFIX_VARIANT in by_reason:
            L.append("")
            L.append("`suffix-variant` means the number and year match orders we "
                     "hold, but the pre-1974 volumes filed several under that "
                     "number (`1966-EO-019` covers 50 of them), and picking one "
                     "would be a guess.")
        L.append("")
        L.append("| why | citations |")
        L.append("|---|---:|")
        for reason, n in by_reason.most_common():
            L.append(f"| `{reason}` | {n} |")
        L.append("")
    return L


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pass", dest="which_pass",
                    choices=[KNOWN_NAMES, NEW_NAMES, BOTH], default=BOTH,
                    help="which passes to run (default: both). "
                         "'new-names' needs no registry.")
    ap.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY,
                    help=f"registry.json path (default: {DEFAULT_REGISTRY})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"directory to write into (default: {DEFAULT_OUT})")
    ap.add_argument("--mentions-out", type=Path, default=None,
                    help="write mentions.json to this FILE instead of into --out. "
                         "Use it to publish the committed artifact: "
                         "--mentions-out corpus/mentions.json. The report always "
                         "stays in --out, because the corpus holds data, not prose.")
    ap.add_argument("--review-from", type=int, default=DEFAULT_REVIEW_FROM,
                    help="how many times a new name must be seen to reach the "
                         f"review list (default: {DEFAULT_REVIEW_FROM})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Work it out and print the report; write nothing.")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    run_known = args.which_pass in (KNOWN_NAMES, BOTH)
    run_new = args.which_pass in (NEW_NAMES, BOTH)

    orders = records_mod.load_corpus(CORPUS_FILES)
    readable_orders, skipped = records_mod.with_text(orders)
    print(f"Loaded {len(orders)} orders; {len(readable_orders)} have text, "
          f"{skipped} do not")

    result = RunResult(
        corpus_records=len(orders), orders_read=len(readable_orders),
        orders_without_text=skipped,
        passes_run=tuple(name for name, on in
                         ((KNOWN_NAMES, run_known), (NEW_NAMES, run_new)) if on))

    name_list = None
    unused: list[str] = []
    needing_a_rule: list[tuple[str, int]] = []
    most_hit: list[tuple[str, int]] = []

    if run_known:
        if not args.registry.exists():
            raise SystemExit(
                f"registry not found at {args.registry} — clone/pull "
                "ny-gov-web-registry (sibling of this repo), pass --registry, or "
                "run with --pass new-names (that pass needs no registry)")
        name_list = _load_name_list(args.registry)
        result.name_list_counts = name_list.counts()
        print(f"Name list: {result.name_list_counts}")
        result.mentions = scan_mod.scan(readable_orders, name_list)
        # The safety check sees EVERY hit, letterhead included — it asks whether a
        # short name is slipping in on the default rule, and a filtered view would
        # hide exactly the case it exists to catch.
        hits = scan_mod.name_hit_counts(result.mentions, name_list)
        needing_a_rule = namelist_mod.names_needing_a_rule(name_list, hits)
        unused = namelist_mod.unused_rules(name_list)
        # The table a person reads leaves the letterhead out, so the numbers mean
        # "how often this agency comes up".
        body_hits = scan_mod.name_hit_counts(result.mentions, name_list,
                                             body_only=True)
        most_hit = sorted(body_hits.items(), key=lambda t: (-t[1], t[0]))[:20]
        in_letterhead = sum(1 for m in result.mentions if m.in_letterhead)
        generated = name_list.counts()["from_mayoral_variant"]
        print(f"Pass one: {len(result.mentions)} names found "
              f"({in_letterhead} of them letterhead); "
              f"{generated} mayoral short spellings built; "
              f"{len(needing_a_rule)} short names need a rule; "
              f"{len(unused)} rules unused")

        # Carry the registry's own record for every agency the pass matched, so
        # mentions.json can be read — and displayed — without the registry.
        registry_agencies = namelist_mod.load_registry(args.registry)
        described = agencies_mod.load_descriptions(
            args.registry.parent / DESCRIPTIONS_NAME)
        result.agencies = agencies_mod.build(
            agencies_mod.ids_in(result.mentions), registry_agencies, described,
            namelist_mod.load_extra_agencies(EXTRA_AGENCIES),
            name_list.generated_names_by_agency())
        with_words = sum(1 for a in result.agencies if a.get("description"))
        from_registry = sum(1 for a in result.agencies
                            if a["id"] in {r.get("id") for r in registry_agencies})
        print(f"Agencies found: {len(result.agencies)} "
              f"({from_registry} of {len(registry_agencies)} in the registry, "
              f"{len(result.agencies) - from_registry} from the extra file); "
              f"{with_words} carry a description")

    if run_new:
        known = (frozenset(n.normalized for n in name_list.names)
                 if name_list else frozenset())
        result.proposed, result.discarded = discover_mod.find_new_names(
            readable_orders, known, result.mentions)
        print(f"Pass two: {len(result.proposed)} proposed, "
              f"{sum(d.count for d in result.discarded)} thrown out")

    # Pass three — what the orders do to agencies. It reads the spans the two
    # passes above produced, so it works with whichever of them ran.
    result.agency_events, result.unresolved_events = reorg_mod.find_events(
        readable_orders, result.mentions, result.proposed)
    resolved = sum(1 for e in result.agency_events if e.fully_resolved)
    print(f"Pass three: {len(result.agency_events)} agency events "
          f"({resolved} pin down a known agency on every side); "
          f"{len(result.unresolved_events)} sentences to review")

    # Pass four — what the orders do to each other. Needs no registry.
    (result.order_edges, result.order_dangles,
     result.extensions_skipped) = citations_mod.extract(readable_orders)
    print(f"Pass four: {len(result.order_edges)} order-to-order edges; "
          f"{len(result.order_dangles)} citations went nowhere; "
          f"{result.extensions_skipped} extensions skipped")

    payload = build_payload(result, generated_by=GENERATED_BY,
                            review_from=args.review_from)
    report = render_report(result, payload, needing_a_rule=needing_a_rule,
                           unused_rules=unused, most_hit=most_hit)

    if args.dry_run:
        print("\nDRY-RUN — nothing written\n")
        print(report)
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    mentions_json = args.mentions_out or (args.out / "mentions.json")
    report_md = args.out / "report.md"
    mentions_json.parent.mkdir(parents=True, exist_ok=True)
    mentions_json.write_text(dumps(payload), encoding="utf-8")
    report_md.write_text(report, encoding="utf-8")

    print("\nWROTE:")
    print(f"  {mentions_json}")
    print(f"  {report_md}")
    print(f"\nfound={payload['mention_count']} "
          f"body={payload['body_mention_count']} "
          f"letterhead={payload['letterhead_count']} "
          f"agencies={payload['agency_count']} "
          f"proposed={payload['proposed_count']} "
          f"to_review={payload['to_review_count']} "
          f"rare={payload['rare_count']} "
          f"events={payload['agency_event_count']} "
          f"resolved={payload['fully_resolved_event_count']} "
          f"unresolved={payload['unresolved_event_count']} "
          f"order_edges={payload['order_edge_count']} "
          f"need_a_rule={len(needing_a_rule)}")
    return 0


def _load_name_list(registry_path: Path) -> namelist_mod.NameList:
    """Load all three inputs and build the name list."""
    rules, short_name_length, max_hits = namelist_mod.load_rules(NAME_RULES)
    return namelist_mod.build(
        namelist_mod.load_registry(registry_path),
        namelist_mod.load_extra_agencies(EXTRA_AGENCIES),
        rules,
        short_name_length,
        max_hits,
    )


if __name__ == "__main__":
    raise SystemExit(main())
