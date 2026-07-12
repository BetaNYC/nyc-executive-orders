"""Enumeration: title parsing, date parsing, entry mapping, pagination."""

from __future__ import annotations

import pytest

from nyc_executive_orders import enumerate as eo_enum


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Emergency Executive Order 718", True),
        ("Executive Order 42", False),
        ("  emergency executive order 5  ", True),
    ],
)
def test_parse_is_emergency(title, expected):
    assert eo_enum.parse_is_emergency(title) is expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Emergency Executive Order 718", "718"),  # Adams plain integer
        ("Executive Order 42", "42"),
        ("Executive Order No. 100", "100"),
        ("Executive Order No. 1.37", "1.37"),  # Mamdani dotted — prefix kept
        ("Emergency Executive Order 2.37", "2.37"),
        ("Emergency Executive Order No. 1.16 ", "1.16"),  # trailing whitespace
        ("Executive Order 08", "08"),
        ("Executive Order", None),  # no number
    ],
)
def test_parse_number(title, expected):
    assert eo_enum.parse_number(title) == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Emergency Executive Order No. 1.37", True),
        ("Executive Order No. 17", True),
        (
            "Designation of the Mayor’s Office of Contract Services, the "
            "Fire Department, and the Department of Design and Construction as "
            "Administering Agencies",
            False,
        ),
    ],
)
def test_is_executive_order_filters_non_eo(title, expected):
    assert eo_enum.is_executive_order(title) is expected


def test_parse_article_date_iso():
    assert eo_enum.parse_article_date("December 29, 2024") == "2024-12-29"
    assert eo_enum.parse_article_date("January 5, 2024") == "2024-01-05"


def test_parse_article_date_bad_returns_none():
    assert eo_enum.parse_article_date("") is None
    assert eo_enum.parse_article_date("2024-12-29") is None  # wrong format


def test_entry_from_result_full_url_and_year():
    result = {
        "link": "/mayors-office/news/2024/12/emergency-executive-order-718.html",
        "title": "Emergency Executive Order 718",
        "articleDate": "December 29, 2024",
    }
    entry = eo_enum.entry_from_result(result, fallback_year=2024)
    assert entry.number == "718"
    assert entry.is_emergency is True
    assert entry.year == 2024
    assert entry.date_signed == "2024-12-29"
    assert entry.article_url == (
        "https://www.nyc.gov/mayors-office/news/2024/12/"
        "emergency-executive-order-718.html"
    )


def test_entry_from_result_falls_back_to_window_year_when_date_missing():
    result = {"link": "/x.html", "title": "Executive Order 9", "articleDate": ""}
    entry = eo_enum.entry_from_result(result, fallback_year=2023)
    assert entry.year == 2023
    assert entry.date_signed is None


def test_enumerate_year_paginates(harvest_fetcher):
    entries = eo_enum.enumerate_year(harvest_fetcher, 2024, page_size=2)
    # 3 EOs across 2 pages (totalPages=2 in the fixtures).
    assert len(entries) == 3
    numbers = sorted(e.number for e in entries)
    assert numbers == ["42", "716", "718"]
    # Exactly two enumeration fetches (page 1 + page 2), no more.
    search_calls = [u for u in harvest_fetcher.text_calls if "articlesearch.json" in u]
    assert len(search_calls) == 2


def test_enumerate_years_spans_range(harvest_fetcher):
    # 2024 has fixtures (3 EOs). 2025 request also routes to the same pages map;
    # to keep this focused, just confirm the single-year total via the range API.
    entries = eo_enum.enumerate_years(harvest_fetcher, 2024, 2024, page_size=2)
    assert len(entries) == 3


def test_on_fetch_called_per_page(harvest_fetcher):
    calls = []
    eo_enum.enumerate_year(
        harvest_fetcher, 2024, page_size=2, on_fetch=lambda: calls.append(1)
    )
    assert len(calls) == 2  # one per page fetch
