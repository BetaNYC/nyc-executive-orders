"""Metadata derivation — year->mayor incl. boundaries and the 2026 null case."""

from __future__ import annotations

import pytest

from nyc_executive_orders.enrich import (
    ADMIN_NOTE_AMBIGUOUS_YEAR,
    ADMIN_NOTE_BEFORE_RECORD,
    ADMIN_NOTE_PENDING,
    administration_fields,
    enrich_record,
    mayor_for_date,
    mayor_for_year,
    pre1974_administration,
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
        (2025, "Adams"),      # end boundary of Adams
        (2026, "Mamdani"),    # start boundary of Mamdani (took office 2026-01-01)
        (2029, "Mamdani"),    # end boundary of Mamdani (last term on record)
    ],
)
def test_mayor_for_year_boundaries(year, mayor):
    assert mayor_for_year(year) == mayor


def test_2026_is_mamdani():
    assert mayor_for_year(2026) == "Mamdani"
    administration, note = administration_fields(2026)
    assert administration == "Mamdani"
    assert note is None


def test_past_last_term_is_null():
    # Past the last term on record (2030+): null with the pending note.
    assert mayor_for_year(2030) is None
    administration, note = administration_fields(2030)
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


def test_enrich_record_2026_is_mamdani():
    out = enrich_record({"year": 2026})
    assert out["mayor"] == "Mamdani"
    assert out["administration"] == "Mamdani"
    assert out["admin_note"] is None


# --------------------------------------------------------------------------- #
# Phase E — pre-1974, where year alone is NOT enough                            #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "iso_date, mayor",
    [
        ("1946-01-07", "O'Dwyer"),       # first day of the earliest volume
        ("1950-08-31", "O'Dwyer"),       # his last day in office
        ("1950-09-01", "Impellitteri"),  # Acting Mayor from the very next day
        ("1950-11-14", "Impellitteri"),  # sworn in after the special election
        ("1953-12-31", "Impellitteri"),  # end of his term
        ("1954-01-01", "Wagner"),
        ("1965-12-31", "Wagner"),
        ("1966-01-01", "Lindsay"),
        ("1973-11-12", "Lindsay"),       # last day covered by any volume
    ],
)
def test_mayor_for_date_covers_the_1950_handover(iso_date, mayor):
    assert mayor_for_date(iso_date) == mayor


def test_mayor_for_date_rejects_garbage_rather_than_raising():
    """A mangled OCR date must leave the field empty, never crash a build."""
    assert mayor_for_date("not-a-date") is None
    assert mayor_for_date("") is None
    assert mayor_for_date(None) is None


def test_1950_without_a_date_is_not_guessed():
    """1950 has two administrations, so the year alone cannot resolve it.

    "An empty or flagged field is correct; a guess is a bug."
    """
    mayor, note = pre1974_administration(1950, None)
    assert mayor is None
    assert note == ADMIN_NOTE_AMBIGUOUS_YEAR


def test_unambiguous_pre_1974_year_resolves_without_a_date():
    assert pre1974_administration(1951, None) == ("Impellitteri", None)
    assert pre1974_administration(1970, None) == ("Lindsay", None)


def test_pre_1974_enrich_prefers_the_signing_date():
    out = enrich_record({"year": 1950, "date_signed": "1950-11-16"})
    assert out["mayor"] == "Impellitteri"
    assert out["admin_note"] is None
    # Same year, other side of the handover.
    out = enrich_record({"year": 1950, "date_signed": "1950-01-04"})
    assert out["mayor"] == "O'Dwyer"


def test_before_the_earliest_volume_is_null():
    mayor, note = pre1974_administration(1930, None)
    assert mayor is None
    assert note == ADMIN_NOTE_BEFORE_RECORD


def test_mayor_for_year_still_returns_none_pre_1974():
    """The year-granular lookup keeps its old contract exactly.

    Phase E resolves the pre-1974 era through a SEPARATE date-granular table, so
    this function — and every 1974+ record that flows through it — is unchanged.
    """
    assert mayor_for_year(1973) is None
    assert mayor_for_year(1950) is None
