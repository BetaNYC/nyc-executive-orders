"""The synthetic eo_id scheme."""

from __future__ import annotations

from nyc_executive_orders.identity import mint_eo_id


def test_regular_eo_id():
    assert mint_eo_id(2024, 42, is_emergency=False) == "2024-EO-042"


def test_emergency_eo_id():
    assert mint_eo_id(2024, 718, is_emergency=True) == "2024-EEO-718"


def test_long_number_not_truncated():
    assert mint_eo_id(2024, 1234, is_emergency=True) == "2024-EEO-1234"


def test_unknown_number():
    assert mint_eo_id(2024, None, is_emergency=False) == "2024-EO-UNK"
