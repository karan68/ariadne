"""Offline tests for the TrialsAgent — deterministic age/condition eligibility +
the full run() path via a fake client (no network / no LLM)."""

import uuid

import pytest

from app import registry
from app.agents.trials import (
    AgeConstraint,
    TrialRecord,
    TrialsAgent,
    build_trial_index,
    compute_age,
    evaluate_eligibility,
    hero_confirmed_conditions,
    parse_age_constraint,
)
from app.models import FindingKind


# --- age parsing -------------------------------------------------------------

def test_parse_age_constraint_variants():
    assert parse_age_constraint("Age 18 to 65 years.") == AgeConstraint("range", 18, 65)
    assert parse_age_constraint("Age 18 years or older.") == AgeConstraint("lower", 18, None)
    assert parse_age_constraint("Age 50 years or older.") == AgeConstraint("lower", 50, None)
    # "under 50" -> upper bound of 49
    assert parse_age_constraint("Age under 50 years.") == AgeConstraint("upper", None, 49)
    assert parse_age_constraint("Age 5 to 17 years at enrolment.") == AgeConstraint("range", 5, 17)
    assert parse_age_constraint("Willing to provide blood samples.") is None


def test_age_constraint_satisfied_by():
    assert AgeConstraint("range", 18, 65).satisfied_by(32)
    assert not AgeConstraint("range", 5, 17).satisfied_by(32)
    assert AgeConstraint("lower", 18, None).satisfied_by(32)
    assert not AgeConstraint("lower", 50, None).satisfied_by(32)
    assert AgeConstraint("upper", None, 49).satisfied_by(32)  # "under 50" catches 32


def test_compute_age():
    assert compute_age(1994, as_of_year=2026) == 32


# --- graph -> trial index ----------------------------------------------------

def _tnode(ntype, **props):
    return {"id": uuid.uuid4().hex, "label": ntype + "_" + uuid.uuid4().hex,
            "type": ntype, "properties": props}


def _trials_graph():
    """Mirror the REAL trials-graph shape: a ReferenceTrialsGraph container links
    (edge 'trials') to one Trial and (edge 'criteria') to its criteria."""
    nodes = []
    edges = []

    def add_trial(nct, title, conditions, incl, excl):
        cont = _tnode("ReferenceTrialsGraph")
        trial = _tnode("Trial", nct_id=nct, title=title, conditions=conditions,
                       status="Recruiting")
        nodes.extend([cont, trial])
        edges.append({"source": cont["id"], "target": trial["id"], "label": "trials"})
        for text in incl:
            c = _tnode("EligibilityCriterion", kind="inclusion", text=text)
            nodes.append(c)
            edges.append({"source": cont["id"], "target": c["id"], "label": "criteria"})
        for text in excl:
            c = _tnode("EligibilityCriterion", kind="exclusion", text=text)
            nodes.append(c)
            edges.append({"source": cont["id"], "target": c["id"], "label": "criteria"})

    add_trial("NCT09000001", "Tocilizumab for Active Takayasu (TAKT-2)",
              ["Takayasu arteritis", "large-vessel vasculitis"],
              ["Age 18 to 65 years.", "Diagnosis of Takayasu arteritis confirmed by imaging."],
              ["Active or chronic infection.", "Pregnancy or breastfeeding."])
    add_trial("NCT09000004", "Upadacitinib in Giant Cell Arteritis (GACE)",
              ["Giant cell arteritis"],
              ["Age 50 years or older.", "Confirmed giant cell arteritis."],
              ["Age under 50 years.", "Takayasu arteritis or other non-GCA vasculitis."])
    add_trial("NCT09000006", "Biologic Therapy for Childhood Takayasu (KID-TAK)",
              ["Takayasu arteritis", "paediatric vasculitis"],
              ["Age 5 to 17 years at enrolment.", "Confirmed Takayasu arteritis."],
              ["Age 18 years or older.", "Pregnancy."])
    return {"nodes": nodes, "edges": edges}


def _clinical_graph():
    def cn(name, status):
        return {"id": uuid.uuid4().hex, "label": name, "type": "Condition",
                "properties": {"status": status}}
    return {"nodes": [
        cn("Takayasu arteritis", "confirmed"),
        cn("Hypertension", "confirmed"),
        cn("Post-viral fatigue", "suspected"),   # suspected -> excluded from hero set
        cn("Large-vessel vasculitis", "suspected"),
    ], "edges": []}


def test_build_trial_index_groups_criteria_per_trial():
    g = _trials_graph()
    index = build_trial_index(g["nodes"], g["edges"])
    assert set(index) == {"NCT09000001", "NCT09000004", "NCT09000006"}
    tak = index["NCT09000001"]
    assert isinstance(tak, TrialRecord)
    assert len(tak.inclusion) == 2 and len(tak.exclusion) == 2
    assert "Takayasu arteritis" in tak.conditions


def test_hero_confirmed_conditions_only_confirmed():
    conds = hero_confirmed_conditions(_clinical_graph()["nodes"])
    assert "takayasu arteritis" in conds
    assert "hypertension" in conds
    assert "post-viral fatigue" not in conds        # suspected
    assert "large-vessel vasculitis" not in conds   # suspected


