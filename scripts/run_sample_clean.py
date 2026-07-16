#!/usr/bin/env python3
"""Run the deterministic cleaner on a stratified SAMPLE and emit a report.

READ-ONLY on the corpus. Selects a ~60-doc stratified, worst-weighted sample from
the OCR population (the 916 scanned docs) using a fully deterministic recipe (no
randomness; stable ``eo_id`` ordering), runs
:func:`nyc_executive_orders.clean.clean_record` on each, and writes before/after
diffs + a summary quality report under ``sample_clean_report/``.

It NEVER writes into ``corpus/``, never regenerates the corpus, never runs the
full 916 sweep. The sample exists so a human can approve the cleaning RULES (esp.
the lexicon title gate) before any full run.

Selection metrics come from running the cleaner IN MEMORY over every OCR record
(analysis, not a sweep — nothing is written). Source of record bodies + fields is
``corpus/eo.json`` (the bulk artifact; same body as each ``corpus/YYYY/*.md``).

Run:
    uv run --no-project python scripts/run_sample_clean.py
"""

from __future__ import annotations

import difflib
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders import lexicon  # noqa: E402
from nyc_executive_orders.clean import clean_record  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
EO_JSON = REPO_ROOT / "corpus" / "eo.json"
OUT_DIR = REPO_ROOT / "sample_clean_report"

# The original 17-doc sample (round 1) — always included so behavior is diffable.
ORIG_TIER1 = [
    "1976-EO-052", "1975-EO-039", "1996-EO-027", "1976-EO-064", "1979-EO-040",
    "1980-EO-043", "1998-EO-043", "1978-EO-017", "1974-EO-018", "1977-EO-091",
    "1974-EO-001", "1986-EO-101",
]
ORIG_TIER2 = ["1980-EO-049", "2013-EO-429", "2022-EEO-125"]
ORIG_CONTROLS = ["2022-EEO-295", "2008-EO-110"]

# Selection thresholds (deterministic recipe; tuned to land ~60 docs).
HEAVY_NOISE_TOPN = 25       # the N worst OCR docs by trimmed leading-noise chars
AMBIGUOUS_CAP = 10          # first N anchor-after-body / large-trim docs
EMPTY_TITLE_TOPN = 10       # first N OCR docs with an empty title (the 337-set)
# Per-era OCR coverage: worst-by-leading-noise within each era. 2002-2021 is
# essentially all born-digital (one lone scanned doc), so it contributes ~1.
ERA_OCR_TOPN = {"1974-2001": 10, "2002-2021": 6, "2022-2026": 12}
ERAS = [("1974-2001", 1974, 2001), ("2002-2021", 2002, 2021), ("2022-2026", 2022, 2026)]


def _era(year: int) -> str:
    for name, lo, hi in ERAS:
        if lo <= year <= hi:
            return name
    return "other"


def _analyze(record: dict) -> dict:
    """Run the cleaner in memory and pull the selection/report signals."""
    year = int(record["year"])
    r = clean_record(
        record.get("full_text", ""),
        year=year,
        existing_title=record.get("title"),
        existing_date_signed=record.get("date_signed"),
        text_source=record.get("text_source"),
    )
    return {
        "eo_id": record["eo_id"],
        "year": year,
        "era": _era(year),
        "text_source": record.get("text_source"),
        "result": r,
        "ln": len(r.dropped_header),
        "tier": r.text_quality,
        "flags": r.flags,
        "title_before_empty": not (record.get("title") or "").strip(),
        "date_before_null": not record.get("date_signed"),
        "title_extracted": r.title_extracted,
        "date_extracted": r.date_extracted,
        "title_uncertain": any(f.startswith("title-uncertain") for f in r.flags),
        "record": record,
    }


