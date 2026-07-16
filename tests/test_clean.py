"""Offline tests for the deterministic corpus cleaner (:mod:`clean`).

No network, no PDF, no external binary. Every fixture string mirrors a shape
actually observed in the OCR'd corpus (the doc it came from is named in a
comment), per engineering-standards §0 — the tests encode documented shapes,
not invented ones.
"""

from __future__ import annotations

from nyc_executive_orders import clean
from nyc_executive_orders.clean import clean_record


# --------------------------------------------------------------------------- #
# Pass 1 — anchor detection                                                     #
# --------------------------------------------------------------------------- #

def test_trims_leading_header_before_office_anchor():
    # Shape of 1974-EO-001: junk + a file-mark, then "OFFICE OF THE MAYOR".
    body = (
        "E OF-/\n"
        "Jk Cty Keay :\n"
        "yAu L, 1974\n"
        "[ps 7-&\n"
        "OFFICE OF THE MAYOR\n"
        "EXECUTIVE ORDER NO. 1\n"
        "PURSUANT TO THE PROVISIONS OF SECTION THREE OF THE NEW\n"
        "York City Charter and except as hereafter provided:\n"
    )
    r = clean_record(body, year=1974)
    assert r.anchor_found is True
    assert r.anchor_label == "office-of-the-mayor"
    assert r.full_text.startswith("OFFICE OF THE MAYOR")
    assert "Jk Cty Keay" in r.dropped_header
    assert "Jk Cty Keay" not in r.full_text
    # Non-destructive: raw preserved verbatim.
    assert r.full_text_raw == body


def test_fuzzy_anchor_catches_mangled_executive_at_char_zero():
    # 1976-EO-064: real header is "RXECUTIVE ORDER NO. 64" at char 0. A naive
    # exact match would skip it and trim 1600+ chars of real body. Fuzzy must
    # catch it so nothing is trimmed.
    body = (
        "RXECUTIVE ORDER NO. 64\n"
        "JULY 26, 1976\n"
        "ESTABLISHMENT OF THE MAYOR'S MIDTOWN ACTION OFFICE\n"
        "Whereas, The Offices of Midtown Planning and Development had broad\n"
        "common objectives to improve conditions in the Midtown area; and\n"
    )
    r = clean_record(body, year=1976)
    assert r.anchor_found is True
    assert r.dropped_header == ""          # nothing trimmed — header was at top
    assert r.full_text.startswith("RXECUTIVE ORDER NO. 64")


def test_fuzzy_anchor_catches_mangled_office_of_the_mayor():
    # 1980-EO-049: "operce oF THE MAYOR" (OFFICE OF THE MAYOR mangled).
    body = (
        "rete fe tne\n"
        "A mere, cae\n"
        "operce oF THE MAYOR\n"
        "EXECUTIVE ORDER NO. 49\n"
        "By virtue of the power vested in me as Mayor, it is hereby ordered:\n"
    )
    r = clean_record(body, year=1980)
    assert r.anchor_found is True
    assert "rete fe tne" in r.dropped_header
    assert r.full_text.startswith("operce oF THE MAYOR")


def test_no_anchor_keeps_everything_and_flags_review():
    # 1979-EO-040 shape: near-pure garbage, no recoverable anchor near the top.
    body = "oe\nee:\ndeath\nma\nrey\nDECEMBER\n"
    r = clean_record(body, year=1979, text_source="ocr")
    assert r.anchor_found is False
    assert r.dropped_header == ""          # conservative: nothing trimmed
    assert r.text_quality == clean.TEXT_QUALITY_REVIEW
    assert any(f.startswith("no-anchor-found") for f in r.flags)


def test_large_trim_is_skipped_and_flagged():
    # A body whose only anchor is a genuine mid-body "...Executive Order No. 98..."
    # reference far down; trimming to it would delete real content. No body-starter
    # precedes it, so this exercises the char-count cap (not the body-start guard).
    filler = "Legitimate agency order text that must not be trimmed here now. " * 20
    body = filler + "\npursuant to Executive Order No. 98 dated March 12, 2020\n"
    r = clean_record(body, year=2020)
    assert r.dropped_header == ""
    assert any(f.startswith("large-header-trim-skipped") for f in r.flags)
    assert r.text_quality == clean.TEXT_QUALITY_REVIEW
    assert r.full_text_raw == body