def test_evaluate_eligibility_reproduces_golden_verdicts():
    g = _trials_graph()
    index = build_trial_index(g["nodes"], g["edges"])
    hero = ["takayasu arteritis", "hypertension"]

    v1 = evaluate_eligibility(32, hero, index["NCT09000001"])
    assert v1.eligible and v1.reason == "eligible"

    # GCA: age >= 50 fails and Takayasu is excluded -> condition axis decides first
    v4 = evaluate_eligibility(32, hero, index["NCT09000004"])
    assert not v4.eligible and not v4.condition_ok

    # Paediatric Takayasu: right disease, wrong age -> age decides (the trap)
    v6 = evaluate_eligibility(32, hero, index["NCT09000006"])
    assert not v6.eligible
    assert v6.condition_ok and not v6.age_ok
    assert v6.reason == "age"
    assert "18 years or older" in v6.deciding_criterion or "5 to 17" in v6.deciding_criterion


# --- full run() via a fake client -------------------------------------------

def _cited(body, data_id):
    chunk = uuid.uuid4().hex
    return (f"{body}\n\nEvidence:\n- chunk 1 of document doc_0 "
            f"(data_id: {data_id}, chunk_id: {chunk}): \"deciding criterion quoted\"\n")


class _FakeClient:
    def __init__(self, *, uncited_ncts=(), fail_narrative=False):
        self._graphs = {"clin-1": _clinical_graph(), "trials-1": _trials_graph()}
        self._uncited = set(uncited_ncts)
        self._fail_narrative = fail_narrative
        self.calls = []

    async def connect(self):
        self.calls.append(("connect",))

    async def disconnect(self):
        self.calls.append(("disconnect",))

    async def dataset_graph(self, dataset_id):
        self.calls.append(("dataset_graph", dataset_id))
        return self._graphs[dataset_id]

    async def recall(self, query_text, query_type=None, datasets=None, session_id=None,
                     include_references=True, only_context=False, top_k=None, node_name=None):
        self.calls.append(("recall", session_id))
        if "list each trial" in query_text:  # narrative
            if self._fail_narrative:
                return []
            return [{"text": _cited("Eligibility summary across trials.", uuid.uuid4().hex),
                     "search_type": query_type}]
        for nct in ("NCT09000001", "NCT09000004", "NCT09000006"):
            if nct in query_text:
                if nct in self._uncited:
                    return [{"text": "No evidence block here.", "search_type": query_type}]
                return [{"text": _cited(f"{nct} eligibility.", uuid.uuid4().hex),
                         "search_type": query_type}]
        return []


@pytest.fixture
def _active_brains(monkeypatch):
    def fake_get_active(pid, kind="clinical"):
        if kind == "clinical":
            return {"name": "patient_odyssey_clinical__x", "id": "clin-1"}
        if kind == "trials":
            return {"name": "reference_trials__x", "id": "trials-1"}
        return None
    monkeypatch.setattr(registry, "get_active", fake_get_active)


@pytest.mark.asyncio
async def test_run_produces_cited_trialmatches_with_correct_eligibility(_active_brains):
    agent = TrialsAgent("odyssey", client=_FakeClient(), year_of_birth=1994, as_of_year=2026)
    res = await agent.run()

    assert res.hero_age == 32
    assert "takayasu arteritis" in res.hero_conditions
    by_id = {m.nct_id: m for m in res.matches}
    assert by_id["NCT09000001"].eligible is True
    assert by_id["NCT09000004"].eligible is False
    assert by_id["NCT09000006"].eligible is False
    # every surfaced match is cited (citation-required)
    assert all(m.evidence and m.evidence[0].data_id for m in res.matches)
    # the paediatric trap: age is the deciding criterion
    paeds = by_id["NCT09000006"]
    assert "18 years or older" in paeds.deciding_criterion or "5 to 17" in paeds.deciding_criterion
    assert res.narrative is not None and res.narrative.kind == FindingKind.trial


@pytest.mark.asyncio
async def test_run_suppresses_uncited_trial(_active_brains):
    agent = TrialsAgent("odyssey", client=_FakeClient(uncited_ncts=["NCT09000004"]),
                        year_of_birth=1994, as_of_year=2026)
    res = await agent.run()

    ids = {m.nct_id for m in res.matches}
    assert "NCT09000004" not in ids
    assert "NCT09000004" in res.suppressed_uncited


@pytest.mark.asyncio
async def test_run_suppresses_narrative_without_citations(_active_brains):
    agent = TrialsAgent("odyssey", client=_FakeClient(fail_narrative=True),
                        year_of_birth=1994, as_of_year=2026)
    res = await agent.run()

    assert res.narrative is None
    assert res.matches  # structured matches still produced


@pytest.mark.asyncio
async def test_eligible_ids_partition(_active_brains):
    agent = TrialsAgent("odyssey", client=_FakeClient(), year_of_birth=1994, as_of_year=2026)
    res = await agent.run()
    assert res.eligible_ids == ["NCT09000001"]
    assert set(res.ineligible_ids) == {"NCT09000004", "NCT09000006"}
