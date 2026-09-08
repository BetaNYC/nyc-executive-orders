"""Offline tests for :mod:`lineage.discover` (the new-name pass).

Every fixture mirrors a shape measured in the corpus, and each capture rule here
exists because a naive version of it produced a wrong result on real text
(engineering-standards §0). The counts quoted in the comments were measured on
2026-08-26 over 3,202 scannable records.
"""

from __future__ import annotations

import pytest
from lineage import discover
from lineage.mentions import (
    WHY_ALREADY_MATCHED,
    WHY_JUST_A_NOUN,
    WHY_UNREADABLE,
    Mention,
)


def _find(text, known=(), spans=(), eo_id="TEST-EO-001"):
    return discover.find_in_record(eo_id, text, frozenset(known), list(spans))


def _names(text, **kw):
    found, _ = _find(text, **kw)
    return [p.name for p in found]


def _whys(text, **kw):
    _, dropped = _find(text, **kw)
    return [reason for _, reason in dropped]


# --------------------------------------------------------------------------- #
# The offset contract                                                           #
# --------------------------------------------------------------------------- #


def test_positions_point_into_full_text_exactly_after_trimming():
    text = "adopted by the Board of Estimate on June 5, 1975, and"
    (p,) = _find(text)[0]
    assert text[p.start:p.end] == p.text
    assert p.name == "Board of Estimate"


def test_text_is_what_the_order_says_while_name_is_tidied():
    """PDF extraction wraps names; the span must stay exact, the group key must not."""
    text = "the Financial Information Services\nAgency shall"
    (p,) = _find(text)[0]
    assert p.text == "Financial Information Services\nAgency"
    assert text[p.start:p.end] == p.text
    assert p.name == "Financial Information Services Agency"


# --------------------------------------------------------------------------- #
# Connectors inside the name                                                    #
# --------------------------------------------------------------------------- #


def test_a_joining_word_does_not_cut_the_name_short():
    """Without lowercase connectors this captures only "Limousine Commission"."""
    assert _names("the Taxi and Limousine Commission shall") == [
        "Taxi and Limousine Commission"]


@pytest.mark.parametrize("text,expected", [
    ("the Department of Health and Mental Hygiene shall",
     "Department of Health and Mental Hygiene"),
    ("the Office of Management and Budget shall", "Office of Management and Budget"),
    # "with" continues a name but may never end one.
    ("the Office for People with Disabilities shall",
     "Office for People with Disabilities"),
])
def test_the_tail_rule_keeps_a_two_part_name_whole(text, expected):
    assert _names(text) == [expected]


# --------------------------------------------------------------------------- #
# trim_span — trailing debris                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw,expected", [
    # 37 captures of this exact shape before the month rule existed.
    ("Board of Estimate on June", "Board of Estimate"),
    ("Board of Estimate April", "Board of Estimate"),
    # 24 captures of this shape before the connector rule existed.
    ("Bureau of the Budget for", "Bureau of the Budget"),
    ("Board of Estimate and the", "Board of Estimate"),
    ("Office for People with", "Office for People"),
    ("Department of Correction’s", "Department of Correction"),
    # A clean name is returned unchanged.
    ("Department of Buildings", "Department of Buildings"),
    ("Commission on Human Rights", "Commission on Human Rights"),
])
def test_trimming_removes_only_trailing_junk(raw, expected):
    assert discover.trim_trailing_words(raw) == expected


def test_trimming_returns_the_start_so_the_end_position_can_shrink_with_it():
    raw = "Board of Estimate on June"
    assert raw.startswith(discover.trim_trailing_words(raw))


def test_trimming_a_possessive_does_not_eat_a_real_trailing_s():
    assert discover.trim_trailing_words("Department of Buildings") == "Department of Buildings"


# --------------------------------------------------------------------------- #
# Rejections                                                                    #
# --------------------------------------------------------------------------- #


def test_a_noun_on_its_own_is_thrown_out():
    """"Commission" alone was the top match on a naive rule — 1,174 hits, no name."""
    assert _names("The Commission shall meet.") == []
    assert WHY_JUST_A_NOUN in _whys("The Commission shall meet.")


