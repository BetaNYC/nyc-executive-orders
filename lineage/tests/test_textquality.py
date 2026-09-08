"""Offline tests for :mod:`lineage.textquality`.

Thresholds are ``clean.py``'s REVIEW tier, deliberately not its CLEAN tier: a
proposal name only needs to be readable enough for a person to judge, and the
strict tier discards real names from the 1946-1973 volumes, where 285 of 978
records are already flagged ``needs-review``.
"""

from __future__ import annotations

import pytest
from lineage.textquality import english_like, junk_ratio, readable, word_ratio


@pytest.mark.parametrize("token,expected", [
    ("Department", True),
    ("Commission", True),
    ("a", True),
    ("i", True),
    ("b", False),          # a lone consonant is not a word
    ("Bqxr", False),       # no vowel
    ("Fkkdl", False),      # no vowel
    ("strengths", False),  # 5+ consonant run — the rule's known cost
    ("", False),
])
def test_english_like(token, expected):
    assert english_like(token) is expected


def test_word_ratio_is_one_when_no_alpha_tokens_are_present():
    assert word_ratio("311 (2)") == 1.0


def test_word_ratio_falls_with_ocr_damage():
    assert word_ratio("Department of Buildings") == 1.0
    assert word_ratio("Bqxr Fkkdl Commission") == pytest.approx(1 / 3)


def test_junk_ratio_tolerates_ordinary_punctuation():
    # Shape of 1984-EO-077, where OCR left a bracket inside the name.
    assert junk_ratio("Office of Payroll) Administration") == 0.0
    assert junk_ratio("Mayor’s Office — “the Office”") == 0.0


def test_junk_ratio_rises_with_scanner_artifacts():
    assert junk_ratio("Off\x0cice ▮▮ of ▮ Bui\x0bldings") > 0.15


@pytest.mark.parametrize("text,expected", [
    ("Department of Buildings", True),
    ("Taxi and Limousine Commission", True),
    # Damaged but readable — a person must still get to see it.
    ("Mayor's Dmestic Violence Coordinating Council", True),
    ("Bqxr Fkkdl Commission", False),
])
def test_readable(text, expected):
    assert readable(text) is expected
