#!/usr/bin/env python3
"""Phase F QA — is the VLM text actually better than the Tesseract text it replaces?

The comparison is exact and costs nothing, because `corpus/eo.json` is committed
and already holds `full_text_raw` — the verbatim Tesseract transcription — for the
1,019 orders that have one. Snapshot the metrics BEFORE the first rebuild
overwrites that file, then diff every document against its own past.

Three stages:

    --stage classify   after run_post1974_ocr.py --classify-blank-only.
                       THE CALIBRATION ORACLE: a page classified blank inside a
                       document that already has Tesseract text is a PROVEN false
                       blank, because a blank page cannot have produced text. The
                       gate is zero of those.
    --stage ocr        after an OCR batch. Coverage and QA flags per year.
    --stage diff       THE CUTOVER GATE. Old vs new, per document.

Hard failures (non-zero exit) are things no threshold should excuse: a document
that lost text it used to have, a selected document with no records, a document
whose every page came back blank. Everything else is listed for a human.

    python scripts/report_post1974_ocr.py --snapshot          # do this FIRST
    python scripts/report_post1974_ocr.py --stage classify
    python scripts/report_post1974_ocr.py --stage diff --samples 10

No network. No model. Reads committed files and writes a report.
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders import clean  # noqa: E402
from nyc_executive_orders.vlm_corpus import (  # noqa: E402
    STATUS_UNREADABLE,
    doc_ocr_dir,
    load_vlm_document,
    select_candidates,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECORDS = REPO_ROOT / "corpus" / "eo.json"
DEFAULT_OCR_ROOT = REPO_ROOT / "sources" / "ocr"
DEFAULT_BASELINE = REPO_ROOT / "sources" / "ocr" / "_tesseract_baseline.json"
DEFAULT_REPORT = REPO_ROOT / "post1974_ocr_report.md"

# A document below this share of its old character count lost something. Listed
# for review, not failed: a VLM body legitimately drops the scanner artefacts and
# repeated furniture that Tesseract emitted as text.
SHRINK_REVIEW_RATIO = 0.5
GROWTH_REVIEW_RATIO = 2.0

# Below this, a document that HAD text now has effectively none. Hard failure.
LOST_TEXT_CHARS = 200


def _quiet_mupdf() -> None:
    try:
        import fitz

        fitz.TOOLS.mupdf_display_errors(False)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


def metrics(text: str) -> dict:
    """The same three numbers clean._tier decides a record's quality on."""
    return {
        "chars": len(text),
        "word_ratio": round(clean._word_ratio(text), 4),
        "junk_ratio": round(clean._junk_ratio(text), 4),
    }


# --------------------------------------------------------------------------- #
# Baseline snapshot                                                             #
# --------------------------------------------------------------------------- #

