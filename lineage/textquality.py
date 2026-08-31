"""Cheap, dictionary-free check on whether a found name is readable.

Ported from ``src/nyc_executive_orders/clean.py`` (``_english_like`` /
``_word_ratio`` / ``_junk_ratio``) rather than imported, because :mod:`lineage` is
standalone. One deliberate difference: the allowed-character set gains ``+`` and
the en/em dashes, which are ordinary inside an agency name ("Office of LGBTQIA+
Affairs", "H+H") and would otherwise be counted as scanner junk.

The cutoffs are that module's REVIEW tier, not its CLEAN tier: a proposed agency
name only has to be readable enough for a person to judge, and the strict tier
throws away real names from the 1946-1973 volumes, where 285 of 978 orders are
already flagged ``needs-review``.
"""

from __future__ import annotations

import re

_VOWELS = frozenset("aeiouy")

# clean.py REVIEW tier: REVIEW_MIN_WORD_RATIO / REVIEW_MAX_JUNK_RATIO.
MIN_WORD_RATIO = 0.70
MAX_JUNK_RATIO = 0.15

# clean.py's allowed set, plus three characters that are ordinary inside a modern
# agency name and would otherwise count as junk: "+" ("Office of LGBTQIA+ Affairs",
# "H+H") and the en/em dashes.
_ALLOWED_CHARS = frozenset(" \t\n.,;:'\"()-&/$%§#’“”+–—")


def english_like(token: str) -> bool:
    """Does this one word look like English? No dictionary, just letter shape."""
    t = token.lower()
    if not t.isalpha():
        return False
    if len(t) == 1:
        return t in ("a", "i")
    if len(t) > 20:
        return False
    if not any(c in _VOWELS for c in t):
        return False
    # No run of 5+ consonants (real English words basically never have this).
    run = 0
    for c in t:
        if c in _VOWELS:
            run = 0
        else:
            run += 1
            if run >= 5:
                return False
    return True


def word_ratio(text: str) -> float:
    """Fraction of >=2-char alpha tokens that look English. 1.0 if none present."""
    toks = [w for w in re.findall(r"[A-Za-z]+", text) if len(w) >= 2]
    if not toks:
        return 1.0
    return sum(english_like(w) for w in toks) / len(toks)


def junk_ratio(text: str) -> float:
    """Fraction of chars that are neither alnum, space, nor common punctuation."""
    if not text:
        return 0.0
    junk = sum(1 for c in text if not (c.isalnum() or c in _ALLOWED_CHARS))
    return junk / len(text)


def readable(text: str) -> bool:
    """Is this name clean enough to put in front of a person?"""
    return word_ratio(text) >= MIN_WORD_RATIO and junk_ratio(text) <= MAX_JUNK_RATIO
