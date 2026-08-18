"""The synthetic `eo_id` scheme (LOCKED cross-link name — project STATUS.md).

Per-mayor numbering resets, so a raw EO number is not unique across
administrations. `eo_id` disambiguates. Phase A scheme, prefixed by the signing
year (which, within the current era, uniquely pins an order together with its
number + series):

    regular    ->  YYYY-EO-NNN     e.g. 2024-EO-042
    emergency  ->  YYYY-EEO-<num>  e.g. 2024-EEO-718, 2026-EEO-1.37

The `<num>` component is the order's literal number *label* as the city prints
it, not merely a trailing integer:

  * Regular EOs are a plain integer sequence, zero-padded to at least 3 digits
    (longer numbers are not truncated): 42 -> "042", 17 -> "017", 8 -> "008".
  * Emergency EOs use two schemes across administrations, and the label is
    preserved *literally* so distinct orders never collide:
      - Adams-era plain integers:   718  -> "718"
      - Mamdani-era dotted `X.YY`:  1.37 -> "1.37", 2.37 -> "2.37"
    Preserving the dotted prefix is the whole point: "1.37" and "2.37" are
    different orders signed the same day and MUST mint different ids.

When the number can't be parsed from the title, "UNK" is used so the row is
still identifiable and flagged downstream.

The minted id is safe as a filesystem name for `pdfs/YYYY/<eo_id>.pdf`: the only
non-alphanumeric characters are '-' and (for dotted emergency labels) '.', both
valid in a POSIX/macOS filename.

Phase E adds a SECOND minter, `mint_pre1974_id`, for the 1946-1973 bound volumes,
which carry instrument series the modern scheme has no room for (numbered
Executive Orders alongside Executive and Administrative Memoranda, and one volume
of unnumbered memoranda). `mint_eo_id` above is deliberately left untouched — its
scheme is LOCKED and 2,291 committed records depend on it.
"""

from __future__ import annotations

import re


def mint_eo_id(year: int, number: int | str | None, is_emergency: bool) -> str:
    """Build the synthetic eo_id for one order.

    `number` is the order's number *label*: an int (regular EOs, Adams-era
    emergency EOs) or a string carrying the literal identifier including any
    dotted prefix (Mamdani-era emergency EOs, e.g. "1.37"). `None` mints "UNK".
    """
    series = "EEO" if is_emergency else "EO"
    if number is None:
        num = "UNK"
    else:
        label = str(number).strip()
        if is_emergency:
            # Emergency numbering is a mixed integer/dotted scheme; preserve the
            # label literally so dotted pairs (1.37 vs 2.37) stay distinct and
            # ids line up with the city's own short ids (eeo-718, eeo-1.37).
            num = label
        elif label.isdigit():
            # Regular EOs are a plain integer sequence — zero-pad for sortability.
            num = f"{int(label):03d}"
        else:
            # Defensive: a non-integer regular label is unexpected; keep it
            # verbatim rather than crash, so the row stays identifiable.
            num = label
    return f"{year}-{series}-{num}"


# --------------------------------------------------------------------------- #
# Phase E — pre-1974 bound volumes (1946-1973)                                  #
# --------------------------------------------------------------------------- #

# The instrument series found in the 14 bound volumes, taken from the label
# PRINTED ON THE PAGE rather than from the volume's filename. That distinction is
# load-bearing: the volume filed by GPP as
# `1950-11-16_1953-11-24_Impellitteri-Sharkey_Memoranda.pdf` contains pages headed
# both "MEMORANDUM No. 1" and "MEMORANDUM / EXECUTIVE ORDER NO. 14", and its own
# index is titled "INDEX TO THE EXECUTIVE ORDERS OF THE MAYOR". Trusting the
# filename would mislabel roughly half of it.
SERIES_EXECUTIVE_ORDER = "EO"
SERIES_EXECUTIVE_MEMORANDUM = "EM"
SERIES_ADMINISTRATIVE_MEMORANDUM = "AM"
PRE1974_SERIES = (
    SERIES_EXECUTIVE_ORDER,
    SERIES_EXECUTIVE_MEMORANDUM,
    SERIES_ADMINISTRATIVE_MEMORANDUM,
)

# There is no emergency series before 1974 — `is_emergency` is False on every
# pre-1974 record, so "EEO" never appears here.

# Suffixes appended when two unnumbered instruments share a signing date. 'a' is
# implicit (the first gets no suffix), so index 1 -> 'b', 2 -> 'c', ...
_COLLISION_SUFFIXES = "abcdefghijklmnopqrstuvwxyz"


def mint_pre1974_id(
    year: int,
    number: int | str | None,
    series: str,
    *,
    month_day: str | None = None,
    occurrence: int = 0,
) -> str:
    """Build the id for one pre-1974 instrument.

    Numbered instruments (the overwhelming majority — both the Executive Order
    volumes and most memoranda are numbered on the page) mint the same shape as
    the modern scheme, with the series swapped in::

        1951-EO-014     Executive Order No. 14, signed 1951
        1951-EM-001     Memorandum No. 1
        1972-AM-007     Administrative Memorandum No. 7

    Unnumbered instruments (the 10-page
    `1966-01-01_1968-05-13_Lindsay_Memoranda-Unnumbered.pdf` volume) have no
    number to carry, so the id is derived from the signing date instead::

        1967-EM-D0104   the memorandum signed January 4, 1967
        1967-EM-D0104b  a second one signed the same day

    Date-derived rather than sequence-derived on purpose: a sequence number would
    shift every id in the volume whenever segmentation changed by one document,
    silently breaking cross-links. The date is a property of the instrument, so
    the id is stable as long as the date reads the same.

    ``occurrence`` is the 0-based index among instruments sharing that date;
    ``month_day`` is "MMDD". With neither a number nor a date, the id falls back
    to "UNK" (the same marker :func:`mint_eo_id` uses), still disambiguated by
    ``occurrence`` so two unknowns cannot collide.
    """
    if series not in PRE1974_SERIES:
        raise ValueError(
            f"unknown pre-1974 series {series!r}; expected one of {PRE1974_SERIES}"
        )

    if number is not None:
        label = str(number).strip()
        # Zero-pad a plain integer for sortability, exactly as the regular EO
        # branch of mint_eo_id does. A suffixed label ("7A") pads its digits and
        # keeps the letter, so 7 -> 007, 7A -> 007A, and the two sort adjacent.
        m = re.match(r"^(\d+)([A-Za-z]*)$", label)
        num = f"{int(m.group(1)):03d}{m.group(2).upper()}" if m else label
        return f"{year}-{series}-{num}{_suffix(occurrence)}"

    stem = f"D{month_day}" if month_day else "UNK"
    return f"{year}-{series}-{stem}{_suffix(occurrence)}"


def _suffix(occurrence: int) -> str:
    """'' for the first instrument on a date, then 'b', 'c', ... for the rest.

    Past 'z' the raw index is appended ("-27") rather than wrapping, so ids stay
    unique no matter how implausible the input.
    """
    if occurrence <= 0:
        return ""
    if occurrence < len(_COLLISION_SUFFIXES):
        return _COLLISION_SUFFIXES[occurrence]
    return f"-{occurrence}"
