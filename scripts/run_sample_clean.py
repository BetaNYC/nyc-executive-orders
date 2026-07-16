#!/usr/bin/env python3
"""Run the deterministic cleaner on a small stratified SAMPLE and emit a report.

READ-ONLY on the corpus. This is the "eyeball the RULES before any full sweep"
tool from the sample-cleaner task: it reads the published ``corpus/YYYY/<eo_id>.md``
files for a fixed ~17-doc sample, runs :func:`nyc_executive_orders.clean.clean_record`
on each body, and writes human-readable before/after diffs + a summary quality
report under ``sample_clean_report/``.

It NEVER writes into ``corpus/`` and never regenerates the corpus. The sample
output exists so a human can approve the cleaning rules; nothing here mutates the
published dataset.

Run (isolated, no project deps needed beyond pyyaml):
    uv run --no-project --with pyyaml python scripts/run_sample_clean.py
Or, inside the project env:
    uv run python scripts/run_sample_clean.py
"""

from __future__ import annotations

import difflib
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders.clean import clean_record  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO_ROOT / "corpus"
OUT_DIR = REPO_ROOT / "sample_clean_report"

# Fixed, stratified, worst-weighted sample (from the task brief + eo.json scan).
TIER1 = [
    "1976-EO-052", "1975-EO-039", "1996-EO-027", "1976-EO-064", "1979-EO-040",
    "1980-EO-043", "1998-EO-043", "1978-EO-017", "1974-EO-018", "1977-EO-091",
    "1974-EO-001", "1986-EO-101",
]
# Tier 2 (moderate): 1980s / born-digital-era-scanned / 2020s.
TIER2 = ["1980-EO-049", "2013-EO-429", "2022-EEO-125"]
# Controls: already-clean born-digital docs — must stay untouched.
CONTROLS = ["2022-EEO-295", "2008-EO-110"]

SAMPLE = (
    [("tier1", e) for e in TIER1]
    + [("tier2", e) for e in TIER2]
    + [("control", e) for e in CONTROLS]
)


def _split_frontmatter(md_text: str) -> tuple[dict, str]:
    """Split a corpus ``.md`` into (frontmatter dict, body str)."""
    if not md_text.startswith("---\n"):
        return {}, md_text
    end = md_text.find("\n---\n", 4)
    if end == -1:
        return {}, md_text
    fm = yaml.safe_load(md_text[4:end]) or {}
    body = md_text[end + len("\n---\n"):].lstrip("\n")
    return fm, body


def _corpus_path(eo_id: str) -> Path:
    year = eo_id[:4]
    return CORPUS_DIR / year / f"{eo_id}.md"


def _fmt(val) -> str:
    if val is None:
        return "null"
    if val == "":
        return "'' (empty)"
    return repr(val)


