"""Metadata derivation — year->mayor incl. boundaries and the 2026 null case."""

from __future__ import annotations

import pytest

from nyc_executive_orders.enrich import (
    ADMIN_NOTE_PENDING,
    administration_fields,
    enrich_record,
    mayor_for_year,
)


@pytest.mark.parametrize(
    "year, mayor",
    [
        (1974, "Beame"),
        (1977, "Beame"),      # end boundary of Beame
        (1978, "Koch"),       # start boundary of Koch
        (1989, "Koch"),
        (1990, "Dinkins"),
        (2001, "Giuliani"),   # end boundary of Giuliani
        (2002, "Bloomberg"),  # start boundary of Bloomberg
        (2013, "Bloomberg"),
        (2014, "de Blasio"),
        (2021, "de Blasio"),  # end boundary of de Blasio
        (2022, "Adams"),      # start boundary of Adams
        (2025, "Adams"),      # end boundary of Adams (last confirmed term)
    ],
)
def test_mayor_for_year_boundaries(year, mayor):
    assert mayor_for_year(year) == mayor


def test_2026_is_null_pending_confirmation():
    assert mayor_for_year(2026) is None
    administration, note = administration_fields(2026)
    assert administration is None
    assert note == ADMIN_NOTE_PENDING


def test_pre_1974_is_null():
    # Below the corpus floor: no term on record, so null (with the pending note).
    assert mayor_for_year(1973) is None


def test_enrich_record_shape_current_era():
    rec = {"year": 2024}
    out = enrich_record(rec)
    assert out["mayor"] == "Adams"
    assert out["administration"] == "Adams"
    assert out["admin_note"] is None
    # Phase C fields present but empty/null.
    assert out["supersedes"] == []
    assert out["superseded_by"] == []
    assert out["establishes_entity"] is None
    assert out["in_effect"] is None


def test_enrich_record_2026_carries_note():
    out = enrich_record({"year": 2026})
    assert out["mayor"] is None
    assert out["administration"] is None
    assert out["admin_note"] == ADMIN_NOTE_PENDING
