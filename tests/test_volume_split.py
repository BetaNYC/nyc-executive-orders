"""Phase E segmentation — page roles, index parsing, and index reconciliation.

Every fixture under ``tests/fixtures/pre1974/`` is a REAL page record copied
verbatim from the proven 126-page Impellitteri-Sharkey run, not a hand-written
approximation (engineering-standards §0: mocks encode observed shapes). The pages
were chosen to cover each structural case the segmenter has to get right:

    page 1    library endpaper (furniture)
    page 2    blank verso, skipped by the blank/bleed-through classifier
    page 6    the volume's subject index (SUBJECT | NUMBER | DATE, 37 rows)
    page 12   a one-page instrument, "Memorandum No. 2"
    page 24   "MEMORANDUM NO. 7-A" — the hyphenated suffix
    page 26   a multi-page instrument's opening page, "MEMORANDUM NO. 8"
    page 28   its continuation (no letterhead)
    page 30   its continuation opening with the "-2-" page ornament
    page 48   "MEMORANDUM / EXECUTIVE ORDER NO. 14" — two labels, one element
    page 82   "EXECUTIVE MEMORANDUM NO. 28"
    page 83   the repetition-loop failure: unparseable JSON, zero ink coverage
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyc_executive_orders import volume_split as vs

FIXTURES = Path(__file__).parent / "fixtures" / "pre1974"

# The volume these fixtures come from covers 1950-11-16 .. 1953-11-24.
MIN_YEAR, MAX_YEAR = 1950, 1953


def load_page(number: int) -> dict:
    return json.loads((FIXTURES / f"page_{number:04d}.json").read_text(encoding="utf-8"))


def load_all() -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8"))
            for p in sorted(FIXTURES.glob("page_*.json"))]


@pytest.fixture
def split():
    return vs.split_volume(load_all(), min_year=MIN_YEAR, max_year=MAX_YEAR)


# --------------------------------------------------------------------------- #
# Page classification                                                           #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "page, role",
    [
        (1, vs.ROLE_FURNITURE),      # library endpaper
        (2, vs.ROLE_BLANK),          # blank verso
        (6, vs.ROLE_INDEX),          # subject index
        (12, vs.ROLE_START),         # Memorandum No. 2
        (24, vs.ROLE_START),         # Memorandum No. 7-A
        (26, vs.ROLE_START),         # Memorandum No. 8
        (30, vs.ROLE_CONTINUATION),  # "-2-" ornament
        (48, vs.ROLE_START),         # Executive Order No. 14
        (83, vs.ROLE_FAILED),        # unparseable
    ],
)
def test_page_roles(page, role):
    info = vs.classify_page(load_page(page), min_year=MIN_YEAR, max_year=MAX_YEAR)
    assert info.role == role


def test_blank_page_carries_no_lines():
    info = vs.classify_page(load_page(2), min_year=MIN_YEAR, max_year=MAX_YEAR)
    assert info.lines == []


def test_failed_page_is_flagged_not_silently_empty():
    """Page 83's JSON did not parse. It must never look like a clean blank page."""
    info = vs.classify_page(load_page(83), min_year=MIN_YEAR, max_year=MAX_YEAR)
    assert info.role == vs.ROLE_FAILED
    assert "parse-error" in info.flags
    assert "truncated" in info.flags


# --------------------------------------------------------------------------- #
# Instrument labels                                                             #
# --------------------------------------------------------------------------- #

def test_executive_order_wins_over_memorandum_on_the_same_line():
    """Page 48 reads "MEMORANDUM\\nEXECUTIVE ORDER NO. 14" in ONE element.

    The more specific label has to win: the city called this an executive order,
    and a bare-MEMORANDUM match would erase that.
    """
    info = vs.classify_page(load_page(48), min_year=MIN_YEAR, max_year=MAX_YEAR)
    assert info.series == vs.SERIES_EXECUTIVE_ORDER
    assert info.number == "14"


def test_hyphenated_number_suffix_is_captured():
    """"MEMORANDUM NO. 7-A" must not read as plain "7" and collide with the real 7."""
    info = vs.classify_page(load_page(24), min_year=MIN_YEAR, max_year=MAX_YEAR)
    assert info.number == "7A"


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("7-A", "7A"), ("7 A", "7A"), ("7a", "7A"), ("7A", "7A"),
        ("007", "7"), ("14", "14"), ("", None),
    ],
)
def test_normalize_number(raw, expected):
    assert vs.normalize_number(raw) == expected