def select_sample(analyses: dict[str, dict]) -> dict[str, str]:
    """Deterministically choose the sample. Returns ``{eo_id: stratum_label}``.

    Union of strata; first assignment wins for the label (originals first). Every
    ``sorted(...)`` uses ``eo_id`` as the stable key — no randomness anywhere.
    """
    chosen: dict[str, str] = {}

    def add(eo_id: str, label: str) -> None:
        if eo_id in analyses:
            chosen.setdefault(eo_id, label)

    # A. Originals (labelled by their round-1 tier).
    for e in ORIG_TIER1:
        add(e, "orig-tier1")
    for e in ORIG_TIER2:
        add(e, "orig-tier2")
    for e in ORIG_CONTROLS:
        add(e, "orig-control")

    ocr = [a for a in analyses.values() if a["text_source"] == "ocr"]
    by_noise = sorted(ocr, key=lambda a: (-a["ln"], a["eo_id"]))

    # B. Worst leading-noise: the top-N OCR docs by trimmed header chars.
    for a in by_noise[:HEAVY_NOISE_TOPN]:
        add(a["eo_id"], "heavy-noise")

    # C. Ambiguous/worst (guard-triggered: content preserved instead of trimmed).
    ambiguous = [
        a for a in ocr
        if any(f.startswith(("anchor-after-body-start", "large-header-trim-skipped"))
               for f in a["flags"])
    ]
    for a in sorted(ambiguous, key=lambda a: a["eo_id"])[:AMBIGUOUS_CAP]:
        add(a["eo_id"], "ambiguous")

    # D. Per-era OCR coverage — worst-by-leading-noise within each era.
    for name, lo, hi in ERAS:
        bucket = [a for a in by_noise if a["era"] == name]
        for a in bucket[:ERA_OCR_TOPN.get(name, 0)]:
            add(a["eo_id"], f"era-{name}")

    # E. The empty-title set (337-set representation): first N OCR docs by eo_id
    #    whose title was empty pre-clean.
    empty_title = [a for a in ocr if a["title_before_empty"]]
    for a in sorted(empty_title, key=lambda a: a["eo_id"])[:EMPTY_TITLE_TOPN]:
        add(a["eo_id"], "empty-title")

    # F. One extra born-digital control (first born-digital by eo_id not yet chosen).
    born = sorted(
        (a for a in analyses.values() if a["text_source"] == "born-digital"),
        key=lambda a: a["eo_id"],
    )
    for a in born:
        if a["eo_id"] not in chosen:
            add(a["eo_id"], "extra-control")
            break

    return chosen


def _fmt(val) -> str:
    if val is None:
        return "null"
    if val == "":
        return "'' (empty)"
    return repr(val)