def test_anchor_after_body_start_is_not_trimmed():
    # 1975-EO-039 shape: a Commissioner amendment whose opening clause
    # ("BY VIRTUE OF THE AUTHORITY...") precedes the only anchor, which is a body
    # reference ("...Administrative Code of The City of New York..."). Trimming to
    # that anchor would delete the real opening — the guard must prevent it.
    body = (
        "proposed amendments to the Home Improvement Business Regulations\n"
        "BY VIRTUE OF THE AUTHORITY VESTED IN ME AS COMMISSIONER OF\n"
        "the Department of Consumer Affairs under Section 1105 of the Charter,\n"
        "pursuant to the Administrative Code of The City of New York, comment\n"
        "must be submitted before October 6, 1975.\n"
    )
    r = clean_record(body, year=1975, text_source="ocr")
    assert r.dropped_header == ""            # real opening clause preserved
    assert r.anchor_found is False
    assert any(f.startswith("anchor-after-body-start") for f in r.flags)
    assert r.text_quality == clean.TEXT_QUALITY_REVIEW
    assert "BY VIRTUE OF THE AUTHORITY" in r.full_text


# --------------------------------------------------------------------------- #
# Pass 2 — file-mark stripping                                                  #
# --------------------------------------------------------------------------- #

def test_strips_isolated_file_marks_only():
    body = (
        "EXECUTIVE ORDER NO. 1\n"
        "Section 1. Something is ordered here as real body text.\n"
        "37-12\n"
        "Section 2. More real body text follows the stray mark.\n"
        "nl-8\n"
    )
    r = clean_record(body, year=1974)
    assert "37-12" in r.dropped_marks
    assert "nl-8" in r.dropped_marks
    assert "37-12" not in r.full_text
    assert "nl-8" not in r.full_text
    assert "real body text" in r.full_text


def test_ps_mark_with_ampersand_stripped():
    body = "OFFICE OF THE MAYOR\nps 7-&\nSection 1. Body.\n"
    r = clean_record(body, year=1974)
    assert "ps 7-&" in r.dropped_marks


def test_list_and_section_markers_are_preserved():
    body = (
        "EXECUTIVE ORDER NO. 17\n"
        "Section 1. Prior Executive Order.\n"
        "2.\n"
        "(a) Disburse any money other than in escrow;\n"
        "§4-a\n"
        "§2.\n"
    )
    r = clean_record(body, year=1978)
    assert r.dropped_marks == []
    for keep in ("2.", "(a) Disburse", "§4-a", "§2."):
        assert keep in r.full_text


# --------------------------------------------------------------------------- #
# Pass 3 — title extraction                                                     #
# --------------------------------------------------------------------------- #

def test_extracts_allcaps_title_after_anchor():
    body = (
        "EXECUTIVE ORDER NO. 101\n"
        "December 8, 1986\n"
        "VOTER ASSISTANCE PROGRAM\n"
        "By virtue of the power vested in me as Mayor of the City of\n"
        "New York, it is hereby ordered:\n"
    )
    r = clean_record(body, year=1986)
    assert r.title == "VOTER ASSISTANCE PROGRAM"
    assert r.title_extracted is True


def test_title_containing_mayor_is_not_skipped_as_letterhead():
    # 1976-EO-064: title "ESTABLISHMENT OF THE MAYOR'S MIDTOWN ACTION OFFICE"
    # contains "OF THE MAYOR" and must NOT be mistaken for the letterhead line.
    body = (
        "RXECUTIVE ORDER NO. 64\n"
        "'\n"
        "JULY 26, 1976\n"
        "ESTABLISHMENT OF THE MAYOR'S MIDTOWN ACTION OFFICE\n"
        "Whereas, The Offices of Midtown Planning had broad objectives; and\n"
    )
    r = clean_record(body, year=1976)
    assert r.title == "ESTABLISHMENT OF THE MAYOR'S MIDTOWN ACTION OFFICE"
    assert r.title_extracted is True


def test_short_letterhead_line_is_skipped_for_title():
    # The bare "OFFICE OF THE MAYOR" letterhead (short, anchor-dominated) must be
    # skipped so the real caps subject line below it becomes the title.
    body = (
        "THE CITY OF NEW YORK\n"
        "OFFICE OF THE MAYOR\n"
        "EXECUTIVE ORDER NO. 5\n"
        "March 1, 1985\n"
        "REAL SUBJECT LINE OF THIS ORDER\n"
        "By the power vested in me as Mayor, it is hereby ordered:\n"
    )
    r = clean_record(body, year=1985)
    assert r.title == "REAL SUBJECT LINE OF THIS ORDER"


def test_mangled_title_surfaced_as_uncertain_not_written():
    # 1996-EO-027: caps subject is OCR-mangled ("TRANSPL OF AMBL ...") — must NOT
    # be written to frontmatter; surfaced as a `title-uncertain` flag instead, and
    # the doc tiers needs-review.
    body = (
        "ERECULTIVE ORDER NO. 27\n"
        "February 26, 1996\n"
        "TRANSPL OF AMBL\n"
        ".\n"
        "WHEREAS the corporation entered into a memorandum;\n"
    )
    r = clean_record(body, year=1996, text_source="ocr")
    assert r.title is None
    assert any(f.startswith("title-uncertain:") for f in r.flags)
    assert r.text_quality == clean.TEXT_QUALITY_REVIEW


