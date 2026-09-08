"""Tell the mayoral letterhead apart from a real reference to the Mayor's office.

Nearly every order opens with the same stationery::

    # CITY OF NEW YORK
    OFFICE OF THE MAYOR
    NEW YORK 7, N. Y.
    January 10, 1955.
    EXECUTIVE ORDER #15

That ``OFFICE OF THE MAYOR`` line is in ``full_text`` by design.
``nyc_executive_orders.clean`` uses it as the ANCHOR it trims the header *to*, so
everything above it goes to ``dropped_header`` and the anchor line itself stays.
Pass one then finds it, and ``Office of the Mayor`` tops the report with 2810 —
a count of stationery, not of government.

So the find is marked, never deleted. ``full_text[start:end] == text`` still holds
for it, pass two still sees its span as already matched, and a reader who wants
the letterhead can still have it.

Two tests, and BOTH must pass:

1. **The line holds nothing but the name.** Not enough on its own: pulling text
   out of a PDF drops a real sentence onto its own line often enough to matter
   (``...is hereby established in the\\nOffice of the Mayor.``).
2. **A masthead line sits within three lines.** The stationery never comes alone —
   the city name, the street address, the ZIP, or the order number is beside it.

Measured over the whole corpus: 2547 finds marked, 263 real body references left
alone, and no wrong mark in a 40-case hand check.
"""

from __future__ import annotations

import re

# How far to look, in lines, on each side of the find for masthead furniture.
# Three covers the widest real masthead measured — city name, office, street,
# ZIP, date, order number — without reaching into the body of the order.
NEIGHBOUR_LINES = 3

# A masthead line is SHORT. The bound is what separates letterhead ("OFFICE OF
# THE MAYOR", 4 tokens) from a real caps title that merely holds a masthead word
# ("ESTABLISHMENT OF THE MAYOR'S MIDTOWN ACTION OFFICE", 8 tokens).
# Copied from nyc_executive_orders.clean.FURNITURE_MAX_TOKENS, which draws the
# same line for the same reason; lineage never imports that package.
MASTHEAD_MAX_TOKENS = 6

# Markdown and OCR marks that may sit before the name on its own line and still
# leave the line empty of words: a heading hash, a quote arrow, a bullet.
LEADING_MARKS = " \t#>*"
# Punctuation that may trail the name on a letterhead line — an OCR rule drawn
# under it, a stray comma, the em dash of "OFFICE OF THE MAYOR —".
TRAILING_MARKS = " \t.,;:!_·—–-"

# A US ZIP, or the "N. Y." of an old address line. Copied from
# nyc_executive_orders.clean._ADDRESS_RE.
_ADDRESS_RE = re.compile(r"\b\d{5}\b|N\.?\s*Y\.?", re.IGNORECASE)

_NON_ALPHA_RE = re.compile(r"[^A-Za-z ]+")

# Token sets that make a short line masthead furniture. Each entry is a rule:
# every token in the first set must be present, and at least one from the second
# (an empty second set means "no further token needed").
_MASTHEAD_RULES: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    # "THE CITY OF NEW YORK", "CITY OF NEW YORK"
    (frozenset({"CITY", "YORK"}), frozenset()),
    # "OFFICE OF THE MAYOR", "ROBERT F. WAGNER, MAYOR", "DEPUTY MAYOR"
    (frozenset({"MAYOR"}), frozenset()),
    # "EXECUTIVE ORDER NO. 15", "EXECUTIVE MEMORANDUM", "EXECUTIVE OFFICE"
    (frozenset({"EXECUTIVE"}), frozenset({"ORDER", "ORDERS", "MEMORANDUM",
                                          "OFFICE"})),
)


def _tokens(line: str) -> list[str]:
    """Upper-case alphabetic tokens of one line. Digits and punctuation drop out."""
    return _NON_ALPHA_RE.sub(" ", line).upper().split()


def is_masthead_line(line: str) -> bool:
    """True if one line is letterhead furniture — a city name, address, or order no."""
    if not line.strip():
        return False
    if _ADDRESS_RE.search(line):
        return True
    tokens = _tokens(line)
    if not tokens or len(tokens) > MASTHEAD_MAX_TOKENS:
        return False
    seen = set(tokens)
    return any(required <= seen and (not one_of or one_of & seen)
               for required, one_of in _MASTHEAD_RULES)


def alone_on_its_lines(text: str, start: int, end: int) -> bool:
    """True if nothing but the find sits on the line(s) it occupies.

    A find wrapped across lines ("OFFICE\\nOF THE\\nMAYOR") is judged from the
    start of its first line to the end of its last, so the wrap costs it nothing.
    """
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    if line_end < 0:
        line_end = len(text)
    return (not text[line_start:start].strip(LEADING_MARKS)
            and not text[end:line_end].strip(TRAILING_MARKS))


def has_masthead_neighbour(text: str, start: int, end: int) -> bool:
    """True if a masthead line sits within :data:`NEIGHBOUR_LINES` of the find.

    The find's OWN lines are skipped — the letterhead line answers for itself
    under :func:`is_masthead_line`, so counting it would make this test say
    nothing.
    """
    lines = text.split("\n")
    first = text.count("\n", 0, start)
    last = text.count("\n", 0, end)
    lo = max(0, first - NEIGHBOUR_LINES)
    hi = min(len(lines), last + NEIGHBOUR_LINES + 1)
    return any(is_masthead_line(lines[i]) for i in range(lo, hi)
               if not first <= i <= last)


def is_letterhead(text: str, start: int, end: int) -> bool:
    """True if the find at ``text[start:end]`` is letterhead, not a reference."""
    return (alone_on_its_lines(text, start, end)
            and has_masthead_neighbour(text, start, end))