# --------------------------------------------------------------------------- #
# Subject index                                                                 #
# --------------------------------------------------------------------------- #

def test_index_parses_distinct_numbers_with_dates():
    entries = vs.parse_index([load_page(6)], MIN_YEAR, MAX_YEAR)
    # The index is alphabetical by subject and repeats numbers across subjects,
    # so the parse must dedupe to distinct instruments.
    assert entries["1"].date == "1950-11-16"
    assert entries["14"].date == "1951-12-12"
    assert entries["7A"].date == "1951-07-16"
    assert "Conflict of interest" in entries["1"].subjects


def test_index_pages_are_not_emitted_as_documents(split):
    assert 6 in split.index_pages
    assert all(6 not in doc.pages for doc in split.documents)


def test_furniture_and_blank_pages_are_not_documents(split):
    assert split.furniture_pages == [1]
    assert split.blank_pages == [2]
    for doc in split.documents:
        assert 1 not in doc.pages and 2 not in doc.pages


# --------------------------------------------------------------------------- #
# Grouping                                                                      #
# --------------------------------------------------------------------------- #

def test_continuation_pages_attach_to_the_open_instrument(split):
    doc = next(d for d in split.documents if d.number == "8")
    assert doc.pages == [26, 28, 30]


def test_failed_page_attaches_to_its_instrument(split):
    """Page 83 belongs to memorandum 28 and must carry its failure onto it."""
    doc = next(d for d in split.documents if d.number == "28")
    assert 83 in doc.pages
    assert any("parse-error" in f for f in doc.flags)


def test_dates_come_off_the_page(split):
    dates = {d.number: d.date_on_page for d in split.documents}
    assert dates["2"] == "1950-12-14"
    assert dates["14"] == "1951-12-12"


def test_ordinal_date_is_parsed(split):
    """Page 24 is dated "July 16th, 1951." — the era's ordinal typing style."""
    doc = next(d for d in split.documents if d.number == "7A")
    assert doc.date_on_page == "1951-07-16"


def test_signer_is_captured(split):
    doc = next(d for d in split.documents if d.number == "2")
    assert doc.signed_by == "VINCENT R. IMPELLITTERI"


# --------------------------------------------------------------------------- #
# Series resolution                                                             #
# --------------------------------------------------------------------------- #

def test_volume_series_comes_from_the_index_title(split):
    """One numbering sequence gets ONE series.

    This volume's instruments are printed variously as "Memorandum No. 2",
    "EXECUTIVE ORDER NO. 14" and "EXECUTIVE MEMORANDUM NO. 28" while running a
    single 1..39 sequence. Its own index is headed "INDEX TO THE EXECUTIVE
    ORDERS OF THE MAYOR", which is what settles it.
    """
    assert split.series == vs.SERIES_EXECUTIVE_ORDER
    assert {d.series for d in split.documents} == {vs.SERIES_EXECUTIVE_ORDER}


def test_printed_index_title_outranks_the_catalogue_description():
    """This volume's catalogue record says memoranda; its own index says orders.

    The artifact's own printed statement wins over the catalogue record about
    it — which is why passing the real description does not move this volume.
    """
    description = (
        "A collection of Executive Memoranda issued by Mayor Vincent R. "
        "Impellitteri and Acting Mayor Joseph T. Sharkey between November 16, "
        "1950 and November 24, 1953, with a subject index."
    )
    assert vs.series_from_description(description) == vs.SERIES_EXECUTIVE_MEMORANDUM
    split = vs.split_volume(load_all(), min_year=MIN_YEAR, max_year=MAX_YEAR,
                            description=description)
    assert split.series == vs.SERIES_EXECUTIVE_ORDER


def test_catalogue_description_outranks_a_thin_majority_vote():
    """A one-observation majority must not name a whole volume.

    The 1969-01-16..1971-12-27 Lindsay memoranda yielded exactly ONE labelled
    document in 174 pages, and that one stray "EXECUTIVE ORDER" heading named
    the volume EO against a catalogue record that says Executive Memoranda.
    """
    one_stray_label = [vs.PageInfo(page=1, role=vs.ROLE_START,
                                   series=vs.SERIES_EXECUTIVE_ORDER)]
    series, flags = vs.resolve_series(one_stray_label, [])
    assert series == vs.SERIES_EXECUTIVE_ORDER      # what it used to do

    series, flags = vs.resolve_series(one_stray_label, [],
                                      vs.SERIES_EXECUTIVE_MEMORANDUM)
    assert series == vs.SERIES_EXECUTIVE_MEMORANDUM
    assert any(f.startswith("series-from-catalogue") for f in flags)


