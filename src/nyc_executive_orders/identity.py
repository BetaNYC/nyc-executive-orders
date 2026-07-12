"""The synthetic `eo_id` scheme (LOCKED cross-link name — project STATUS.md).

Per-mayor numbering resets, so a raw EO number is not unique across
administrations. `eo_id` disambiguates. Phase A scheme, prefixed by the signing
year (which, within the current era, uniquely pins an order together with its
number + series):

    regular    ->  YYYY-EO-NNN     e.g. 2024-EO-042
    emergency  ->  YYYY-EEO-NNN    e.g. 2024-EEO-718

NNN is the EO number, zero-padded to at least 3 digits (longer numbers are not
truncated). When the number can't be parsed from the title, "UNK" is used so the
row is still identifiable and flagged downstream.
"""

from __future__ import annotations


def mint_eo_id(year: int, number: int | None, is_emergency: bool) -> str:
    """Build the synthetic eo_id for one order."""
    series = "EEO" if is_emergency else "EO"
    num = f"{number:03d}" if number is not None else "UNK"
    return f"{year}-{series}-{num}"