@pytest.mark.parametrize("text", ["The Commission met", "A Board met", "Agency"])
def test_a_noun_alone_is_never_proposed(text):
    assert _names(text) == []


def test_ocr_garbage_is_thrown_out_rather_than_reviewed():
    """The head-word survives OCR but the modifiers do not; word_ratio is 0.33."""
    assert _names("the Bqxr Fkkdl Commission shall") == []
    assert WHY_UNREADABLE in _whys("the Bqxr Fkkdl Commission shall")


def test_an_ocr_damaged_but_readable_name_still_reaches_the_queue():
    """A damaged name a person can still read must not be silently dropped."""
    assert _names("the Mayor's Dmestic Violence Coordinating Council shall") == [
        "Mayor's Dmestic Violence Coordinating Council"]


# --------------------------------------------------------------------------- #
# Case rules — the head-word initial is the only capitalization that is required #
# --------------------------------------------------------------------------- #


def test_an_all_caps_title_is_captured():
    """The literal title of 1993-EO-057. A case-sensitive head-word list finds
    nothing here, which cost 1,594 spans corpus-wide before this rule."""
    text = "THE MAYOR'S DOMESTIC VIOLENCE COORDINATING COUNCIL\nWHEREAS, it is"
    assert _names(text) == ["THE MAYOR'S DOMESTIC VIOLENCE COORDINATING COUNCIL"]


def test_an_all_caps_two_word_noun_is_captured():
    assert _names("MAYOR'S TASK FORCE ON REORGANIZATION") == [
        "MAYOR'S TASK FORCE ON REORGANIZATION"]


@pytest.mark.parametrize("text", [
    "Each agency shall report annually",
    "every City agency must comply",
    "the head of each department shall",
])
def test_a_lowercase_noun_is_prose_not_a_name(text):
    """Folding the head-word entirely admits 228 spans of exactly this shape."""
    assert _names(text) == []


def test_a_name_already_in_the_name_list_is_not_re_proposed():
    known = {"taxi and limousine commission"}
    assert _names("the Taxi and Limousine Commission shall", known=known) == []


def test_the_known_name_pass_wins_every_overlap():
    text = "the Taxi and Limousine Commission shall"
    start = text.index("Taxi")
    spans = [(start, start + len("Taxi and Limousine Commission"))]
    assert _names(text, spans=spans) == []
    assert WHY_ALREADY_MATCHED in _whys(text, spans=spans)


# --------------------------------------------------------------------------- #
# Whole-corpus pass                                                             #
# --------------------------------------------------------------------------- #


def test_the_pass_counts_what_it_threw_out_by_name_and_why():
    recs = [{"eo_id": "A", "full_text": "The Commission met. The Commission met."}]
    _, rejections = discover.find_new_names(recs, frozenset(), [])
    (r,) = [x for x in rejections if x.why == WHY_JUST_A_NOUN]
    assert r.count == 2


def test_the_pass_gives_the_same_answer_every_time():
    recs = [{"eo_id": "A", "full_text": "the Board of Estimate and the Loft Board"}]
    assert discover.find_new_names(recs, frozenset(), []) == discover.find_new_names(recs, frozenset(), [])


def test_a_find_in_one_order_does_not_silence_the_same_phrase_in_another():
    """A span from order A must not suppress the same phrase in order B."""
    recs = [{"eo_id": "A", "full_text": "the Loft Board met"},
            {"eo_id": "B", "full_text": "the Loft Board met"}]
    mentions = [Mention("A", 4, 14, "Loft Board", "lb", ("lb",),
                        "name_list", "exact", "registry")]
    proposals, _ = discover.find_new_names(recs, frozenset(), mentions)
    assert [c.eo_id for c in proposals] == ["B"]


def test_a_shut_down_agency_the_registry_cannot_hold_is_found_here():
    """The point of the new-name pass. Measured: 70 spans of this name across the corpus,
    and the registry contains none of them because DoITT was folded into OTI."""
    text = ("the Department of Information Technology and Telecommunications "
            "shall provide")
    assert _names(text) == [
        "Department of Information Technology and Telecommunications"]
