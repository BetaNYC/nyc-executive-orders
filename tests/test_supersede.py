"""Offline tests for the Phase-C supersession extractor (:mod:`supersede`).

No network, no PDF, no external binary, no LLM. Every fixture string mirrors a
shape actually observed in the corpus (the source EO is named in a comment), per
engineering-standards §0 — the tests encode documented shapes, not invented ones.
"""

from __future__ import annotations

from nyc_executive_orders.supersede import (
    annotate_records,
    build_registry_index,
    compute,
    extract_body_edges,
    extract_entities,
    extract_xref_edges,
)


def _rec(eo_id, year, is_emergency=False, full_text="", dropped_header=""):
    """Minimal corpus record with the fields the extractor reads."""
    return {
        "eo_id": eo_id, "year": year,
        "is_emergency": is_emergency, "full_text": full_text,
        "dropped_header": dropped_header, "supersedes": [], "superseded_by": [],
        "establishes_entity": None, "in_effect": None,
    }


# --------------------------------------------------------------------------- #
# Citation extraction — year-scoping and series                                 #
# --------------------------------------------------------------------------- #

def test_year_comes_from_cited_date_not_doc_year():
    # Shape of 2022-EO-021: a 2022 order revoking a 2018 order.
    text = ("§ 11. Executive Order No. 31, dated March 7, 2018, is hereby "
            "REVOKED. § 12.")
    edges, dangles, ext = extract_body_edges(
        "2022-EO-021", text, corpus_ids={"2022-EO-021", "2018-EO-031"})
    assert [e.target for e in edges] == ["2018-EO-031"]
    assert edges[0].verb == "revoked"
    assert ext == 0 and dangles == []


def test_known_false_positive_trap_resolves_to_cited_year():
    # The documented trap: a 2014 doc citing "Executive Order 72, dated October
    # 6, 2005" must resolve to 2005-EO-072, NOT 2014-EO-072.
    text = "Executive Order 72, dated October 6, 2005, is hereby revoked."
    edges, _, _ = extract_body_edges(
        "2014-EO-010", text, corpus_ids={"2014-EO-010", "2005-EO-072"})
    assert edges[0].target == "2005-EO-072"


def test_series_distinction_emergency_vs_regular():
    # "Emergency Executive Order" -> EEO; a bare "Executive Order" -> EO. Same
    # number, same date, different series => different targets.
    text = ("Emergency Executive Order No. 50, dated March 4, 2022, is revoked. "
            "Executive Order No. 50, dated March 4, 2022, is revoked.")
    ids = {"2022-EEO-50", "2022-EO-050", "X"}
    edges, _, _ = extract_body_edges("X", text, corpus_ids=ids)
    targets = {e.target for e in edges}
    assert targets == {"2022-EEO-50", "2022-EO-050"}


def test_number_collision_across_administrations_is_year_scoped():
    # 2021 de Blasio EEO 50 and 2022 Adams EEO 50 are different orders. A citation
    # dated in 2021 must not resolve to the 2022 order.
    text = "Emergency Executive Order No. 50, dated December 1, 2021, is revoked."
    edges, dangles, _ = extract_body_edges(
        "2022-EEO-99", text, corpus_ids={"2022-EEO-50", "2022-EEO-99"})
    # 2021 target is not in the (2022-only) corpus => dangle, NOT a false edge.
    assert edges == []
    assert dangles[0].target == "2021-EEO-50"
    assert dangles[0].reason == "not-in-corpus"


def test_of_year_citation_form_resolves():
    # Shape of 2023-EO-035: "Executive Order 102 of 2007 is hereby revoked."
    text = "Executive Order 102 of 2007 is hereby revoked."
    edges, _, _ = extract_body_edges(
        "2023-EO-035", text, corpus_ids={"2023-EO-035", "2007-EO-102"})
    assert edges[0].target == "2007-EO-102"


def test_citation_list_shares_trailing_verb():
    # Shape of 2022-EO-003: three citations, one trailing verb.
    text = ("Executive Order No. 28, dated July 11, 2017; Executive Order No. 34, "
            "dated April 12, 2018; and Executive Order No. 50, dated November 19, "
            "2019, are hereby REVOKED.")
    ids = {"A", "2017-EO-028", "2018-EO-034", "2019-EO-050"}
    edges, _, _ = extract_body_edges("A", text, corpus_ids=ids)
    assert {e.target for e in edges} == {"2017-EO-028", "2018-EO-034", "2019-EO-050"}


# --------------------------------------------------------------------------- #
# Verb classes + in_effect                                                      #
# --------------------------------------------------------------------------- #

def test_amend_is_an_edge_but_does_not_flip_in_effect():
    # Shape of 2023-EO-037 amending 2022-EO-021.
    actor = _rec("2023-EO-037", 2023,
                 full_text="Executive Order No. 21, dated July 21, 2022, "
                           "is amended to read as follows:")
    target = _rec("2022-EO-021", 2022)
    recs = [actor, target]
    result = compute(recs, [])
    annotate_records(recs, result)
    assert target["superseded_by"] == ["2023-EO-037"]
    assert result.edges[0].verb == "amended"
    # amend never flips in_effect
    assert target["in_effect"] is None


