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

from typing import NamedTuple


class MayoralTerm(NamedTuple):
    start_year: int  # inclusive
    end_year: int    # inclusive
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
    index row. Pure function of ``record["year"]`` plus the Phase-C placeholders.
    """
    year = int(record["year"])
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
