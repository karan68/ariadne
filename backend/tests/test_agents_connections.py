"""Offline tests for the ConnectionsAgent — deterministic phenotype ranking + the
full run() path via a fake client (no network / no LLM)."""

import uuid

import pytest

from app import registry
from app.agents.connections import (
    ConnectionsAgent,
    build_candidate_index,
    patient_phenotype,
    rank_candidates,
)
from app.models import FindingKind
from app.normalize import Normalizer, hpo_display_map


def _node(node_type: str, label: str, **props) -> dict:
    return {"id": uuid.uuid4().hex, "label": label, "type": node_type, "properties": props}


def _pattern(condition: str, features) -> dict:
    return _node("LiteraturePattern", "LiteraturePattern_" + uuid.uuid4().hex,
                 condition=condition, features=list(features), source="test")


# Patient symptom nodes mirror the REAL live shapes: constitutional cluster plus the
# two vascular discriminators (claudication, cold extremities) that only a
# large-vessel vasculitis pattern accounts for.
def _clinical_nodes():
    return [
        _node("Symptom", "Fatigue"),
        _node("Symptom", "Low-grade fever"),
        _node("Symptom", "Night sweats"),
        _node("Symptom", "Unintentional weight loss"),
        _node("Symptom", "Arthralgia of knees"),
        _node("Symptom", "Upper-limb claudication"),   # -> HP:0004417 (vascular)
        _node("Symptom", "Left hand cold sensation"),  # -> HP:0500015 (vascular)
        _node("Symptom", "No chest pain"),              # pertinent negative -> dropped
        _node("Encounter", "General Medicine", date="2021-02-10"),
    ]


# Literature patterns: Takayasu carries the vascular signs; the mimics are
# constitutional-only, so the ranking must separate them.
def _literature_nodes():
    return [
        _pattern("Takayasu arteritis", [
            "fever", "night sweats", "unintentional weight loss", "fatigue",
            "arthralgia", "left-arm claudication", "diminished radial pulse",
            "subclavian bruit", "large-vessel stenosis",
        ]),
        _pattern("Lymphoma", [
            "recurrent fever", "drenching night sweats", "unintentional weight loss",
            "fatigue", "anaemia", "lymphadenopathy",
        ]),
        _pattern("Systemic lupus erythematosus", [
            "fatigue", "fever", "arthralgia", "malar rash", "photosensitivity",
        ]),
    ]


# --- pure building blocks ----------------------------------------------------

def test_patient_phenotype_normalizes_and_drops_unmatched():
    displays, hpo = patient_phenotype(_clinical_nodes(), Normalizer())
    assert "Fatigue" in displays and "Fever" in displays
    # vascular discriminators present
    assert "HP:0004417" in hpo  # Intermittent claudication
    assert "HP:0500015" in hpo  # Cold extremities
    # pertinent negative "No chest pain" must not resolve to a phenotype
    assert "Chest pain" not in displays


def test_build_candidate_index_groups_by_condition():
    index = build_candidate_index(_literature_nodes(), Normalizer())
    assert set(index) == {"Takayasu arteritis", "Lymphoma", "Systemic lupus erythematosus"}
    tak = index["Takayasu arteritis"]
    assert "HP:0004417" in tak["hpo"]      # claudication captured
    assert len(tak["features"]) == 9


def test_rank_candidates_puts_large_vessel_pattern_first():
    norm = Normalizer()
    _, hpo = patient_phenotype(_clinical_nodes(), norm)
    index = build_candidate_index(_literature_nodes(), norm)
    ranked = rank_candidates(set(hpo), index, hpo_display_map())

    assert ranked[0].condition == "Takayasu arteritis"
    # vascular weighting makes Takayasu win despite others sharing B-symptoms
    assert ranked[0].score > ranked[1].score
    assert ranked[0].vascular  # at least one vascular feature matched
    # ranking is fully deterministic (stable order across calls)
    again = rank_candidates(set(hpo), index, hpo_display_map())
    assert [c.condition for c in ranked] == [c.condition for c in again]


# --- full run() via a fake client -------------------------------------------

