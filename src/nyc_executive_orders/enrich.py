"""Metadata derivation — mayor/administration from signing year, plus Phase-C
placeholder fields.

Year -> mayor is exact for NYC: mayoral terms start January 1, so an order's
signing year pins its administration with no ambiguity. The term table below is
verified public record (7 mayors, 1974-2025). It is the ONLY place a mayor name
is hardcoded, and it is structured so a new term is a one-line append.

Deliberately NOT derived here (Phase C, not this build):
  * ``supersedes`` / ``superseded_by`` — citation-graph extraction from body
    text. Emitted as empty lists with a TODO marker; no body parsing attempted.
  * ``in_effect`` — depends on the supersession/revocation graph above. Emitted
    as ``None``.
  * ``establishes_entity`` — entity extraction. Emitted as ``None``.
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple


class MayoralTerm(NamedTuple):
    start_year: int  # inclusive
    end_year: int    # inclusive
    mayor: str


class DatedTerm(NamedTuple):
    """A term with full-date bounds, for eras where year alone is ambiguous."""

    start: str  # ISO 8601, inclusive
    end: str    # ISO 8601, inclusive
    mayor: str


# Verified public record. NYC mayoral terms begin Jan 1, so year alone resolves
# the mayor. This is a self-contained, minimal lookup for THIS pipeline's
# who-signed-what need only — it is deliberately NOT the org's canonical
# elected-officials source (that lives in nyc-boundaries + the Electeds CRM and
# is being redesigned; see the BetaNYC task on unified electeds documentation).
# A term changes ~once every 4-12 years; add a future term as a ONE-line append.
MAYORAL_TERMS: tuple[MayoralTerm, ...] = (
    MayoralTerm(1974, 1977, "Beame"),
    MayoralTerm(1978, 1989, "Koch"),
    MayoralTerm(1990, 1993, "Dinkins"),
    MayoralTerm(1994, 2001, "Giuliani"),
    MayoralTerm(2002, 2013, "Bloomberg"),
    MayoralTerm(2014, 2021, "de Blasio"),
    MayoralTerm(2022, 2025, "Adams"),
    MayoralTerm(2026, 2029, "Mamdani"),  # took office 2026-01-01
)

# Note surfaced when the administration can't be resolved because the signing
# year is past the last term on record (2030+). Below the 1974 corpus floor it
# also applies. Within the table (1974-2029) the mayor always resolves.
ADMIN_NOTE_PENDING = "signing year past the last mayoral term on record"

# --------------------------------------------------------------------------- #
# Phase E — pre-1974 administrations (1946-1973)                                #
# --------------------------------------------------------------------------- #

# The year-alone assumption above ("NYC mayoral terms begin Jan 1, so year alone
# resolves the mayor") is exactly true from 1974 on, and FALSE before it:
# William O'Dwyer resigned on 1950-08-31 to become Ambassador to Mexico, and
# Vincent R. Impellitteri — then President of the City Council — took over as
# Acting Mayor the next day, winning the November special election and being
# sworn in as Mayor on 1950-11-14. So 1950 has two administrations and a
# year-granular lookup cannot separate them.
#
# Hence a parallel DATE-granular table for the pre-1974 era. `mayor_for_year`
# above is deliberately left alone: it still returns None below 1974 (asserted by
# tests/test_enrich.py), and every one of the 2,291 committed 1974+ records keeps
# resolving through exactly the code path it always did.
#
# Verified public record. Impellitteri's span is dated from the start of his
# acting mayoralty, since the office was continuously his from that day; whether a
# given order was signed by him as Acting or as elected Mayor — or by an Acting
# Mayor standing in for him, e.g. Joseph T. Sharkey — is a property of the
# SIGNATURE, and is carried per record as `signed_by` in the Phase E provenance
# sidecar rather than confused with the administration.
PRE1974_TERMS: tuple[DatedTerm, ...] = (
    DatedTerm("1946-01-01", "1950-08-31", "O'Dwyer"),
    DatedTerm("1950-09-01", "1953-12-31", "Impellitteri"),
    DatedTerm("1954-01-01", "1965-12-31", "Wagner"),
    DatedTerm("1966-01-01", "1973-12-31", "Lindsay"),
)

# First year the modern year-granular table covers. Derived, not hardcoded, so
# the two tables cannot drift apart if a term is ever prepended above.
_MODERN_FLOOR_YEAR = MAYORAL_TERMS[0].start_year

# Earliest year any bound volume reaches (the O'Dwyer compilation opens
# 1946-01-07). Below this there is no source material in this project at all.
PRE1974_FLOOR_YEAR = int(PRE1974_TERMS[0].start[:4])

ADMIN_NOTE_BEFORE_RECORD = "signing year precedes the earliest volume on record"
ADMIN_NOTE_AMBIGUOUS_YEAR = (
    "signing year spans two administrations and the order carries no usable "
    "signing date; administration not inferred"
)


def mayor_for_date(iso_date: str) -> str | None:
    """Return the mayor holding office on an ISO 8601 date, or None if outside.

    Pre-1974 only. Covers the 1950 handover that :func:`mayor_for_year` cannot.
    A malformed date returns None rather than raising — a bad OCR'd date should
    leave the field empty and flagged, never crash a corpus build.
    """
    try:
        target = date.fromisoformat(iso_date)
    except (TypeError, ValueError):
        return None
    for term in PRE1974_TERMS:
        if date.fromisoformat(term.start) <= target <= date.fromisoformat(term.end):
            return term.mayor
    return None


def pre1974_administration(
    year: int, date_signed: str | None
) -> tuple[str | None, str | None]:
    """Resolve ``(mayor, admin_note)`` for a pre-1974 order.

    Prefers the exact signing date. Without one, falls back to the year — which
    is decisive for every pre-1974 year EXCEPT 1950, the only year two
    administrations share. An ambiguous year resolves to None with a note rather
    than a coin-flip guess, per the project's "an empty field is correct; a guess
    is a bug" rule.
    """
    if date_signed:
        mayor = mayor_for_date(date_signed)
        if mayor is not None:
            return mayor, None

    if year < PRE1974_FLOOR_YEAR:
        return None, ADMIN_NOTE_BEFORE_RECORD

    overlapping = {
        term.mayor
        for term in PRE1974_TERMS
        if int(term.start[:4]) <= year <= int(term.end[:4])
    }
    if len(overlapping) == 1:
        return overlapping.pop(), None
    if not overlapping:
        return None, ADMIN_NOTE_BEFORE_RECORD
    return None, ADMIN_NOTE_AMBIGUOUS_YEAR

# Phase C markers — carried on every record so the fields exist now and the
# graph work fills them later without a schema change.
SUPERSEDES_TODO = "TODO(phase-c): citation-graph extraction not yet run"


def mayor_for_year(year: int) -> str | None:
    """Return the mayor for a signing year, or None if past the confirmed table."""
    for term in MAYORAL_TERMS:
        if term.start_year <= year <= term.end_year:
            return term.mayor
    return None


def administration_fields(year: int) -> tuple[str | None, str | None]:
    """Return ``(administration, admin_note)`` for a signing year.

    In-table years resolve to the mayor's name with no note. Years past the last
    confirmed term (2026+) return ``(None, ADMIN_NOTE_PENDING)`` — never a
    hardcoded guess. The administration label tracks the mayor name (an
    administration is named for its mayor); they are emitted as distinct
    frontmatter fields for consumer convenience.
    """
    mayor = mayor_for_year(year)
    if mayor is None:
        return None, ADMIN_NOTE_PENDING
    return mayor, None


def enrich_record(record: dict) -> dict:
    """Derive the metadata fields the corpus frontmatter needs from an index row.

    Returns a NEW dict of only the derived fields; the caller merges it with the
    index row. Pure function of ``record["year"]`` — plus, for pre-1974 rows only,
    ``record["date_signed"]`` — and the Phase-C placeholders.

    The 1974+ path is untouched by Phase E: same lookup, same values, so every
    committed record re-emits byte-identically.
    """
    year = int(record["year"])
    if year < _MODERN_FLOOR_YEAR:
        # Phase E (1946-1973). Needs the full date, not just the year — see
        # PRE1974_TERMS on the 1950 O'Dwyer/Impellitteri handover.
        mayor, admin_note = pre1974_administration(year, record.get("date_signed"))
        administration = mayor
    else:
        mayor = mayor_for_year(year)
        administration, admin_note = administration_fields(year)
    return {
        "mayor": mayor,
        "administration": administration,
        "admin_note": admin_note,
        # Phase C — present but intentionally empty/null this build. The TODO
        # marker lives in code (SUPERSEDES_TODO) rather than the frontmatter, so
        # the emitted field set stays exactly the locked corpus schema.
        "supersedes": [],
        "superseded_by": [],
        "establishes_entity": None,
        "in_effect": None,
    }
