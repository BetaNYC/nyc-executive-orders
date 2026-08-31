"""Tidy up an agency name so two spellings of it can be compared.

Ported from ``src/nyc_executive_orders/supersede.py::_norm_entity`` rather than
imported, because this package is standalone. Keep the two in step — if they
drift, the two halves of the project stop agreeing on which names match.
"""

from __future__ import annotations

import re
import unicodedata

_LEADING_ARTICLE_RE = re.compile(r"^\s*(the|a|an)\s+", re.IGNORECASE)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")

# A trailing possessive is a whole token, never a set of characters. Stripping with
# str.rstrip("'’s") eats a real trailing "s" as well, which turns "Department of
# Buildings" into "Department of Building" — measured, and the reason this is its
# own function with its own test.
_POSSESSIVE_RE = re.compile(r"[’']s$")


def normalize_name(name: str) -> str:
    """Reduce a name to a comparison key: one case, no punctuation, single spaces.

    Drops accents, lowercases, removes a leading "the"/"a"/"an" so "the Office of
    X" compares equal to "Office of X", and turns every other punctuation mark into
    a space. The result is a lookup key only — it is never written back into a
    record.
    """
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = _LEADING_ARTICLE_RE.sub("", s)
    s = _NON_ALNUM_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def strip_possessive(name: str) -> str:
    """Remove a trailing ``'s`` or ``’s``.

    ``"Department of Buildings"`` comes back unchanged;
    ``"Department of Correction’s"`` becomes ``"Department of Correction"``.
    """
    return _POSSESSIVE_RE.sub("", name)


def collapse_spaces(text: str) -> str:
    """Turn every run of whitespace, newlines included, into one space.

    For display and grouping only. The result no longer lines up with the order
    text character for character, so a recorded position must always come from the
    regex match, never from the length of this result.
    """
    return " ".join(text.split())
