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
"""

from __future__ import annotations


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
