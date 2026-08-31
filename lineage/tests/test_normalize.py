"""Offline tests for :mod:`lineage.normalize`.

The possessive test is the load-bearing one. ``str.rstrip("'’s")`` strips a
CHARACTER SET, not a token, so it eats the real trailing "s" of "Buildings" — a bug
measured against the corpus before this module existed.
"""

from __future__ import annotations

import pytest
from lineage.normalize import collapse_spaces, normalize_name, strip_possessive

# --------------------------------------------------------------------------- #
# normalize_name                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("raw,expected", [
    ("Office of Technology and Innovation", "office of technology and innovation"),
    # Leading article dropped, so "the Office of X" matches a bare "Office of X".
    ("The Department of Buildings", "department of buildings"),
    ("A Task Force", "task force"),
    ("An Agency", "agency"),
    # Punctuation folds to space: both apostrophe styles must land on one key.
    ("Mayor's Office", "mayor s office"),
    ("Mayor’s Office", "mayor s office"),
    # Shape of 1984-EO-077, where OCR left a stray bracket inside the name.
    ("Office of Payroll) Administration", "office of payroll administration"),
    ("Office of LGBTQIA+ Affairs", "office of lgbtqia affairs"),
])
def test_normalize_name_folds_to_the_expected_key(raw, expected):
    assert normalize_name(raw) == expected


def test_normalize_name_strips_diacritics():
    assert normalize_name("Bureau of Café Inspection") == "bureau of cafe inspection"


def test_normalize_name_collapses_whitespace_runs_including_newlines():
    # PDF extraction wraps names across lines constantly; both spellings are one key.
    assert normalize_name("Cyber\nCommand") == normalize_name("Cyber Command")


def test_normalize_name_returns_empty_for_a_degenerate_name():
    assert normalize_name("---") == ""


def test_curly_and_straight_apostrophes_normalize_together():
    # 1993-EO-057 and 1993-EO-058 name the same body with different apostrophes.
    straight = "Mayor's Domestic Violence Coordinating Council"
    curly = "Mayor’s Domestic Violence Coordinating Council"
    assert normalize_name(straight) == normalize_name(curly)


# --------------------------------------------------------------------------- #
# strip_possessive — the rstrip trap                                            #
# --------------------------------------------------------------------------- #


def test_strip_possessive_does_not_eat_a_real_trailing_s():
    """The whole reason this is a function and not str.rstrip."""
    assert strip_possessive("Department of Buildings") == "Department of Buildings"
    assert "Department of Buildings".rstrip("'’s") == "Department of Building"


@pytest.mark.parametrize("raw,expected", [
    ("Department of Correction’s", "Department of Correction"),
    ("Department of Correction's", "Department of Correction"),
    ("Department of Correction", "Department of Correction"),
    # Only a trailing possessive goes; an interior apostrophe stays.
    ("Mayor's Office", "Mayor's Office"),
])
def test_strip_possessive_removes_only_the_token_suffix(raw, expected):
    assert strip_possessive(raw) == expected


# --------------------------------------------------------------------------- #
# collapse_spaces                                                                   #
# --------------------------------------------------------------------------- #


def test_collapse_spaces_folds_newlines_and_runs_to_single_spaces():
    assert collapse_spaces("Financial Information Services\nAgency") == (
        "Financial Information Services Agency")
    assert collapse_spaces("Board   of\t Estimate") == "Board of Estimate"