def _doc_report(a: dict, label: str) -> str:
    r = a["result"]
    rec = a["record"]
    lines = [f"## {a['eo_id']} ({label}, era {a['era']})\n",
             f"- text_source: `{a['text_source']}`",
             f"- **text_quality: `{r.text_quality}`**",
             f"- anchor_found: `{r.anchor_found}` (label: `{r.anchor_label}`)\n",
             "### Frontmatter delta",
             f"- title: {_fmt(rec.get('title'))} -> {_fmt(r.title)}"
             f"  {'(EXTRACTED)' if r.title_extracted else '(unchanged)'}",
             f"- date_signed: {_fmt(rec.get('date_signed'))} -> {_fmt(r.date_signed)}"
             f"  {'(EXTRACTED)' if r.date_extracted else '(unchanged)'}\n",
             "### Metrics"]
    lines += [f"- {k}: `{v}`" for k, v in r.metrics.items()]
    lines.append("")
    if r.flags:
        lines.append("### Flags")
        lines += [f"- {f}" for f in r.flags]
        lines.append("")
    lines.append("### dropped_header (relocated, not deleted)")
    lines.append(f"```\n{r.dropped_header}\n```" if r.dropped_header
                 else "_(empty — nothing trimmed)_")
    lines.append("")
    lines.append("### dropped_marks")
    lines.append(f"{r.dropped_marks if r.dropped_marks else '_(none)_'}\n")
    lines.append("### Body diff (raw OCR -> cleaned)")
    diff = list(difflib.unified_diff(
        r.full_text_raw.splitlines(), r.full_text.splitlines(),
        fromfile="raw", tofile="cleaned", lineterm=""))
    if diff:
        shown = diff[:100]
        lines.append("```diff\n" + "\n".join(shown)
                     + ("\n... (truncated)" if len(diff) > 100 else "") + "\n```")
    else:
        lines.append("_(no change to body)_")
    lines.append("\n---\n")
    return "\n".join(lines)


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = json.loads(EO_JSON.read_text(encoding="utf-8"))
    analyses = {rec["eo_id"]: _analyze(rec) for rec in records}
    n_ocr_pop = sum(1 for a in analyses.values() if a["text_source"] == "ocr")

    chosen = select_sample(analyses)
    selected = sorted(chosen)  # stable eo_id order

    for eo_id in selected:
        (OUT_DIR / f"{eo_id}.md").write_text(
            _doc_report(analyses[eo_id], chosen[eo_id]), encoding="utf-8")

    sel = [analyses[e] for e in selected]
    ocr_sel = [a for a in sel if a["text_source"] == "ocr"]

    out: list[str] = ["# EO corpus cleaner — WIDE SAMPLE report (round 2)\n"]
    out.append(f"Deterministic {len(selected)}-doc stratified sample of the "
               f"{n_ocr_pop} OCR docs. READ-ONLY on `corpus/`; nothing modified, "
               "no full sweep. Selection metrics computed by running the cleaner "
               "in memory over every OCR record.\n")
    out.append(f"- **Lexicon source (title gate): `{lexicon.english_lexicon_source()}`** "
               "— FROZEN in-repo list; host dictionary not consulted, so title "
               "accept/reject is reproducible on every machine.")
    out.append(f"- Sample size: **{len(selected)}** ({len(ocr_sel)} OCR + "
               f"{len(selected) - len(ocr_sel)} born-digital).\n")

    out.append("## text_quality distribution (selected sample)\n")
    for q in ("clean", "minor-noise", "needs-review"):
        out.append(f"- {q}: {sum(1 for a in sel if a['tier'] == q)}")
    out.append("")

    empty_title = [a for a in ocr_sel if a["title_before_empty"]]
    accepted = [a for a in empty_title if a["title_extracted"]]
    held = [a for a in empty_title if a["title_uncertain"]]
    none_found = [a for a in empty_title if not a["title_extracted"] and not a["title_uncertain"]]
    out.append("## Title lexicon gate (selected OCR docs with an empty title)\n")
    out.append(f"- empty-title docs in sample: {len(empty_title)}")
    out.append(f"- **auto-accepted (all tokens recognized): {len(accepted)}**")
    out.append(f"- **held as title-uncertain (>=1 unrecognized token): {len(held)}**")
    out.append(f"- no caps subject line found at all: {len(none_found)}\n")
    if accepted:
        out.append("Auto-accepted titles:")
        out += [f"  - {a['eo_id']}: {a['result'].title!r}" for a in accepted]
        out.append("")
    if held:
        out.append("Held (surfaced for human review, NOT written):")
        for a in held:
            cand = next((f for f in a["flags"] if f.startswith("title-uncertain")), "")
            out.append(f"  - {a['eo_id']}: {cand}")
        out.append("")

    empty_date = [a for a in ocr_sel if a["date_before_null"]]
    out.append("## Date extraction (selected OCR docs with null date)\n")
    out.append(f"- null-date docs: {len(empty_date)}; extracted: "
               f"{sum(1 for a in empty_date if a['date_extracted'])}\n")

    out.append("## Strata (each doc labelled by first-matched stratum)\n")
    for label, n in sorted(Counter(chosen.values()).items()):
        out.append(f"- {label}: {n}")
    out.append("")

    out.append("## Controls (born-digital — body must be untouched)\n")
    for a in sel:
        if a["text_source"] != "born-digital":
            continue
        ok = a["ln"] == 0 and not a["result"].dropped_marks and a["tier"] == "clean"
        gap = [x for x, on in (("title", a["title_extracted"]), ("date", a["date_extracted"])) if on]
        note = f"; gap-filled {'+'.join(gap)}" if gap else ""
        out.append(f"- {a['eo_id']}: {'PASS' if ok else 'CHECK'} "
                   f"(hdr={a['ln']}, marks={len(a['result'].dropped_marks)}, "
                   f"quality={a['tier']}{note})")
    out.append("")

    out.append("## Outcome table (sorted by eo_id)\n")
    out.append("| eo_id | stratum | src | quality | anc | hdr | marks | title | date |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for a in sel:
        r = a["result"]
        tflag = ("acc" if r.title_extracted else
                 "unc" if a["title_uncertain"] else
                 "kept" if not a["title_before_empty"] else "-")
        dflag = ("y" if r.date_extracted else
                 "kept" if not a["date_before_null"] else "-")
        out.append(
            f"| {a['eo_id']} | {chosen[a['eo_id']]} | {a['text_source'][:4]} | "
            f"{r.text_quality} | {r.anchor_found} | {a['ln']} | "
            f"{len(r.dropped_marks)} | {tflag} | {dflag} |")
    out.append("")

    (OUT_DIR / "REPORT.md").write_text("\n".join(out), encoding="utf-8")
    print(f"Selected {len(selected)} docs -> {OUT_DIR}/REPORT.md (+ per-doc diffs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