def _doc_report(kind: str, eo_id: str) -> tuple[str, dict]:
    path = _corpus_path(eo_id)
    if not path.exists():
        return f"## {eo_id} ({kind}) — MISSING at {path}\n", {"eo_id": eo_id, "missing": True}
    fm, body = _split_frontmatter(path.read_text(encoding="utf-8"))
    year = int(fm.get("year", eo_id[:4]))
    r = clean_record(
        body,
        year=year,
        existing_title=fm.get("title"),
        existing_date_signed=fm.get("date_signed"),
        text_source=fm.get("text_source"),
    )

    lines: list[str] = []
    lines.append(f"## {eo_id} ({kind})\n")
    lines.append(f"- text_source: `{fm.get('text_source')}`")
    lines.append(f"- **text_quality: `{r.text_quality}`**")
    lines.append(f"- anchor_found: `{r.anchor_found}` (label: `{r.anchor_label}`)")
    lines.append("")
    lines.append("### Frontmatter delta")
    lines.append(f"- title: {_fmt(fm.get('title'))} -> {_fmt(r.title)}"
                 f"  {'(EXTRACTED)' if r.title_extracted else '(unchanged)'}")
    lines.append(f"- date_signed: {_fmt(fm.get('date_signed'))} -> {_fmt(r.date_signed)}"
                 f"  {'(EXTRACTED)' if r.date_extracted else '(unchanged)'}")
    lines.append("")
    lines.append("### Metrics")
    for k, v in r.metrics.items():
        lines.append(f"- {k}: `{v}`")
    lines.append("")
    if r.flags:
        lines.append("### Flags")
        for f in r.flags:
            lines.append(f"- {f}")
        lines.append("")
    lines.append("### dropped_header (relocated, not deleted)")
    if r.dropped_header:
        lines.append(f"```\n{r.dropped_header}\n```")
    else:
        lines.append("_(empty — nothing trimmed)_")
    lines.append("")
    lines.append("### dropped_marks (relocated, not deleted)")
    lines.append(f"{r.dropped_marks if r.dropped_marks else '_(none)_'}")
    lines.append("")
    lines.append("### Body diff (raw OCR -> cleaned)")
    diff = list(difflib.unified_diff(
        r.full_text_raw.splitlines(),
        r.full_text.splitlines(),
        fromfile=f"{eo_id} raw", tofile=f"{eo_id} cleaned", lineterm="",
    ))
    if diff:
        shown = diff[:120]
        lines.append("```diff\n" + "\n".join(shown)
                     + ("\n... (diff truncated)" if len(diff) > 120 else "") + "\n```")
    else:
        lines.append("_(no change to body)_")
    lines.append("\n---\n")

    summary = {
        "eo_id": eo_id,
        "kind": kind,
        "text_source": fm.get("text_source"),
        "text_quality": r.text_quality,
        "anchor_found": r.anchor_found,
        "dropped_header_chars": len(r.dropped_header),
        "dropped_marks": len(r.dropped_marks),
        "title_extracted": r.title_extracted,
        "date_extracted": r.date_extracted,
        "title_before_empty": not (fm.get("title") or "").strip(),
        "date_before_null": not fm.get("date_signed"),
        "flags": r.flags,
    }
    return "\n".join(lines), summary


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summaries: list[dict] = []
    for kind, eo_id in SAMPLE:
        report, summary = _doc_report(kind, eo_id)
        (OUT_DIR / f"{eo_id}.md").write_text(report, encoding="utf-8")
        summaries.append(summary)

    # ---- summary report ---------------------------------------------------- #
    out: list[str] = []
    out.append("# EO corpus cleaner — SAMPLE report\n")
    out.append("Deterministic, rule-based, non-destructive cleaner run on a fixed "
               "~17-doc stratified sample. READ-ONLY on `corpus/`; no corpus files "
               "were modified. Per-doc diffs are in this directory "
               "(`sample_clean_report/<eo_id>.md`).\n")
    out.append("## Outcome table\n")
    out.append("| eo_id | kind | source | quality | anchor | hdr chars | marks | "
               "title extr | date extr |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for s in summaries:
        if s.get("missing"):
            out.append(f"| {s['eo_id']} | — | — | MISSING | — | — | — | — | — |")
            continue
        out.append(
            f"| {s['eo_id']} | {s['kind']} | {s['text_source']} | "
            f"**{s['text_quality']}** | {s['anchor_found']} | "
            f"{s['dropped_header_chars']} | {s['dropped_marks']} | "
            f"{s['title_extracted']} | {s['date_extracted']} |"
        )
    out.append("")

    # ---- aggregate stats --------------------------------------------------- #
    real = [s for s in summaries if not s.get("missing")]
    ocr = [s for s in real if s["text_source"] == "ocr"]
    title_gap = [s for s in ocr if s["title_before_empty"]]
    date_gap = [s for s in ocr if s["date_before_null"]]
    out.append("## Aggregate\n")
    out.append(f"- Docs in sample: {len(real)}")
    out.append(f"- OCR docs: {len(ocr)}; born-digital controls: "
               f"{len([s for s in real if s['text_source'] == 'born-digital'])}")
    out.append(f"- Anchor found (header trimmed or confirmed clean): "
               f"{len([s for s in real if s['anchor_found']])}/{len(real)}")
    out.append(f"- Title extracted where it was empty: "
               f"{len([s for s in title_gap if s['title_extracted']])}/{len(title_gap)}")
    out.append(f"- Date extracted where it was null: "
               f"{len([s for s in date_gap if s['date_extracted']])}/{len(date_gap)}")
    for tier_q in ("clean", "minor-noise", "needs-review"):
        out.append(f"- text_quality == {tier_q}: "
                   f"{len([s for s in real if s['text_quality'] == tier_q])}")
    out.append("")
    out.append("## Controls check (body must be untouched)\n")
    out.append("A control PASSES when the cleaner relocated NOTHING out of the body "
               "(no header trim, no marks) and tiered it `clean`. Filling an "
               "*empty* title/date on a born-digital doc from its own body is a "
               "correct gap-fill, not a violation — noted separately.\n")
    for s in real:
        if s["kind"] != "control":
            continue
        ok = (s["dropped_header_chars"] == 0 and s["dropped_marks"] == 0
              and s["text_quality"] == "clean")
        gapfill = []
        if s["title_extracted"]:
            gapfill.append("title")
        if s["date_extracted"]:
            gapfill.append("date")
        note = f"; gap-filled {'+'.join(gapfill)} (was empty)" if gapfill else ""
        out.append(f"- {s['eo_id']}: {'PASS' if ok else 'CHECK'} "
                   f"(hdr={s['dropped_header_chars']}, marks={s['dropped_marks']}, "
                   f"quality={s['text_quality']}{note})")
    out.append("")
    out.append("## Docs flagged needs-review\n")
    for s in real:
        if s["text_quality"] == "needs-review":
            out.append(f"- {s['eo_id']} ({s['kind']}): flags={s['flags']}")
    out.append("")

    (OUT_DIR / "REPORT.md").write_text("\n".join(out), encoding="utf-8")
    print(f"Wrote {len(summaries)} per-doc reports + REPORT.md to {OUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
