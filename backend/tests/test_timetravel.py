"""Offline unit tests for app/timetravel.py — the time-travel counterfactual.

Pure functions only (no cloud): the phenotype scan over the real hero encounter texts,
the completed-calendar-month arithmetic, the as-of subgraph date filter, and the
end-to-end trace/lead/flag computation against a clinically-faithful fixture literature
index that reproduces the honest anchors (constitutional lead 2021-09-02, first
vascular-supported flag 2022-08-05, 18 months earlier).
"""

from __future__ import annotations

from app.normalize import Normalizer
from app.seed.odyssey_patient import ENCOUNTERS, HERO_PATIENT
from app.timetravel import (
    TARGET_CONDITION,
    as_of_subgraph,
    build_candidate_index,
    build_trace,
    constitutional_lead_date,
    first_vascular_flag_date,
    months_between,
    node_event_date,
    summarize,
)
from evals.p4_eval import fixture_candidate_index, fixture_literature_nodes


# --------------------------------------------------------------------------- #
# date arithmetic
# --------------------------------------------------------------------------- #
def test_months_between_completed_calendar_months():
    assert months_between("2022-08-05", "2024-03-01") == 18
    assert months_between("2021-09-02", "2024-03-01") == 29
    assert months_between("2024-01-01", "2024-03-01") == 2
    assert months_between("2024-02-15", "2024-03-01") == 0     # day-of-month not reached
    assert months_between("2023-03-01", "2024-03-01") == 12
    assert months_between("2024-03-01", "2024-03-01") == 0
    assert months_between("2024-03-05", "2024-03-01") == 0     # clamped, never negative


def test_node_event_date_only_for_dated_types():
    assert node_event_date({"type": "Encounter", "properties": {"date": "2021-02-10"}}) == "2021-02-10"
    assert node_event_date({"type": "Medication", "properties": {"start": "2024-03-20"}}) == "2024-03-20"
    # Symptom onset is free-text -> undated
    assert node_event_date({"type": "Symptom", "properties": {"onset": "3 months"}}) is None
    # infra node -> undated
    assert node_event_date({"type": "TextSummary", "properties": {}}) is None
    # malformed date -> None (never guesses)
    assert node_event_date({"type": "Encounter", "properties": {"date": "not-a-date"}}) is None


# --------------------------------------------------------------------------- #
# as-of subgraph
# --------------------------------------------------------------------------- #
def test_as_of_subgraph_excludes_future_and_undated():
    nodes = [
        {"id": "a", "type": "Encounter", "properties": {"date": "2021-02-10"}},
        {"id": "b", "type": "LabResult", "properties": {"date": "2022-08-05"}},
        {"id": "c", "type": "Condition", "properties": {"date": "2024-03-01"}},
        {"id": "d", "type": "Symptom", "properties": {"onset": "since March"}},
    ]
    kept, excluded = as_of_subgraph(nodes, "2022-08-05")
    kept_ids = {n["id"] for n in kept}
    excl_ids = {n["id"] for n in excluded}
    assert kept_ids == {"a", "b"}              # <= cutoff
    assert excl_ids == {"c"}                   # future
    # undated symptom is in neither partition
    assert "d" not in kept_ids and "d" not in excl_ids
    # invariant: nothing kept is future-dated
    assert all((node_event_date(n) or "") <= "2022-08-05" for n in kept)


# --------------------------------------------------------------------------- #
# candidate index (fixture)
# --------------------------------------------------------------------------- #
def test_fixture_index_grounds_only_curated_conditions():
    index = fixture_candidate_index()
    assert TARGET_CONDITION in index
    assert "Giant cell arteritis" in index
    # Takayasu carries HPO for the vascular discriminators
    from app.agents.connections import VASCULAR_HPO
    assert index[TARGET_CONDITION]["hpo"] & VASCULAR_HPO
    # a constitutional-only mimic carries none of the vascular HPO
    assert not (index["Lymphoma"]["hpo"] & VASCULAR_HPO)


def test_build_candidate_index_reads_features_from_nodes():
    index = build_candidate_index(fixture_literature_nodes(), Normalizer())
    assert set(index) == set(fixture_candidate_index())


# --------------------------------------------------------------------------- #
# the trace over the real encounters
# --------------------------------------------------------------------------- #
def _trace():
    return build_trace(ENCOUNTERS, fixture_candidate_index(), Normalizer())


def test_trace_covers_every_dated_encounter_in_order():
    trace = _trace()
    dates = [s.date for s in trace]
    assert dates == sorted(dates)
    assert len(trace) == len(ENCOUNTERS)


def test_constitutional_lead_reproduces_anchor():
    assert constitutional_lead_date(_trace()) == "2021-09-02"


def test_first_vascular_flag_reproduces_anchor():
    trace = _trace()
    flag = first_vascular_flag_date(trace)
    assert flag == HERO_PATIENT["earliest_flaggable_date"] == "2022-08-05"
    step = next(s for s in trace if s.date == flag)
    assert step.top_condition == TARGET_CONDITION
    assert step.top_is_clear and step.has_vascular


def test_no_flag_before_a_real_vascular_sign():
    trace = _trace()
    for step in trace:
        if step.date < "2022-08-05":
            assert not step.has_vascular


def test_months_earlier_is_eighteen():
    trace = _trace()
    flag = first_vascular_flag_date(trace)
    assert months_between(flag, HERO_PATIENT["true_diagnosis_date"]) == 18


def test_summarize_is_internally_consistent():
    res = summarize(ENCOUNTERS, fixture_candidate_index(), Normalizer())
    assert res.true_diagnosis == TARGET_CONDITION
    assert res.constitutional_lead_date <= res.first_flag_date < res.true_diagnosis_date
    assert res.months_earlier == 18
    assert TARGET_CONDITION in res.candidates
    assert res.flag_step is not None and res.flag_step.has_vascular


def test_scan_dates_phenotype_by_the_note_text():
    """The phenotype at a date is scanned from the literal note — never invented."""
    norm = Normalizer()
    # the 2022-08-05 encounter documents claudication (the first vascular sign)
    enc = next(e for e in ENCOUNTERS if e["date"] == "2022-08-05")
    codes = {c.code for c in norm.scan(enc["text"], "symptom")}
    assert "HP:0004417" in codes           # Intermittent claudication
    # the very first encounter does NOT mention any vascular sign
    first = next(e for e in ENCOUNTERS if e["date"] == "2021-02-10")
    from app.agents.connections import VASCULAR_HPO
    first_codes = {c.code for c in norm.scan(first["text"], "symptom")}
    assert not (first_codes & VASCULAR_HPO)
