"""Pass four, one rule at a time, against made-up orders.

Nothing here reads the corpus or the registry. The numbers over the real corpus are
pinned in ``test_real_corpus.py``.
"""

from __future__ import annotations

from lineage import citations
from lineage.mentions import (
    REASON_IMPLAUSIBLE_YEAR,
    REASON_NOT_IN_CORPUS,
    REASON_NO_YEAR,
    REASON_SUFFIX_VARIANT,
    SOURCE_BODY_CITATION,
    SOURCE_HEADER_XREF,
)


def order(eo_id: str, text: str = "", header: str = "") -> dict:
    return {"eo_id": eo_id, "full_text": text, "dropped_header": header}


# --------------------------------------------------------------------------- #
# The id minter                                                                 #
# --------------------------------------------------------------------------- #

def test_a_regular_order_number_is_padded_to_three_digits():
    assert citations.mint_eo_id(2024, 42, False) == "2024-EO-042"
    assert citations.mint_eo_id(2024, 8, False) == "2024-EO-008"


def test_a_long_regular_number_is_not_cut_short():
    assert citations.mint_eo_id(2024, 1234, False) == "2024-EO-1234"


def test_an_emergency_number_keeps_its_label_exactly():
    """Two administrations number these differently and both must survive."""
    assert citations.mint_eo_id(2024, 718, True) == "2024-EEO-718"
    assert citations.mint_eo_id(2026, "1.37", True) == "2026-EEO-1.37"


def test_two_dotted_emergency_orders_signed_the_same_day_stay_apart():
    """Padding these would collapse two different orders into one id."""
    assert (citations.mint_eo_id(2026, "1.37", True)
            != citations.mint_eo_id(2026, "2.37", True))


def test_a_missing_number_still_mints_an_identifiable_id():
    assert citations.mint_eo_id(1999, None, False) == "1999-EO-UNK"


def test_the_stem_of_an_id_drops_the_pre1974_suffix():
    # Both suffix namespaces reduce to the same stem: the label PRINTED on the
    # page, and the page anchor the build mints when two records claim one id.
    assert citations.id_stem("1966-EO-019B") == "1966-EO-019"
    assert citations.id_stem("1966-EO-019-p007") == "1966-EO-019"
    assert citations.id_stem("1966-EO-019-39") == "1966-EO-019"
    assert citations.id_stem("2024-EO-042") == "2024-EO-042"
    assert citations.id_stem("1969-EM-D0101") is None


# --------------------------------------------------------------------------- #
# Body citations                                                                #
# --------------------------------------------------------------------------- #

def test_a_dated_citation_becomes_an_edge():
    edges, dangles, _ = citations.extract([
        order("2022-EO-021",
              "Executive Order No. 31, dated May 1, 2018, is hereby revoked."),
        order("2018-EO-031"),
    ])
    assert dangles == []
    assert len(edges) == 1
    assert (edges[0].actor, edges[0].target, edges[0].verb) == (
        "2022-EO-021", "2018-EO-031", "revoked")
    assert edges[0].source == SOURCE_BODY_CITATION


def test_the_year_comes_from_the_citation_not_from_the_order_carrying_it():
    """Per-mayor numbering resets, so the number alone names several orders."""
    edges, _, _ = citations.extract([
        order("2022-EO-021",
              "Executive Order No. 31 of 2018 is hereby revoked."),
        order("2018-EO-031"),
        order("2020-EO-031"),
    ])
    assert [e.target for e in edges] == ["2018-EO-031"]


def test_a_citation_with_no_year_is_never_guessed_at():
    edges, dangles, _ = citations.extract([
        order("2022-EO-021", "Executive Order No. 31 is hereby revoked."),
        order("2018-EO-031"),
    ])
    assert edges == []
    assert dangles[0].reason == REASON_NO_YEAR


def test_an_ocr_garbled_year_never_mints_a_target():
    _, dangles, _ = citations.extract([
        order("2022-EO-021",
              "Executive Order No. 31, dated May 1, 1474, is hereby revoked.")])
    assert dangles[0].reason == REASON_IMPLAUSIBLE_YEAR


def test_an_order_we_do_not_hold_is_recorded_as_a_dangle():
    _, dangles, _ = citations.extract([
        order("2022-EO-021",
              "Executive Order No. 31, dated May 1, 1968, is hereby revoked.")])
    assert dangles[0].target == "1968-EO-031"
    assert dangles[0].reason == REASON_NOT_IN_CORPUS


def test_a_pre1974_number_filed_under_a_suffix_says_so_rather_than_guessing():
    """The volumes filed 50 orders under 1966-EO-019. Picking one is a guess."""
    _, dangles, _ = citations.extract([
        order("1970-EO-010",
              "Executive Order No. 19, dated June 30, 1966, is hereby revoked."),
        order("1966-EO-019-p007"),
        order("1966-EO-019-39"),
    ])
    assert dangles[0].target == "1966-EO-019"
    assert dangles[0].reason == REASON_SUFFIX_VARIANT


def test_a_section_scoped_edit_is_marked_partial():
    edges, _, _ = citations.extract([
        order("2022-EO-021",
              "Section 8 of Executive Order No. 31, dated May 1, 2018, is hereby "
              "amended."),
        order("2018-EO-031"),
    ])
    assert edges[0].partial is True
    assert edges[0].verb == "amended"


def test_an_extension_is_counted_and_skipped():
    """An emergency order expires by law. That is not supersession."""
    edges, _, extensions = citations.extract([
        order("2022-EEO-62",
              "Emergency Executive Order No. 50, dated April 1, 2022, is hereby "
              "extended for five (5) days."),
        order("2022-EEO-50"),
    ])
    assert edges == []
    assert extensions == 1


def test_an_order_citing_itself_makes_no_edge():
    edges, _, _ = citations.extract([
        order("2018-EO-031",
              "Executive Order No. 31, dated May 1, 2018, is hereby revoked.")])
    assert edges == []


def test_the_same_citation_twice_in_one_order_makes_one_edge():
    edges, _, _ = citations.extract([
        order("2022-EO-021",
              "Executive Order No. 31, dated May 1, 2018, is hereby revoked. "
              "Executive Order No. 31, dated May 1, 2018, is hereby revoked."),
        order("2018-EO-031"),
    ])
    assert len(edges) == 1


# --------------------------------------------------------------------------- #
# Header XREF stamps                                                            #
# --------------------------------------------------------------------------- #

def test_an_xref_stamp_reads_the_other_way_round():
    """"X AMENDED BY Y" means Y acted on X."""
    edges, _, _ = citations.extract([
        order("1977-EO-091", header='XREF: AMENDED BY \'EO 18) 1978"'),
        order("1978-EO-018"),
    ])
    assert (edges[0].actor, edges[0].target) == ("1978-EO-018", "1977-EO-091")
    assert edges[0].source == SOURCE_HEADER_XREF


def test_the_same_input_gives_the_same_answer_every_time():
    records = [
        order("2022-EO-021",
              "Executive Order No. 31, dated May 1, 2018, is hereby revoked."),
        order("2018-EO-031"),
    ]
    assert citations.extract(records) == citations.extract(records)
