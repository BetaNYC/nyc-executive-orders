"""Find where NYC agencies are named in the executive orders.

Standalone on purpose: this package reads ``corpus/*.json`` as data and never
imports :mod:`nyc_executive_orders`. Where it reuses logic from that package
(tidying names, judging OCR quality) the source is named in a comment so the two
stay comparable.

Four passes, deliberately separate:

* **Pass one — known names** (:mod:`lineage.scan`). Every agency name from the
  registry and the extra-agencies file, looked for in every order. Rarely wrong.
  Cannot see an agency that was shut down, because the registry lists only
  agencies that still exist.
* **Pass two — new names** (:mod:`lineage.discover`). Phrases shaped like agency
  names that pass one did not match, sent to a person to review. This is where a
  closed-down body turns up — DoITT, the 1998 Technology Steering Committee — so
  it is the pass the lineage work actually needs.
* **Pass three — what the orders DO to agencies** (:mod:`lineage.reorg`). The
  sentences that link two bodies: established, renamed, abolished, transferred.
  It attaches nothing of its own; every side of a sentence must land on a span
  that pass one or pass two already found, which is what keeps out a person taking
  a post and money moving between accounts. This is the family tree.
* **Pass four — what the orders do to EACH OTHER** (:mod:`lineage.citations`).
  One order revoking, amending or superseding another. Ported from
  :mod:`nyc_executive_orders.supersede`, never imported, and it reads the pre-1974
  volumes that module never sees.

**Rules only, no LLM, no network** (engineering-standards §7), the same discipline
as :mod:`nyc_executive_orders.supersede`. Every find records where it sits in
``full_text``, so any claim can be checked against the order it came from.
"""

from __future__ import annotations

__all__ = ["citations", "discover", "mentions", "namelist", "normalize",
           "records", "reorg", "scan"]
