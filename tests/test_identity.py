"""The synthetic eo_id scheme."""

from __future__ import annotations

from nyc_executive_orders.enumerate import parse_is_emergency, parse_number
from nyc_executive_orders.identity import mint_eo_id


def test_regular_eo_id():
    assert mint_eo_id(2024, 42, is_emergency=False) == "2024-EO-042"


def test_emergency_eo_id():
    assert mint_eo_id(2024, 718, is_emergency=True) == "2024-EEO-718"


def test_long_number_not_truncated():
    assert mint_eo_id(2024, 1234, is_emergency=True) == "2024-EEO-1234"


def test_unknown_number():
    assert mint_eo_id(2024, None, is_emergency=False) == "2024-EO-UNK"


def test_emergency_dotted_label_preserved():
    assert mint_eo_id(2026, "1.37", is_emergency=True) == "2026-EEO-1.37"
    assert mint_eo_id(2026, "2.37", is_emergency=True) == "2026-EEO-2.37"


def _id_from_title(title: str, year: int = 2026) -> str:
    """Mirror the harvest's title -> eo_id path (parse then mint)."""
    return mint_eo_id(year, parse_number(title), parse_is_emergency(title))


# Ground-truth 2026 title set (the supervised live dry-run). Every distinct
# title here MUST mint a distinct eo_id — this is the regression that the old
# "last run of digits" parser failed (it collapsed every X.YY onto YY).
_GROUND_TRUTH_2026 = [
    "Emergency Executive Order No. 1.37",
    "Emergency Executive Order No. 2.37",  # 1./2. pair, same day
    "Emergency Executive Order No. 1.16 ",  # trailing whitespace
    "Emergency Executive Order No. 3",
    "Emergency Executive Order No. 1.3",  # triple with "No. 3"
    "Emergency Executive Order No. 2.3",  # triple with "No. 3"
    "Emergency Executive Order 1.2",
    "Emergency Executive Order 2.2",  # no "No."
    "Emergency Executive Order 2.1",
    "Emergency Executive Order 1",  # early standalone integers
    "Emergency Executive Order 2",
    "Executive Order No. 17",
    "Executive Order 12",
    "Executive Order 08",
]


def test_2026_title_set_has_no_eo_id_collisions():
    ids = [_id_from_title(t) for t in _GROUND_TRUTH_2026]
    assert len(set(ids)) == len(ids), {
        t: i for t, i in zip(_GROUND_TRUTH_2026, ids)
    }


def test_dotted_pair_is_distinct():
    # The 1./2. pair the old parser collided onto 2026-EEO-037.
    assert _id_from_title("Emergency Executive Order No. 1.37") == "2026-EEO-1.37"
    assert _id_from_title("Emergency Executive Order No. 2.37") == "2026-EEO-2.37"


def test_triple_is_distinct():
    # "No. 3" + "No. 1.3" + "No. 2.3" — the old parser collided all onto -003.
    assert _id_from_title("Emergency Executive Order No. 3") == "2026-EEO-3"
    assert _id_from_title("Emergency Executive Order No. 1.3") == "2026-EEO-1.3"
    assert _id_from_title("Emergency Executive Order No. 2.3") == "2026-EEO-2.3"


def test_adams_plain_integer_emergency_still_parses():
    # The other administration's scheme: plain integer, no dot.
    assert _id_from_title("Emergency Executive Order 718") == "2026-EEO-718"


def test_regular_eo_still_zero_padded():
    assert _id_from_title("Executive Order No. 17") == "2026-EO-017"
    assert _id_from_title("Executive Order 08") == "2026-EO-008"
