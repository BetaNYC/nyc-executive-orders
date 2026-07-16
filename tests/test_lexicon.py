"""Offline tests for the title-gate word recognizer (:mod:`lexicon`).

Host-independent: the assertions use words guaranteed by the bundled fallback (so
they hold on a CI box with no ``/usr/share/dict/words``) and non-words that are in
no dictionary. No network.
"""

from __future__ import annotations

from nyc_executive_orders import lexicon


def test_recognizes_common_words():
    for w in ("city", "mayor", "order", "office", "executive", "review"):
        assert lexicon.recognize(w), w


def test_recognizes_domain_terms_web2_lacks():
    # web2 omits these; the domain lexicon must carry them.
    for w in ("citywide", "midtown", "stormwater", "coordination"):
        assert lexicon.recognize(w), w


def test_stems_plurals_and_gerunds():
    # Plural / gerund forms the old dictionary omits, recovered by stemming.
    for w in ("contracts", "veterans", "requirements", "processing", "controls"):
        assert lexicon.recognize(w), w


def test_recognizes_acronyms_and_roman_numerals():
    assert lexicon.recognize("CEQR")
    assert lexicon.recognize("NYC")
    assert lexicon.is_roman_numeral("iv")
    assert lexicon.recognize("IV")
    assert not lexicon.is_roman_numeral("veterans")


def test_rejects_ocr_mangles():
    # The exact mangles the prototype surfaced — none may be recognized.
    for w in ("transpl", "ambl", "zzxqw", "trxnspl", "sryvce"):
        assert not lexicon.recognize(w), w


def test_case_insensitive():
    assert lexicon.recognize("CITY") == lexicon.recognize("city")


def test_source_label_is_reported():
    src = lexicon.english_lexicon_source()
    assert src.startswith("system:") or src == "bundled-fallback"
