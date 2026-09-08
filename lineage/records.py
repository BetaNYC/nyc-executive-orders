"""Load the orders both passes read.

The two published corpus files share an IDENTICAL 23-key schema, so one code path
serves both: ``corpus/eo.json`` (2,291 records, 1974+, regular AND emergency) and
``corpus/eo_pre1974.json`` (978 records, 1946-1973). There are no ``eo_id``
collisions between them, so concatenating is safe.

One trap worth its own constant: 67 orders carry the literal string
``"_No text available_"`` in ``full_text`` rather than an empty string. Code that
does not leave them out counts them as read and finds nothing, which looks like a
clean result instead of a gap.
"""

from __future__ import annotations

import json
from pathlib import Path

# build_corpus.NO_TEXT_STUB — written when a PDF gave up no text at all.
NO_TEXT_STUB = "_No text available_"


def load_corpus(paths: list[Path]) -> list[dict]:
    """Read and concatenate corpus JSON files, in the order given.

    Each file is a bare list. A missing file raises ``SystemExit`` with a
    remediation message rather than a bare traceback.
    """
    records: list[dict] = []
    for path in paths:
        if not path.exists():
            raise SystemExit(f"corpus file not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise SystemExit(f"{path} is not a JSON list of records")
        records.extend(data)
    return records


def with_text(records: list[dict]) -> tuple[list[dict], int]:
    """Split off the orders that actually carry text.

    Returns ``(orders_with_text, skipped_count)``. An order is skipped when
    ``full_text`` is empty or is the no-text placeholder.
    """
    keep, skipped = [], 0
    for r in records:
        text = r.get("full_text") or ""
        if not text or text == NO_TEXT_STUB:
            skipped += 1
            continue
        keep.append(r)
    return keep, skipped