def test_whole_revoke_flips_regular_in_effect_to_false():
    actor = _rec("2022-EO-020", 2022,
                 full_text="Executive Order No. 91, dated December 27, 2021, "
                           "is hereby revoked and replaced by this Order.")
    target = _rec("2021-EO-091", 2021)
    recs = [actor, target]
    annotate_records(recs, compute(recs, []))
    assert target["in_effect"] is False
    assert actor["in_effect"] is None  # actor not revoked by anything => null, not True


def test_section_scoped_repeal_is_partial_and_does_not_flip_in_effect():
    # Shape of 2023-EO-037 § 4: "Section 8 of Executive Order No. 21 ... is repealed."
    actor = _rec("2023-EO-037", 2023,
                 full_text="§ 4. Section 8 of Executive Order No. 21, dated "
                           "July 21, 2022, is repealed.")
    target = _rec("2022-EO-021", 2022)
    recs = [actor, target]
    result = compute(recs, [])
    annotate_records(recs, result)
    assert result.edges[0].partial is True
    assert result.edges[0].verb == "repealed"
    # partial repeal is recorded as an edge but must NOT take the order out of force
    assert target["in_effect"] is None


def test_emergency_in_effect_stays_null_even_when_revoked():
    actor = _rec("2022-EEO-99", 2022,
                 full_text="Emergency Executive Order No. 50, dated March 4, 2022, "
                           "is hereby revoked.")
    target = _rec("2022-EEO-50", 2022, is_emergency=True)
    recs = [actor, target]
    annotate_records(recs, compute(recs, []))
    assert target["superseded_by"] == ["2022-EEO-99"]  # edge recorded
    assert target["in_effect"] is None                  # but expiry is out of v1 scope


def test_in_effect_is_never_true():
    recs = [_rec("1980-EO-005", 1980, full_text="No citations here.")]
    annotate_records(recs, compute(recs, []))
    assert recs[0]["in_effect"] is None


# --------------------------------------------------------------------------- #
# Extensions, self-reference, passive                                           #
# --------------------------------------------------------------------------- #

def test_extension_is_skipped_and_counted_not_an_edge():
    # Shape of an EEO renewal chain: "... is extended for five (5) days."
    text = ("Emergency Executive Order No. 241, dated September 15, 2021, is "
            "extended for five (5) days.")
    edges, dangles, ext = extract_body_edges(
        "2022-EEO-01", text, corpus_ids={"2022-EEO-01", "2021-EEO-241"})
    assert edges == []
    assert ext == 1
    assert dangles == []


def test_self_reference_is_not_an_edge():
    text = "Executive Order No. 5, dated March 21, 2020, is hereby revoked."
    edges, _, _ = extract_body_edges(
        "2020-EO-005", text, corpus_ids={"2020-EO-005"})
    assert edges == []


def test_passive_actor_by_construction_not_captured_as_target():
    # "which was rescinded by Executive Order No. 25" — EO 25 is the ACTOR (after
    # the verb), never a target of the current doc.
    text = "the mandate, which was rescinded by Executive Order No. 25, dated February 6, 2023."
    edges, _, _ = extract_body_edges(
        "2023-EEO-99", text, corpus_ids={"2023-EEO-99", "2023-EO-025"})
    assert edges == []


# --------------------------------------------------------------------------- #
# Header XREF                                                                    #
# --------------------------------------------------------------------------- #

def test_header_xref_direction_actor_is_the_citing_order():
    # Shape of 1977-EO-091's dropped_header: "XREF: AMENDED BY 'EO 18) 1978'".
    # X AMENDED BY Y => Y (1978-EO-018) is the actor, X (current) is the target.
    header = "QUALITY REVIEW\nXREF: AMENDED BY 'EO 18) 1978\"\n"
    edges, dangles = extract_xref_edges(
        "1977-EO-091", header, corpus_ids={"1977-EO-091", "1978-EO-018"})
    assert len(edges) == 1
    e = edges[0]
    assert e.actor == "1978-EO-018" and e.target == "1977-EO-091"
    assert e.verb == "amended" and e.source == "header-xref"


def test_header_xref_dangling_actor_not_in_corpus():
    header = "XREF: REVOKED BY 'EO 18) 1978\""
    edges, dangles = extract_xref_edges(
        "1977-EO-091", header, corpus_ids={"1977-EO-091"})
    assert edges == []
    assert dangles[0].actor == "1978-EO-018"
    assert dangles[0].reason == "not-in-corpus"


# --------------------------------------------------------------------------- #
# Dangling citations                                                            #
# --------------------------------------------------------------------------- #

def test_pre_floor_citation_is_dangle_not_edge():
    # A 1974 order citing a 1970 order (below the corpus floor): recorded as a
    # dangle, never written into fields.
    text = "Executive Order No. 23, dated May 1, 1970, is hereby revoked."
    edges, dangles, _ = extract_body_edges(
        "1974-EO-030", text, corpus_ids={"1974-EO-030"})
    assert edges == []
    assert dangles[0].target == "1970-EO-023"
    assert dangles[0].reason == "not-in-corpus"