def test_catalogue_description_agreeing_with_the_pages_raises_no_flag():
    pages = [vs.PageInfo(page=1, role=vs.ROLE_START, series=vs.SERIES_EXECUTIVE_ORDER)]
    series, flags = vs.resolve_series(pages, [], vs.SERIES_EXECUTIVE_ORDER)
    assert series == vs.SERIES_EXECUTIVE_ORDER
    assert flags == []


def test_series_from_description_reads_the_catalogue_verbatim():
    assert vs.series_from_description(
        "A collection of Administrative Memoranda issued by Mayor John V. Lindsay"
    ) == vs.SERIES_ADMINISTRATIVE_MEMORANDUM
    assert vs.series_from_description(
        "A collection of Executive Orders issued by Mayor Robert F. Wagner"
    ) == vs.SERIES_EXECUTIVE_ORDER
    assert vs.series_from_description("A scrapbook of press clippings") is None
    assert vs.series_from_description(None) is None


def test_printed_label_is_preserved_verbatim(split):
    """The series is normalized, but what the page actually said is not lost."""
    labels = {d.number: d.printed_label for d in split.documents}
    assert labels["14"] == "EXECUTIVE ORDER NO. 14"
    assert labels["28"] == "EXECUTIVE MEMORANDUM NO. 28"
    assert labels["7A"] == "MEMORANDUM NO. 7-A"


# --------------------------------------------------------------------------- #
# Reconciliation                                                                #
# --------------------------------------------------------------------------- #

def test_reconciliation_reports_the_index_gap(split):
    """These fixtures are 11 pages of a 126-page volume, so most of the index's
    instruments are legitimately absent. The report must SAY so rather than
    quietly pass.

    29 expected, not the volume's full 39: the index runs across pages 6 and 8,
    and only page 6 is in this fixture set. For the same reason memorandum 8 —
    whose index row sits on page 8 — reads as "unindexed" here, which is exactly
    the signal that would flag a genuinely unlisted instrument in a full run.
    """
    recon = split.reconciliation()
    assert recon["n_expected"] == 29
    assert recon["unindexed"] == ["8"]        # found, but its index row is absent
    assert "3" in recon["missing"]            # a real instrument, not in this slice


def test_date_disagreement_keeps_the_instrument_and_flags_it(split):
    """Memorandum 28's own face reads January 5, 1953; the index says February 5.

    The instrument wins — the index is a hand-compiled finding aid — but the
    disagreement is recorded rather than smoothed over.
    """
    doc = next(d for d in split.documents if d.number == "28")
    assert doc.date_on_page == "1953-01-05"
    assert any("date-mismatch-with-index" in f for f in doc.flags)


def test_no_duplicate_numbers_after_reconciliation(split):
    numbers = [d.number for d in split.documents if d.number]
    assert len(numbers) == len(set(numbers))


# --------------------------------------------------------------------------- #
# Body rendering                                                                #
# --------------------------------------------------------------------------- #

def test_picture_placeholders_are_dropped_from_the_body(split):
    """Seals and signatures are Pictures; bbox coordinates are not order text."""
    for doc in split.documents:
        assert "[Picture" not in doc.body


def test_doubled_heading_marks_are_collapsed():
    element = {"category": "Section-header", "text": "## PRINCIPLES"}
    assert vs.element_text(element) == "## PRINCIPLES"


def test_title_element_gets_one_hash():
    element = {"category": "Title", "text": "CITY OF NEW YORK"}
    assert vs.element_text(element) == "# CITY OF NEW YORK"


def test_split_is_deterministic():
    """Same input, same output — the whole stage-2 contract depends on it."""
    a = vs.split_volume(load_all(), min_year=MIN_YEAR, max_year=MAX_YEAR)
    b = vs.split_volume(load_all(), min_year=MIN_YEAR, max_year=MAX_YEAR)
    assert [(d.number, d.pages, d.body) for d in a.documents] == \
           [(d.number, d.pages, d.body) for d in b.documents]