def snapshot(records: list[dict], out_path: Path) -> dict:
    """Freeze the Tesseract metrics before a rebuild overwrites corpus/eo.json.

    Metrics only, never a second copy of the text: ~200 KB rather than megabytes,
    and the text itself stays exactly where it already is, in git history.
    """
    payload = {}
    for r in records:
        if r.get("text_source") != "ocr":
            continue
        text = r.get("full_text_raw") or r.get("full_text") or ""
        payload[r["eo_id"]] = {
            **metrics(text),
            "text_quality": r.get("text_quality"),
            "page_count": r.get("page_count"),
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8")
    return payload


# --------------------------------------------------------------------------- #
# Stages                                                                        #
# --------------------------------------------------------------------------- #

def stage_classify(candidates, ocr_root: Path, baseline: dict) -> tuple[list[str], list[str]]:
    """Every blank verdict, cross-checked against whether Tesseract found text."""
    lines = ["## Stage 0 — blank-page calibration", ""]
    failures = []
    dark, std_vals = [], []
    n_pages = n_blank = n_reports = 0
    proven_false = []

    for c in candidates:
        if not c.selected:
            continue
        report_path = doc_ocr_dir(ocr_root, c.year, c.eo_id) / "classify_report.json"
        if not report_path.is_file():
            continue
        n_reports += 1
        report = json.loads(report_path.read_text(encoding="utf-8"))
        pages = report.get("pages", [])
        n_pages += len(pages)
        blanks = [p for p in pages if p.get("blank")]
        n_blank += len(blanks)
        for p in pages:
            stats = p.get("page_stats") or {}
            if "dark_fraction" in stats:
                dark.append(stats["dark_fraction"])
            if "std" in stats:
                std_vals.append(stats["std"])
        if blanks:
            had = baseline.get(c.eo_id, {}).get("chars", 0)
            verdict = "PROVEN FALSE" if had >= LOST_TEXT_CHARS else "plausible"
            row = (f"- `{c.eo_id}` — {len(blanks)}/{len(pages)} page(s) blank "
                   f"(Tesseract had {had} chars) — **{verdict}**")
            if verdict == "PROVEN FALSE":
                proven_false.append(row)
                failures.append(f"{c.eo_id}: page(s) classified blank but the document "
                                f"already has {had} characters of OCR text")
            else:
                lines.append(row)

    if not n_reports:
        lines.append("_No classify_report.json found. Run "
                     "`run_post1974_ocr.py --classify-blank-only` first._")
        return lines, failures

    lines[1:1] = [
        "",
        f"Scored **{n_pages} page(s)** across **{n_reports} document(s)**. "
        f"**{n_blank}** classify as blank.",
        "",
        "| measure | min | p05 | median | threshold |",
        "|---|---|---|---|---|",
        f"| `dark_fraction` | {_q(dark, 0):.5f} | {_q(dark, 0.05):.5f} | "
        f"{_q(dark, 0.5):.5f} | 0.001 |",
        f"| `std` | {_q(std_vals, 0):.2f} | {_q(std_vals, 0.05):.2f} | "
        f"{_q(std_vals, 0.5):.2f} | 6.5 |",
        "",
        "The test is an AND: a page must be below BOTH to be called blank.",
        "",
    ]
    if proven_false:
        lines += ["", "### Proven false blanks — DO NOT OCR until this is fixed", ""]
        lines += proven_false
    elif not n_blank:
        lines += ["", "**Gate passed.** No page classifies as blank at all, so no page "
                  "can be a false blank. The shipped thresholds stand unchanged.", ""]
    return lines, failures


def _q(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(int(q * (len(s) - 1)), len(s) - 1)]


def stage_ocr(candidates, ocr_root: Path) -> tuple[list[str], list[str]]:
    """Per-year coverage and the QA flags the run raised."""
    lines = ["## Stage 1 — OCR coverage", ""]
    failures = []
    per_year = defaultdict(lambda: Counter())
    flags = Counter()
    overrides = []

    for c in candidates:
        if not c.selected:
            continue
        row = per_year[c.year]
        row["docs"] += 1
        row["pages"] += c.page_count
        vlm = load_vlm_document(ocr_root, c.year, c.eo_id)
        if vlm is None or not vlm.records_present:
            continue
        row["recorded"] += 1
        row["pages_done"] += vlm.page_count
        if not vlm.has_text:
            row["empty"] += 1
            failures.append(f"{c.eo_id}: page records present but no usable text")
        for f in vlm.flags:
            flags[f.split(":")[0]] += 1
        if vlm.doc.blank_override:
            overrides.append(c.eo_id)

    lines += ["| year | docs | recorded | pages | pages done | empty |",
              "|---|---|---|---|---|---|"]
    for year in sorted(per_year):
        r = per_year[year]
        lines.append(f"| {year} | {r['docs']} | {r['recorded']} | {r['pages']} | "
                     f"{r['pages_done']} | {r['empty']} |")

    if flags:
        lines += ["", "### QA flags raised", "",
                  "| flag | documents |", "|---|---|"]
        for f, n in flags.most_common():
            lines.append(f"| `{f}` | {n} |")
        lines += ["", "`low-ink-coverage` and `uncovered-ink` deserve a look before you "
                  "treat them as defects: on loose post-1974 letterhead the uncovered ink "
                  "is often a handwritten archival annotation or the letterhead's own "
                  "printed rules — ink on the page, but not order text.", ""]

    if overrides:
        lines += ["", "### Documents whose every page classified blank", "",
                  "OCR'd anyway, by design — a document in the corpus cannot be entirely "
                  "blank, so a unanimous verdict is evidence against the threshold. "
                  "Review each:", ""]
        lines += [f"- `{eo}`" for eo in overrides]
    return lines, failures


def stage_diff(candidates, ocr_root: Path, baseline: dict, records: list[dict],
               samples: int, sample_dir: Path) -> tuple[list[str], list[str]]:
    """Old Tesseract text vs new VLM text, per document. The cutover gate."""
    lines = ["## Stage 2 — Tesseract vs VLM", ""]
    failures = []
    if not baseline:
        lines.append("_No baseline. Run `--snapshot` BEFORE the first rebuild._")
        return lines, failures

    by_id = {r["eo_id"]: r for r in records}
    rows, regressions, lost, migration = [], [], [], Counter()

    for c in candidates:
        if not c.selected:
            continue
        old = baseline.get(c.eo_id)
        if old is None:
            continue                      # never had Tesseract text (the 67)
        vlm = load_vlm_document(ocr_root, c.year, c.eo_id)
        if vlm is None or not vlm.records_present:
            continue

        new = metrics(vlm.text)
        ratio = new["chars"] / old["chars"] if old["chars"] else 0.0
        old_q = old.get("text_quality")
        new_q = _expected_tier(vlm, by_id.get(c.eo_id, {}), c.year)
        migration[(old_q, new_q)] += 1

        if new["chars"] < LOST_TEXT_CHARS <= old["chars"]:
            lost.append(c.eo_id)
            failures.append(f"{c.eo_id}: had {old['chars']} chars of Tesseract text, "
                            f"VLM produced {new['chars']}")
        elif ratio < SHRINK_REVIEW_RATIO or ratio > GROWTH_REVIEW_RATIO:
            regressions.append((c.eo_id, old, new, ratio))

        rows.append((c.eo_id, old, new, ratio, old_q, new_q))

    if not rows:
        lines.append("_No document has both a Tesseract baseline and VLM records yet._")
        return lines, failures

    better = sum(1 for _, o, n, _, _, _ in rows if n["junk_ratio"] <= o["junk_ratio"]
                 and n["word_ratio"] >= o["word_ratio"])
    lines += [
        f"Compared **{len(rows)}** document(s) that have both.",
        "",
        f"- **{better}/{len(rows)}** improved or held on BOTH word-ratio and junk-ratio.",
        f"- **{len(lost)}** lost their text entirely (hard failure).",
        f"- **{len(regressions)}** changed length by more than "
        f"{int((1 - SHRINK_REVIEW_RATIO) * 100)}% either way.",
        "",
        "### Quality-tier migration", "",
        "| Tesseract | VLM | documents |", "|---|---|---|",
    ]
    for (a, b), n in sorted(migration.items(), key=lambda kv: -kv[1]):
        mark = " ⚠️" if a == "clean" and b == "needs-review" else ""
        lines.append(f"| {a} | {b} | {n}{mark} |")
    lines += ["", "The bar to beat is the Tesseract baseline of 923/1019 = 90.6% clean.", ""]

    if lost:
        lines += ["### Documents that LOST their text — cutover is blocked", ""]
        lines += [f"- `{eo}`" for eo in lost]
        lines.append("")

    if regressions:
        lines += ["### Large length changes — review these by hand", "",
                  "| eo_id | old chars | new chars | ratio | old junk | new junk |",
                  "|---|---|---|---|---|---|"]
        for eo, o, n, ratio in sorted(regressions, key=lambda r: r[3])[:60]:
            lines.append(f"| `{eo}` | {o['chars']} | {n['chars']} | {ratio:.2f} | "
                         f"{o['junk_ratio']:.3f} | {n['junk_ratio']:.3f} |")
        lines.append("")

    # The 180-degree render detector: a page whose /Rotate is wrong is invisible
    # to every shape test, and Tesseract masked it with rotate_pages=True.
    upside_down = [
        (eo, o, n) for eo, o, n, _, _, _ in rows
        if n["word_ratio"] < 0.5 <= o["word_ratio"] and o["word_ratio"] > 0.8
    ]
    if upside_down:
        lines += ["### Possible upside-down renders", "",
                  "924 of the 1,799 pages carry a non-zero `/Rotate`. PyMuPDF honours it, "
                  "so a page whose stored angle is WRONG renders 180 degrees out and no "
                  "shape test can see it. Re-run each with `--rotate 180 --force`:", ""]
        lines += [f"- `{eo}` (word-ratio {o['word_ratio']:.2f} -> {n['word_ratio']:.2f})"
                  for eo, o, n in upside_down]
        lines.append("")

    if samples:
        written = _write_samples(rows, ocr_root, baseline, records, samples, sample_dir)
        if written:
            lines += [f"Side-by-side diffs for the {written} largest changes: "
                      f"`{sample_dir}/`", ""]
    return lines, failures


def _expected_tier(vlm, record: dict, year: int) -> str:
    """What tier this VLM body would get, without rebuilding the corpus."""
    from nyc_executive_orders.build_corpus import _forced_review

    if not vlm.has_text:
        return "no-text"
    result = clean.clean_record(
        vlm.text, year=year,
        existing_title=record.get("title"),
        existing_date_signed=record.get("date_signed"),
        text_source="ocr-vlm", apply_body_edits=True,
    )
    return ("needs-review" if _forced_review(vlm.flags) else result.text_quality)


def _write_samples(rows, ocr_root, baseline, records, samples, sample_dir) -> int:
    by_id = {r["eo_id"]: r for r in records}
    worst = sorted(rows, key=lambda r: abs(1 - r[3]), reverse=True)[:samples]
    if not worst:
        return 0
    sample_dir.mkdir(parents=True, exist_ok=True)
    for eo, old, new, ratio, _, _ in worst:
        rec = by_id.get(eo, {})
        old_text = (rec.get("full_text_raw") or rec.get("full_text") or "").splitlines()
        vlm = load_vlm_document(ocr_root, int(eo[:4]), eo)
        new_text = vlm.text.splitlines() if vlm else []
        diff = "\n".join(difflib.unified_diff(old_text, new_text,
                                              "tesseract", "vlm", lineterm=""))
        (sample_dir / f"{eo}.md").write_text(
            f"# {eo}\n\nchars {old['chars']} -> {new['chars']} (ratio {ratio:.2f})\n\n"
            f"## Tesseract\n\n```\n" + "\n".join(old_text) + "\n```\n\n"
            f"## VLM\n\n```\n" + "\n".join(new_text) + "\n```\n\n"
            f"## Diff\n\n```diff\n{diff}\n```\n",
            encoding="utf-8",
        )
    return len(worst)


# --------------------------------------------------------------------------- #
# CLI                                                                           #
# --------------------------------------------------------------------------- #

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--stage", choices=["classify", "ocr", "diff", "all"], default="all")
    p.add_argument("--snapshot", action="store_true",
                   help="Freeze the Tesseract metrics baseline and exit. Do this BEFORE "
                        "the first rebuild overwrites corpus/eo.json.")
    p.add_argument("--samples", type=int, default=0,
                   help="Write side-by-side diffs for the N largest changes.")
    p.add_argument("--sample-dir", default=str(REPO_ROOT / "sample_vlm_diff"))
    p.add_argument("--records", default=str(DEFAULT_RECORDS))
    p.add_argument("--ocr-root", default=str(DEFAULT_OCR_ROOT))
    p.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    p.add_argument("--report-path", default=str(DEFAULT_REPORT))
    p.add_argument("--year", type=int, action="append", default=None)
    return p


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)
    _quiet_mupdf()

    records = json.loads(Path(args.records).read_text(encoding="utf-8"))
    baseline_path = Path(args.baseline)

    if args.snapshot:
        payload = snapshot(records, baseline_path)
        print(f"snapshot: {len(payload)} Tesseract record(s) -> {baseline_path}")
        return 0

    baseline = (json.loads(baseline_path.read_text(encoding="utf-8"))
                if baseline_path.is_file() else {})
    ocr_root = Path(args.ocr_root)
    candidates = select_candidates(
        records, repo_root=REPO_ROOT,
        years=set(args.year) if args.year else None,
    )

    body = ["# Post-1974 VLM OCR report", "",
            "Generated by `scripts/report_post1974_ocr.py`. Every number here is "
            "measured from committed files; nothing is estimated.", ""]
    failures: list[str] = []

    if args.stage in ("classify", "all"):
        lines, f = stage_classify(candidates, ocr_root, baseline)
        body += lines + [""]
        failures += f
    if args.stage in ("ocr", "all"):
        lines, f = stage_ocr(candidates, ocr_root)
        body += lines + [""]
        failures += f
    if args.stage in ("diff", "all"):
        lines, f = stage_diff(candidates, ocr_root, baseline, records,
                              args.samples, Path(args.sample_dir))
        body += lines + [""]
        failures += f

    unreadable = [c for c in candidates if c.status == STATUS_UNREADABLE]
    if unreadable:
        body += ["## PDFs that cannot be opened at all", "",
                 "A harvest bug, not an OCR one — these were never queued.", ""]
        body += [f"- `{c.eo_id}`: {c.error}" for c in unreadable]
        body.append("")

    if failures:
        body = body[:3] + ["> **CUTOVER BLOCKED.** "
                           f"{len(failures)} hard failure(s); see below.", ""] + body[3:]
        body += ["## Hard failures", ""] + [f"- {f}" for f in failures] + [""]

    Path(args.report_path).write_text("\n".join(body) + "\n", encoding="utf-8")
    print(f"wrote {args.report_path}")
    for line in failures:
        print(f"  FAIL {line}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