def test_date_line_not_taken_as_title():
    # 1974-EO-007: "DATED JANUARY I, 1974" (Roman-numeral day) must not become a
    # title — it carries a month name and is a date line.
    body = (
        "EXECUTIVE ORDER NO. 7\n"
        "DATED JANUARY I, 1974\n"
        "ESTABLISHMENT OF A REAL COMMITTEE\n"
        "By the power vested in me as Mayor, it is hereby ordered:\n"
    )
    r = clean_record(body, year=1974)
    assert r.title == "ESTABLISHMENT OF A REAL COMMITTEE"


def test_eo_number_fragment_and_month_line_skipped():
    # 1978-EO-010 shape: one-word-per-line header split ("ORDER / No. 10 / APRIL").
    # Neither the EO-number fragment nor the bare month line is a title.
    body = (
        "EXECUTIVE\n"
        "ORDER No. 10\n"
        "APRIL\n"
        "By the power vested in me as Mayor, it is hereby ordered:\n"
    )
    r = clean_record(body, year=1978)
    assert r.title is None


def test_truncated_title_ending_in_function_word_is_held():
    # 1994-EO-014 / 1974-EO-004: caps line ending in a dangling function word is a
    # truncated line or a sentence, not a subject title.
    for caps in ("TERMINATION OF CONTRACT WITH", "PROTECT THE PRINCIPLES WHICH"):
        body = (
            "EXECUTIVE ORDER NO. 14\n"
            "March 3, 1994\n"
            f"{caps}\n"
            "By the power vested in me as Mayor, it is hereby ordered:\n"
        )
        r = clean_record(body, year=1994, text_source="ocr")
        assert r.title is None, caps
        assert any(f.startswith("title-uncertain:") for f in r.flags), caps


def test_title_edge_junk_is_stripped():
    body = (
        "EXECUTIVE ORDER NO. 91\n"
        "August 24, 1977\n"
        "* ENVIRONMENTAL QUALITY REVIEW |\n"
        "By the power vested in me as Mayor, it is hereby ordered:\n"
    )
    r = clean_record(body, year=1977)
    assert r.title == "ENVIRONMENTAL QUALITY REVIEW"


def test_extracts_multiline_wrapped_title():
    # 1974-EO-018: title wraps two lines and ends in a stray dash.
    body = (
        "EXECUTIVE ORDER NO. 18\n"
        "July 25, 1974\n"
        "ESTABLISHMENT OF AN OFFICE OF\n"
        "ELECTRONIC DATA PROCESSING—\n"
        "By the power vested in me as Mayor, it is hereby ordered:\n"
    )
    r = clean_record(body, year=1974)
    assert r.title == "ESTABLISHMENT OF AN OFFICE OF ELECTRONIC DATA PROCESSING"


def test_title_not_swallowing_body_starter():
    body = (
        "EXECUTIVE ORDER NO. 5\n"
        "March 1, 1980\n"
        "WHEREAS the following is true and also fully uppercase here;\n"
    )
    r = clean_record(body, year=1980)
    assert r.title is None
    assert any(f == "title-not-extracted" for f in r.flags)


def test_existing_title_never_overwritten():
    body = (
        "EXECUTIVE ORDER NO. 125\n"
        "June 21, 2022\n"
        "SOME OTHER CAPS SUBJECT LINE THAT SHOULD BE IGNORED\n"
    )
    r = clean_record(body, year=2022, existing_title="Real Title From Index")
    assert r.title == "Real Title From Index"
    assert r.title_extracted is False


def test_mangled_title_rejected_by_lexicon_gate():
    body = (
        "EXECUTIVE ORDER NO. 27\n"
        "February 26, 1996\n"
        "TRXNSPL XF XMBL SRYVCE FNCTNS FRM QZ XMRGNCY MDCL\n"
        "WHEREAS the corporation entered into a memorandum;\n"
    )
    r = clean_record(body, year=1996)
    assert r.title is None            # too mangled to trust into frontmatter


def test_single_unrecognized_token_holds_whole_title():
    # The "Cray" (for "City") case: one OCR-substituted word that still looks
    # word-like must hold the ENTIRE title for review, not slip through.
    body = (
        "EXECUTIVE ORDER NO. 91\n"
        "August 24, 1977\n"
        "ZQXVW ENVIRONMENTAL QUALITY REVIEW\n"
        "By the power vested in me as Mayor, it is hereby ordered:\n"
    )
    r = clean_record(body, year=1977, text_source="ocr")
    assert r.title is None
    assert any(f.startswith("title-uncertain:") for f in r.flags)
    assert r.text_quality == clean.TEXT_QUALITY_REVIEW


# --------------------------------------------------------------------------- #
# Pass 3 — date extraction                                                      #
# --------------------------------------------------------------------------- #

