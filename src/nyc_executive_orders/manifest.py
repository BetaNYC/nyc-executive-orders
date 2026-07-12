"""Manifest + gaps report — the auditable inventory of a harvest.

`manifest.csv` is one row per EO recording what was pulled and its download
state. `gaps.md` reports, per (year, series), the EO numbers missing from the
otherwise-contiguous sequence, plus orders whose PDF URL couldn't be resolved
and any download errors. Together they make a harvest auditable: what we got AND
what we missed. Writes overwrite in place (idempotent).

Note on gap semantics: "missing numbers within a year's sequence" is a heuristic
signal, not proof of a real gap — per-mayor resets and cross-year numbering mean
a hole may be legitimate. It flags candidates for human review, nothing more.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

MANIFEST_FIELDS = [
    "eo_id",
    "number",
    "year",
    "is_emergency",
    "date_signed",
    "title",
    "article_url",
    "source_pdf_url",
    "pdf_path",
    "pdf_resolved",
    "download_status",
]


@dataclass
class ManifestRow:
    """One EO as recorded in the manifest."""

    eo_id: str
    number: int | None
    year: int
    is_emergency: bool
    date_signed: str | None
    title: str
    article_url: str
    source_pdf_url: str | None
    pdf_path: str | None
    pdf_resolved: bool
    download_status: str  # "downloaded" | "cached" | "error" | "skipped" (dry-run)
    download_error: str | None = None

    def as_row(self) -> dict:
        return {
            "eo_id": self.eo_id,
            "number": "" if self.number is None else self.number,
            "year": self.year,
            "is_emergency": str(self.is_emergency).lower(),
            "date_signed": self.date_signed or "",
            "title": self.title,
            "article_url": self.article_url,
            "source_pdf_url": self.source_pdf_url or "",
            "pdf_path": self.pdf_path or "",
            "pdf_resolved": str(self.pdf_resolved).lower(),
            "download_status": self.download_status,
        }


def write_manifest(rows: Iterable[ManifestRow], out_dir: str | Path) -> Path:
    """Write manifest.csv. Returns its path."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "manifest.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_row())
    return csv_path


def find_missing_numbers(rows: Iterable[ManifestRow]) -> dict[tuple[int, str], list[int]]:
    """Per (year, series) missing EO numbers between the min and max present.

    Series is "emergency" or "regular". Rows with an unparsed number are skipped
    (they surface separately). Returns {(year, series): [missing, ...]}.
    """
    present: dict[tuple[int, str], set[int]] = {}
    for row in rows:
        if row.number is None:
            continue
        series = "emergency" if row.is_emergency else "regular"
        present.setdefault((row.year, series), set()).add(row.number)

    missing: dict[tuple[int, str], list[int]] = {}
    for key, numbers in present.items():
        lo, hi = min(numbers), max(numbers)
        gap = [n for n in range(lo, hi + 1) if n not in numbers]
        if gap:
            missing[key] = gap
    return missing


def write_gaps(rows: Iterable[ManifestRow], out_dir: str | Path) -> Path:
    """Write gaps.md: missing numbers, unresolved PDFs, and download errors."""
    rows = list(rows)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gaps_path = out / "gaps.md"

    missing = find_missing_numbers(rows)
    unparsed = [r for r in rows if r.number is None]
    unresolved = [r for r in rows if not r.pdf_resolved]
    errors = [r for r in rows if r.download_status == "error"]

    lines: list[str] = ["# Harvest gaps & errors", ""]

    lines.append("## Missing numbers within a year's sequence")
    lines.append("")
    lines.append(
        "_Heuristic: holes between the lowest and highest number seen per "
        "(year, series). May be legitimate — review, don't assume._"
    )
    lines.append("")
    if missing:
        lines.append("| year | series | missing numbers |")
        lines.append("|---|---|---|")
        for (year, series), nums in sorted(missing.items()):
            rendered = ", ".join(str(n) for n in nums)
            lines.append(f"| {year} | {series} | {rendered} |")
    else:
        lines.append("_None — every observed sequence is contiguous._")
    lines.append("")

    lines.append("## Orders with an unparsed EO number")
    lines.append("")
    if unparsed:
        for r in unparsed:
            lines.append(f"- {r.eo_id} — {r.title} ({r.article_url})")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Orders whose PDF URL could not be resolved")
    lines.append("")
    if unresolved:
        for r in unresolved:
            lines.append(f"- {r.eo_id} — {r.title} ({r.article_url})")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Download errors")
    lines.append("")
    if errors:
        lines.append("| eo_id | source_pdf_url | error |")
        lines.append("|---|---|---|")
        for r in errors:
            lines.append(
                f"| {r.eo_id} | {r.source_pdf_url or ''} | {r.download_error or ''} |"
            )
    else:
        lines.append("_None._")
    lines.append("")

    gaps_path.write_text("\n".join(lines), encoding="utf-8")
    return gaps_path