def _cited(answer_body: str, data_id: str) -> str:
    chunk_id = uuid.uuid4().hex
    return (
        f"{answer_body}\n\nEvidence:\n"
        f"- chunk 1 of document doc_0 (data_id: {data_id}, chunk_id: {chunk_id}): "
        f"\"characteristic features quoted\"\n"
    )


_CLINICAL = {"nodes": _clinical_nodes(), "edges": []}
_LITERATURE = {"nodes": _literature_nodes(), "edges": []}


class _FakeClient:
    """Dispatches dataset_graph by id and recall by the condition named in the query."""

    def __init__(self, *, uncited_conditions=(), fail_narrative=False):
        self._graphs = {"clin-1": _CLINICAL, "lit-1": _LITERATURE}
        self._uncited = set(uncited_conditions)
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
        self.calls.append(("recall", query_type, session_id))
        if "constellation of findings" in query_text:
            if self._fail_narrative:
                return []
            return [{"text": _cited("Most likely: Takayasu arteritis; consider lymphoma, SLE.",
                                    uuid.uuid4().hex), "search_type": query_type}]
        # per-candidate recall — cite unless this condition is set to be uncited
        for cond in ("Takayasu arteritis", "Lymphoma", "Systemic lupus erythematosus"):
            if cond.lower() in query_text.lower():
                if cond in self._uncited:
                    return [{"text": "Features without any evidence block.",
                             "search_type": query_type}]
                return [{"text": _cited(f"Features of {cond}.", uuid.uuid4().hex),
                         "search_type": query_type}]
        return []


@pytest.fixture
def _active_brains(monkeypatch):
    def fake_get_active(pid, kind="clinical"):
        if kind == "clinical":
            return {"name": "patient_odyssey_clinical__x", "id": "clin-1"}
        if kind == "literature":
            return {"name": "reference_literature__x", "id": "lit-1"}
        return None
    monkeypatch.setattr(registry, "get_active", fake_get_active)


@pytest.mark.asyncio
async def test_run_ranks_takayasu_top_with_cited_candidates(_active_brains):
    agent = ConnectionsAgent("odyssey", client=_FakeClient())
    res = await agent.run(top_k=3)

    assert res.top_condition == "Takayasu arteritis"
    assert res.patient_hpo and "HP:0004417" in res.patient_hpo
    # every surfaced candidate is a cited connection finding with an evidence path
    assert res.candidates
    top = res.candidates[0]
    assert top.kind == FindingKind.connection
    assert top.evidence and top.evidence[0].data_id
    assert top.path is not None and top.path.hops
    assert all(hop.evidence for hop in top.path.hops)  # >=1 citation per hop
    # narrative is cited
    assert res.narrative is not None and res.narrative.evidence


@pytest.mark.asyncio
async def test_run_suppresses_uncited_candidate(_active_brains):
    # SLE's scoped recall returns no evidence -> that candidate must be suppressed.
    agent = ConnectionsAgent("odyssey", client=_FakeClient(uncited_conditions=["Systemic lupus erythematosus"]))
    res = await agent.run(top_k=3)

    surfaced = {c.summary.split(":")[0].replace("Consider ", "").strip() for c in res.candidates}
    assert "Systemic lupus erythematosus" not in surfaced
    # but it still appears in the deterministic ranking (transparency)
    assert any(r["condition"] == "Systemic lupus erythematosus" for r in res.ranking)


@pytest.mark.asyncio
async def test_run_suppresses_narrative_without_citations(_active_brains):
    agent = ConnectionsAgent("odyssey", client=_FakeClient(fail_narrative=True))
    res = await agent.run(top_k=3)

    assert res.narrative is None          # suppressed (citation-required)
    assert res.candidates                 # candidates still produced
    assert res.ranking[0]["condition"] == "Takayasu arteritis"


@pytest.mark.asyncio
async def test_candidate_summaries_pass_no_diagnosis_lint(_active_brains):
    from app.models import find_diagnosis_language
    agent = ConnectionsAgent("odyssey", client=_FakeClient())
    res = await agent.run(top_k=3)
    for c in res.candidates:
        assert not find_diagnosis_language(c.summary), c.summary
