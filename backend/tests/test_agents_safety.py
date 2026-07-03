"""Offline tests for the SafetyAgent — deterministic canonicalisation, grounded med
index, interaction + cross-prescriber-duplication detection, and the full run() path
via a fake client (no net / no LLM)."""

import uuid

import pytest

from app import registry
from app.agents.safety import (
    SafetyAgent,
    build_medication_index,
    canonical_drug,
    detect_duplications,
    detect_interactions,
    drug_classes,
)


# --- canonicalisation --------------------------------------------------------

def test_canonical_drug_normalises_casing_and_synonyms():
    assert canonical_drug("Oral iron") == "iron"
    assert canonical_drug("oral iron") == "iron"
    assert canonical_drug("Ferrous sulfate") == "iron"
    assert canonical_drug("  Methotrexate ") == "methotrexate"
    assert canonical_drug("Aspirin") == "aspirin"
    assert canonical_drug("") is None


def test_drug_classes_lookup():
    assert "immunosuppressant" in drug_classes("methotrexate")
    assert "nsaid" in drug_classes("aspirin")
    assert drug_classes("unknown-drug") == set()


# --- grounded med index ------------------------------------------------------

def _med(name, prescriber=None, **props):
    p = dict(props)
    if prescriber:
        p["prescriber"] = prescriber
    return {"id": uuid.uuid4().hex, "label": name, "type": "Medication", "properties": p}


def _hero_med_nodes():
    # mirrors the live graph: casing/prescriber duplicates, 7 distinct drugs
    return [
        _med("Oral iron", "Dr. A. Sharma", start="2021-02-10"),
        _med("oral iron", "Dr. R. Iyer"),
        _med("Amlodipine", "Dr. N. Das"),
        _med("Ramipril", "Dr. N. Das"),
        _med("Tocilizumab", "Dr. S. Menon"),
        _med("prednisolone", "Dr. S. Menon", start="2024-03-01"),
        _med("methotrexate", "Dr. S. Menon", start="2024-03-01"),
        _med("aspirin", "Dr. S. Menon", dose="low-dose"),
        _med("tocilizumab", "Dr. S. Menon"),
        _med("Prednisolone", "Dr. S. Menon"),
        {"id": uuid.uuid4().hex, "label": "Fatigue", "type": "Symptom", "properties": {}},
    ]


def test_build_medication_index_dedupes_to_canonical_universe():
    index = build_medication_index(_hero_med_nodes())
    assert set(index) == {"iron", "amlodipine", "ramipril", "tocilizumab",
                          "prednisolone", "methotrexate", "aspirin"}
    # iron collected both prescribers
    assert index["iron"].prescribers == {"Dr. A. Sharma", "Dr. R. Iyer"}
    # casing duplicates collapse to one record
    assert "Oral iron" in index["iron"].raw_names and "oral iron" in index["iron"].raw_names


# --- deterministic signals ---------------------------------------------------

def test_detect_interactions_mtx_nsaid_and_immuno_stack():
    index = build_medication_index(_hero_med_nodes())
    sigs = detect_interactions(index)
    by_rule = {s.rule_id: s for s in sigs}

    assert "antimetabolite-nsaid" in by_rule
    assert set(by_rule["antimetabolite-nsaid"].medications) == {"methotrexate", "aspirin"}
    assert by_rule["antimetabolite-nsaid"].severity == "major"

    assert "immunosuppressant-stack" in by_rule
    stack = by_rule["immunosuppressant-stack"]
    assert stack.cumulative is True
    assert set(stack.medications) == {"methotrexate", "prednisolone", "tocilizumab"}


def test_detect_interactions_does_not_overflag_antihypertensives():
    # amlodipine + ramipril share only 'antihypertensive'; no rule targets that pair
    index = build_medication_index(_hero_med_nodes())
    sigs = detect_interactions(index)
    for s in sigs:
        assert set(s.medications) != {"amlodipine", "ramipril"}


def test_detect_duplications_cross_prescriber_iron_only():
    index = build_medication_index(_hero_med_nodes())
    dups = detect_duplications(index)
    assert len(dups) == 1
    assert dups[0].canonical == "iron"
    assert dups[0].prescribers == ["Dr. A. Sharma", "Dr. R. Iyer"]


# --- full run() via a fake client -------------------------------------------

def _cited(body):
    return (f"{body}\n\nEvidence:\n- chunk 1 of document doc_0 "
            f"(data_id: {uuid.uuid4().hex}, chunk_id: {uuid.uuid4().hex}): \"quoted source\"\n")


class _FakeClient:
    def __init__(self, *, uncited_rules=(), fail_narrative=False):
        self._graph = {"nodes": _hero_med_nodes(), "edges": []}
        self._uncited = set(uncited_rules)
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
        self.calls.append(("recall", session_id))
        if "interactions or therapeutic duplications" in query_text:  # narrative
            if self._fail_narrative:
                return []
            return [{"text": _cited("Interaction table across meds."), "search_type": query_type}]
        # per-signal: decide cited/uncited by which drugs / rule the session encodes
        sid = session_id or ""
        for token in self._uncited:
            if token in sid:
                return [{"text": "No evidence block here.", "search_type": query_type}]
        return [{"text": _cited("Both medications are documented."), "search_type": query_type}]


@pytest.fixture
def _active_clinical(monkeypatch):
    def fake_get_active(pid, kind="clinical"):
        if kind == "clinical":
            return {"name": "patient_odyssey_clinical__x", "id": "clin-1"}
        return None
    monkeypatch.setattr(registry, "get_active", fake_get_active)


@pytest.mark.asyncio
async def test_run_surfaces_cited_interaction_and_duplication_alerts(_active_clinical):
    agent = SafetyAgent("odyssey", client=_FakeClient())
    res = await agent.run()

    kinds = {a.kind for a in res.alerts}
    assert "interaction" in kinds and "duplication" in kinds
    # every alert cited
    assert all(a.evidence and a.evidence[0].data_id for a in res.alerts)
    # the mtx+aspirin interaction is present
    assert any(set(a.medications) == {"methotrexate", "aspirin"}
               for a in res.interaction_alerts)
    # the immuno stack (3 drugs) is present
    assert any(len(a.medications) == 3 for a in res.interaction_alerts)
    # the cross-prescriber iron duplication is present
    assert any("iron" in " ".join(a.medications) for a in res.duplication_alerts)
    assert res.narrative is not None
    assert res.suppressed_uncited == []


@pytest.mark.asyncio
async def test_run_suppresses_uncited_interaction(_active_clinical):
    # make the mtx-nsaid pair recall come back uncited
    agent = SafetyAgent("odyssey", client=_FakeClient(uncited_rules=["antimetabolite-nsaid"]))
    res = await agent.run()
    assert not any(set(a.medications) == {"methotrexate", "aspirin"}
                   for a in res.interaction_alerts)
    assert "antimetabolite-nsaid" in res.suppressed_uncited


@pytest.mark.asyncio
async def test_run_suppresses_uncited_duplication(_active_clinical):
    agent = SafetyAgent("odyssey", client=_FakeClient(uncited_rules=["dup-iron"]))
    res = await agent.run()
    assert res.duplication_alerts == []
    assert "dup-iron" in res.suppressed_uncited


@pytest.mark.asyncio
async def test_run_suppresses_narrative_without_citations(_active_clinical):
    agent = SafetyAgent("odyssey", client=_FakeClient(fail_narrative=True))
    res = await agent.run()
    assert res.narrative is None
    assert res.alerts  # structured alerts still produced