def test_extracts_month_name_date():
    body = "EXECUTIVE ORDER NO. 18\nJuly 25, 1974\nSection 1. Body.\n"
    r = clean_record(body, year=1974)
    assert r.date_signed == "1974-07-25"
    assert r.date_extracted is True


def test_extracts_uppercase_month_date():
    body = "EXECUTIVE ORDER NO. 64\nJULY 26, 1976\nWhereas body.\n"
    r = clean_record(body, year=1976)
    assert r.date_signed == "1976-07-26"


def test_extracts_numeric_two_digit_year_date():
    body = "OFFICE OF THE MAYOR\nEXECUTIVE ORDER NO. 91\n08/24/77\nBody.\n"
    r = clean_record(body, year=1977)
    assert r.date_signed == "1977-08-24"


def test_date_tolerates_ocr_quote_glyphs():
    # 1977-EO-091: the date line came out as `’ AUGUST 24, “1977` with stray OCR
    # quote glyphs around the year. The separator class must tolerate them.
    body = "OFFICE OF THE MAYOR\nEXECUTIVE ORDER. NO. 91\n’ AUGUST 24, “1977\nBody.\n"
    r = clean_record(body, year=1977)
    assert r.date_signed == "1977-08-24"


def test_date_year_mismatch_rejected():
    # A body-referenced date from a different year must not be adopted.
    body = "EXECUTIVE ORDER NO. 295\nJanuary 5, 1999\nBody.\n"
    r = clean_record(body, year=2022)
    assert r.date_signed is None
    assert any(f == "date-not-extracted" for f in r.flags)


def test_mangled_month_does_not_fabricate_day():
    # 1980-EO-043: "Februapyel3; 1980" — month unreadable, day fused. Never guess.
    body = "EXECUTIVE ORDER NO. 43\nFebruapyel3; 1980\nBody.\n"
    r = clean_record(body, year=1980)
    assert r.date_signed is None


def test_existing_date_never_overwritten():
    body = "EXECUTIVE ORDER NO. 125\nJune 21, 2022\nBody.\n"
    r = clean_record(body, year=2022, existing_date_signed="2022-06-21")
    assert r.date_signed == "2022-06-21"
    assert r.date_extracted is False


# --------------------------------------------------------------------------- #
# Pass 4 — quality tiering + controls                                           #
# --------------------------------------------------------------------------- #

def test_born_digital_control_untouched_and_clean():
    # 2022-EEO-295 shape: clean letterhead on line 1, title/date already in index.
    body = (
        "THE CITY OF NEW YORK\n"
        "OFFICE OF THE MAYOR\n"
        "NEW YORK, N. Y. 10007\n"
        "EMERGENCY EXECUTIVE ORDER NO. 295\n"
        "December 26, 2022\n"
        "WHEREAS, the COVID-19 pandemic has severely impacted New York City;\n"
    )
    r = clean_record(
        body, year=2022, text_source="born-digital",
        existing_title="A Real Title", existing_date_signed="2022-12-26",
    )
    assert r.dropped_header == ""
    assert r.dropped_marks == []
    assert r.title == "A Real Title"
    assert r.date_signed == "2022-12-26"
    assert r.text_quality == clean.TEXT_QUALITY_CLEAN
    # Body content unchanged apart from whitespace normalization.
    assert "WHEREAS, the COVID-19 pandemic" in r.full_text


def test_tier_needs_review_on_low_word_ratio():
    body = "OFFICE OF THE MAYOR\nxqz vbk wtf nrp lkj zxc bnm qwrt plkj;\n"
    r = clean_record(body, year=1980, text_source="ocr")
    assert r.text_quality == clean.TEXT_QUALITY_REVIEW


# --------------------------------------------------------------------------- #
# Determinism + idempotency                                                     #
# --------------------------------------------------------------------------- #

def test_deterministic_same_input_same_output():
    body = "junk head\nOFFICE OF THE MAYOR\nEXECUTIVE ORDER NO. 1\nBody text here.\n"
    a = clean_record(body, year=1974)
    b = clean_record(body, year=1974)
    assert a.full_text == b.full_text
    assert a.dropped_header == b.dropped_header
    assert a.metrics == b.metrics


def test_idempotent_recleaning_cleaned_body_is_stable():
    body = (
        "garbage stamp line\n"
        "OFFICE OF THE MAYOR\n"
        "EXECUTIVE ORDER NO. 1\n"
        "37-12\n"
        "Section 1. Real body text that stays.\n"
    )
    first = clean_record(body, year=1974)
    second = clean_record(first.full_text, year=1974)
    # Re-cleaning already-clean text drops nothing further and is a fixed point.
    assert second.dropped_header == ""
    assert second.dropped_marks == []
    assert second.full_text == first.full_text
