"""Offline tests for the JustifyAgent — grounded prior-auth drug selection, step-therapy
set, indication extraction, and the full run() packet path via a fake client (no net /
no LLM)."""

import uuid

import pytest

from app import registry
from app.agents.justify import (
    JustifyAgent,
    confirmed_condition_display,
    prior_therapy_drugs,
    select_prior_auth_drug,
)
from app.agents.safety import build_medication_index


# --- grounded inputs ---------------------------------------------------------

def _med(name, prescriber=None, **props):
    p = dict(props)
    if prescriber:
        p["prescriber"] = prescriber
    return {"id": uuid.uuid4().hex, "label": name, "type": "Medication", "properties": p}


def _cond(name, status="confirmed", date=None):
    props = {"status": status}
    if date:
        props["date"] = date
    return {"id": uuid.uuid4().hex, "label": name, "type": "Condition", "properties": props}


def _hero_nodes():
    return [
        _med("Oral iron", "Dr. A. Sharma"),
        _med("oral iron", "Dr. R. Iyer"),
        _med("Amlodipine", "Dr. N. Das"),
        _med("Ramipril", "Dr. N. Das"),
        _med("prednisolone", "Dr. S. Menon"),
        _med("methotrexate", "Dr. S. Menon"),
        _med("aspirin", "Dr. S. Menon"),
        _med("tocilizumab", "Dr. S. Menon"),
        # a diagnostic odyssey: several *confirmed* conditions over time — the earlier
        # ones are mislabels/comorbidities; the definitive dx is the most recent.
        _cond("iron deficiency", "confirmed", "2021-05-18"),
        _cond("Hypertension", "confirmed", "2023-06-30"),
        _cond("Takayasu arteritis", "confirmed", "2024-03-01"),
        _cond("Post-viral fatigue", "suspected", "2021-02-10"),
    ]


def test_select_prior_auth_drug_picks_the_biologic():
    index = build_medication_index(_hero_nodes())
    assert select_prior_auth_drug(index) == "tocilizumab"


def test_select_prior_auth_drug_none_when_no_biologic():
    index = build_medication_index([_med("aspirin"), _med("amlodipine")])
    assert select_prior_auth_drug(index) is None


def test_prior_therapy_drugs_excludes_the_requested_biologic():
    index = build_medication_index(_hero_nodes())
    prior = prior_therapy_drugs(index, "tocilizumab")
    assert prior == ["methotrexate", "prednisolone"]
    assert "tocilizumab" not in prior
    # aspirin (antiplatelet/nsaid) and iron are not immunosuppressants -> excluded
    assert "aspirin" not in prior and "iron" not in prior


def test_confirmed_condition_display_picks_most_recent_confirmed():
    # three confirmed conditions across a diagnostic odyssey -> the latest is the dx
    assert confirmed_condition_display(_hero_nodes()) == "Takayasu arteritis"
    assert confirmed_condition_display([_cond("X", "suspected", "2020-01-01")]) is None


# --- full run() via a fake client -------------------------------------------

def _cited(body):
    return (f"{body}\n\nEvidence:\n- chunk 1 of document doc_0 "
            f"(data_id: {uuid.uuid4().hex}, chunk_id: {uuid.uuid4().hex}): \"quoted source\"\n")


class _FakeClient:
    def __init__(self, *, uncited_keys=(), fail_narrative=False):
        self._graph = {"nodes": _hero_nodes(), "edges": []}
        self._uncited = set(uncited_keys)
        self._fail_narrative = fail_narrative
        self.calls = []

    async def connect(self):
        self.calls.append(("connect",))

    async def disconnect(self):
        self.calls.append(("disconnect",))

    async def dataset_graph(self, dataset_id):
        self.calls.append(("dataset_graph", dataset_id))
        return self._graph

    async def recall(self, query_text, query_type=None, datasets=None, session_id=None,
                     include_references=True, only_context=False, top_k=None, node_name=None):
        self.calls.append(("recall", session_id, datasets))
        sid = session_id or ""
        if sid.endswith("-summary"):
            if self._fail_narrative:
                return []
            return [{"text": _cited("Medical-necessity rationale."), "search_type": query_type}]
        for key in self._uncited:
            if sid.endswith(f"-{key}"):
                return [{"text": "No evidence block here.", "search_type": query_type}]
        return [{"text": _cited("Documented and supported."), "search_type": query_type}]


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
async def test_run_assembles_a_complete_cited_packet(_active_brains):
    agent = JustifyAgent("odyssey", client=_FakeClient())
    res = await agent.run()

    assert res.requested_drug == "tocilizumab"
    assert res.packet.indication == "Takayasu arteritis"
    assert [e.key for e in res.packet.elements] == \
        ["diagnosis", "active_disease", "prior_therapy", "supporting_evidence"]
    assert all(e.satisfied and e.evidence for e in res.packet.elements)
    assert res.complete is True
    assert res.missing_elements == []
    assert res.suppressed_uncited == []
    # every element became a cited backing finding
    assert len(res.packet.findings) == 4
    assert all(f.evidence for f in res.packet.findings)
    # supporting_evidence element is sourced from the reference brain
    se = next(e for e in res.packet.elements if e.key == "supporting_evidence")
    assert se.source == "reference"
    assert res.narrative is not None


@pytest.mark.asyncio
async def test_run_marks_uncited_element_missing_not_fabricated(_active_brains):
    agent = JustifyAgent("odyssey", client=_FakeClient(uncited_keys=["prior_therapy"]))
    res = await agent.run()
    prior = next(e for e in res.packet.elements if e.key == "prior_therapy")
    assert prior.satisfied is False
    assert prior.content == "" and prior.evidence == []
    assert res.complete is False
    assert "prior_therapy" in res.missing_elements
    assert "prior_therapy" in res.suppressed_uncited
    # only 3 of 4 elements produced findings
    assert len(res.packet.findings) == 3


@pytest.mark.asyncio
async def test_run_supporting_evidence_queries_reference_brain(_active_brains):
    client = _FakeClient()
    agent = JustifyAgent("odyssey", client=client)
    await agent.run()
    # find the supporting_evidence recall call and confirm it hit the trials dataset
    se_calls = [c for c in client.calls
                if c[0] == "recall" and c[1] and c[1].endswith("-supporting_evidence")]
    assert se_calls and se_calls[0][2] == ["reference_trials__x"]


@pytest.mark.asyncio
async def test_run_narrative_suppressed_when_uncited(_active_brains):
    agent = JustifyAgent("odyssey", client=_FakeClient(fail_narrative=True))
    res = await agent.run()
    assert res.narrative is None
    # packet elements still assembled + complete
    assert res.complete is True


@pytest.mark.asyncio
async def test_run_does_not_disconnect_injected_client(_active_brains):
    client = _FakeClient()
    agent = JustifyAgent("odyssey", client=client)
    await agent.run_and_close()
    assert ("disconnect",) not in client.calls