def test_citation_without_year_is_no_year_dangle():
    # No date => cannot be year-scoped => never guessed by number alone.
    text = "Executive Order No. 87 set forth prohibitions, and is hereby revoked."
    edges, dangles, _ = extract_body_edges(
        "2022-EO-005", text, corpus_ids={"2022-EO-005"})
    assert edges == []
    assert dangles[0].reason == "no-year"


def test_implausible_ocr_year_is_dangle_not_bogus_target():
    # An OCR-garbled year (1474, 2994) must never mint a target.
    text = "Executive Order No. 15, dated June 1, 1474, is hereby revoked."
    edges, dangles, _ = extract_body_edges(
        "2020-EO-010", text, corpus_ids={"2020-EO-010"})
    assert edges == []
    assert dangles[0].reason == "implausible-year"


# --------------------------------------------------------------------------- #
# Establishes-entity                                                            #
# --------------------------------------------------------------------------- #

_REGISTRY = [
    {"id": "office-of-payroll-administration",
     "name": "Office of Payroll Administration", "short_name": "OPA",
     "other_names": [{"name": "Payroll Administration"}]},
    {"id": "mome", "name": "Mayor's Office of Media and Entertainment",
     "short_name": "MOME", "other_names": []},
]


def test_registry_index_handles_dict_other_names():
    idx = build_registry_index(_REGISTRY)
    assert idx["office of payroll administration"] == ["office-of-payroll-administration"]
    assert idx["opa"] == ["office-of-payroll-administration"]
    assert idx["payroll administration"] == ["office-of-payroll-administration"]


def test_exact_entity_match_auto_writes():
    idx = build_registry_index(_REGISTRY)
    # Shape of 1984-EO-077 (OCR left a stray ')'): "there is hereby established
    # an Office of Payroll) Administration (Office)".
    text = "there is hereby established an Office of Payroll Administration (the Office)."
    matches, candidates = extract_entities("1984-EO-077", text, idx)
    assert len(matches) == 1
    assert matches[0].registry_id == "office-of-payroll-administration"
    assert candidates == []


def test_unmatched_entity_is_review_candidate_not_written():
    idx = build_registry_index(_REGISTRY)
    text = "there is hereby established a Medicaid Task Force which shall advise."
    matches, candidates = extract_entities("1976-EO-059", text, idx)
    assert matches == []
    assert candidates[0].reason == "no-match"


def test_multiple_entities_in_one_order_not_auto_written():
    rec = _rec("Z", 1984, full_text=(
        "there is hereby established an Office of Payroll Administration. "
        "Also there is hereby established the Mayor's Office of Media and "
        "Entertainment."))
    recs = [rec]
    result = compute(recs, _REGISTRY)
    annotate_records(recs, result)
    # two exact matches in one order => scalar field can't hold both => leave None
    assert rec["establishes_entity"] is None
    assert len(result.entity_matches) == 2


def test_single_match_auto_writes_registry_id_to_field():
    rec = _rec("1984-EO-077", 1984,
               full_text="there is hereby established an Office of Payroll "
                         "Administration (the Office).")
    recs = [rec]
    annotate_records(recs, compute(recs, _REGISTRY))
    assert rec["establishes_entity"] == "office-of-payroll-administration"


# --------------------------------------------------------------------------- #
# Whole-pass idempotency + array hygiene                                        #
# --------------------------------------------------------------------------- #

def test_annotate_is_idempotent_arrays_not_doubled():
    actor = _rec("B", 2023,
                 full_text="Executive Order No. 21, dated July 21, 2022, is amended.")
    target = _rec("2022-EO-021", 2022)
    recs = [actor, target]
    result = compute(recs, [])
    annotate_records(recs, result)
    annotate_records(recs, result)  # second pass must not double
    assert actor["supersedes"] == ["2022-EO-021"]
    assert target["superseded_by"] == ["B"]


def test_arrays_are_deduped_and_sorted():
    # Same citation twice in one doc must not duplicate the edge/array entry.
    actor = _rec("C", 2023, full_text=(
        "Executive Order No. 21, dated July 21, 2022, is revoked. "
        "Executive Order No. 5, dated January 1, 2022, is revoked. "
        "Executive Order No. 21, dated July 21, 2022, is revoked again."))
    t1 = _rec("2022-EO-021", 2022)
    t2 = _rec("2022-EO-005", 2022)
    recs = [actor, t1, t2]
    annotate_records(recs, compute(recs, []))
    assert actor["supersedes"] == ["2022-EO-005", "2022-EO-021"]  # sorted, deduped


def test_compute_does_not_mutate_records():
    rec = _rec("D", 2023,
               full_text="Executive Order No. 21, dated July 21, 2022, is revoked.")
    recs = [rec, _rec("2022-EO-021", 2022)]
    compute(recs, [])  # compute is read-only
    assert rec["supersedes"] == []  # untouched until annotate_records
