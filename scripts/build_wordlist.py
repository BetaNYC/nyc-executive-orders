#!/usr/bin/env python3
"""Regenerate the FROZEN title-gate wordlist committed at
``src/nyc_executive_orders/data/wordlist.txt``.

WHY a frozen list: the title gate must be deterministic and reproducible by third
parties (engineering-standards §7, Force 1). Reading the host ``/usr/share/dict/words``
at runtime made accept/reject machine-dependent. This script bakes the word set
into the repo ONCE; ``lexicon.py`` then loads only the committed file — the host
dictionary is never consulted at runtime.

Source = the system word list (BSD/macOS ``web2`` at ``/usr/share/dict/words``)
UNION the curated domain lexicon + acronyms defined in :mod:`lexicon`. Entries are
lowercased, alpha-only, de-duplicated, and sorted. A provenance header (commented
with ``#``) records how/when it was built.

Run on a machine that HAS the system dictionary (e.g. macOS):
    uv run --no-project python scripts/build_wordlist.py
Then commit the regenerated ``wordlist.txt``. Re-running on the same source yields
byte-identical output (deterministic: sorted, deduped).
"""

from __future__ import annotations

import datetime as _dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nyc_executive_orders import lexicon  # noqa: E402

SYSTEM_WORDS = Path("/usr/share/dict/words")
OUT = Path(__file__).resolve().parents[1] / "src" / "nyc_executive_orders" / "data" / "wordlist.txt"


def build() -> list[str]:
    words: set[str] = set()
    sys_count = 0
    if SYSTEM_WORDS.exists():
        for w in SYSTEM_WORDS.read_text(encoding="utf-8", errors="ignore").splitlines():
            w = w.strip().lower()
            if w and w.isalpha():
                words.add(w)
                sys_count += 1
    else:
        print(f"WARNING: {SYSTEM_WORDS} not found; freezing domain+fallback only.",
              file=sys.stderr)
    words |= {w.lower() for w in lexicon.DOMAIN_LEXICON if w.isalpha()}
    words |= {w.lower() for w in lexicon.ABBREVIATIONS if w.isalpha()}
    words |= {w.lower() for w in lexicon._FALLBACK_WORDS if w.isalpha()}
    print(f"system words: {sys_count}; total unique frozen: {len(words)}", file=sys.stderr)
    return sorted(words)


def main() -> int:
    words = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    header = [
        "# FROZEN title-gate wordlist for nyc-executive-orders.",
        "# Regenerate with: uv run --no-project python scripts/build_wordlist.py",
        f"# Built: {_dt.date.today().isoformat()}",
        "# Source: /usr/share/dict/words (BSD web2) UNION lexicon.DOMAIN_LEXICON",
        "#         + lexicon.ABBREVIATIONS + lexicon._FALLBACK_WORDS.",
        "# lowercased, alpha-only, de-duplicated, sorted. Loader skips '#' lines.",
        f"# entries: {len(words)}",
    ]
    OUT.write_text("\n".join(header) + "\n" + "\n".join(words) + "\n", encoding="utf-8")
    print(f"wrote {len(words)} words -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
