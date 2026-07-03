"""Offline tests for the TimelineAgent — deterministic axis extraction + the full
run() path via a fake client (no network / no LLM)."""

import uuid

import pytest

from app import registry
from app.agents.timeline import TimelineAgent, build_timeline_events
from app.models import FindingKind


def _node(node_type: str, label: str, **props) -> dict:
    return {"id": uuid.uuid4().hex, "label": label, "type": node_type, "properties": props}


# Node fixtures mirror the REAL live shapes observed on the Cloud graph:
#   Medication.start, Condition.date+status, Symptom.onset (free text),
#   Encounter.date+setting+reason, LabResult.analyte+value+unit+flag+date.
def _sample_nodes():
    return [
        _node("Encounter", "General Medicine", date="2021-02-10",
              setting="General Medicine", reason="persistent fatigue"),
        _node("LabResult", "LabResult_" + uuid.uuid4().hex, date="2021-02-10",
              analyte="Hemoglobin", value="10.4", unit="g/dL", flag="low"),
        _node("Medication", "Oral iron", start="2021-02-10", prescriber="Dr. A. Sharma"),
        _node("Condition", "Post-viral fatigue", date="2021-02-10", status="suspected"),
        _node("Condition", "Takayasu arteritis", date="2024-03-01", status="confirmed"),
        # ImagingStudy label is a UUID fallback; description must compose from props.
        _node("ImagingStudy", "ImagingStudy_" + uuid.uuid4().hex, date="2024-02-08",
              modality="CT/MR angiography", body_site="aorta and great vessels",
              impression="large-vessel arteritis with wall thickening"),
        # Free-text onset -> must NOT be placed on the dated axis (no guessing).
        _node("Symptom", "Fatigue", onset="3 months"),
        # Provider has no date -> excluded.
        _node("Provider", "Dr. A. Sharma", specialty="General Medicine"),
    ]


def test_build_timeline_events_orders_and_extracts_dates():
    events = build_timeline_events(_sample_nodes())
    dates = [e.date for e in events]
    assert dates == sorted(dates)
    assert dates[0] == "2021-02-10"
    assert dates[-1] == "2024-03-01"
    # Symptom (free-text onset) and Provider (no date) are excluded.
    types = {e.type for e in events}
    assert "Symptom" not in types
    assert "Provider" not in types
    assert {"Encounter", "LabResult", "Medication", "Condition"} <= types


def test_lab_description_includes_analyte_value_unit_flag():
    events = build_timeline_events(_sample_nodes())
    lab = next(e for e in events if e.type == "LabResult")
    assert "Hemoglobin" in lab.description
    assert "10.4" in lab.description and "g/dL" in lab.description
    assert "low" in lab.description


def test_condition_and_medication_descriptions():
    events = build_timeline_events(_sample_nodes())
    conf = next(e for e in events if e.type == "Condition" and "Takayasu" in e.description)
    assert "confirmed" in conf.description
    med = next(e for e in events if e.type == "Medication")
    assert med.description.startswith("Started Oral iron")


def test_imaging_description_composes_from_props_not_uuid_label():
    events = build_timeline_events(_sample_nodes())
    img = next(e for e in events if e.type == "ImagingStudy")
    assert not img.description.startswith("ImagingStudy_")
    assert "CT/MR angiography" in img.description
    assert "aorta and great vessels" in img.description
    assert "wall thickening" in img.description


# --- full run() via a fake client -------------------------------------------

_DATA_ID = "967d6c0c-40ee-5cb2-ac3a-1ad38d8b2637"
_CHUNK_ID = "6acf9c52-1499-56c9-8493-87a0e6272e48"

_CITED_ANSWER = (
    "2021-02-10 — fatigue and raised ESR/CRP; iron started.\n"
    "2024-03-01 — large-vessel vasculitis documented.\n\n"
    "Evidence:\n"
    f"- chunk 1 of document doc_0 (data_id: {_DATA_ID}, chunk_id: {_CHUNK_ID}): "
    "\"ESR 62 mm/hr, CRP 18 mg/L\"\n"
)

_UNCITED_ANSWER = "A plain chronological summary with no evidence block."


class _FakeClient:
    def __init__(self, graph, recall_by_type, fail_types=()):
        self._graph = graph
        self._recall_by_type = recall_by_type
        self._fail_types = set(fail_types)
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
        self.calls.append(("recall", query_type, session_id))
        if query_type in self._fail_types:
            raise RuntimeError(f"simulated {query_type} 409")
        payload = self._recall_by_type.get(query_type)
        return [{"text": payload, "search_type": query_type}] if payload else []


@pytest.fixture
def _active_brain(monkeypatch):
    monkeypatch.setattr(
        registry, "get_active",
        lambda pid, kind="clinical": {"name": "patient_odyssey_clinical__x", "id": "ds-1"}
        if kind == "clinical" else None,
    )


@pytest.mark.asyncio
async def test_run_prefers_temporal_and_returns_cited_finding(_active_brain):
    graph = {"nodes": _sample_nodes(), "edges": []}
    client = _FakeClient(graph, {"TEMPORAL": _CITED_ANSWER, "GRAPH_COMPLETION": _CITED_ANSWER})
    agent = TimelineAgent("odyssey", client=client)
    res = await agent.run()

    assert res.used_search_type == "TEMPORAL"
    assert res.events and [e.date for e in res.events] == sorted(e.date for e in res.events)
    assert res.narrative is not None
    assert res.narrative.kind == FindingKind.timeline
    assert res.narrative.evidence and res.narrative.evidence[0].data_id == _DATA_ID
    assert res.narrative.confidence_score > 0


@pytest.mark.asyncio
async def test_run_falls_back_to_graph_completion_when_temporal_fails(_active_brain):
    graph = {"nodes": _sample_nodes(), "edges": []}
    client = _FakeClient(graph, {"GRAPH_COMPLETION": _CITED_ANSWER}, fail_types=["TEMPORAL"])
    agent = TimelineAgent("odyssey", client=client)
    res = await agent.run()

    assert res.used_search_type == "GRAPH_COMPLETION"
    assert res.narrative is not None and res.narrative.evidence


@pytest.mark.asyncio
async def test_run_suppresses_narrative_without_citations(_active_brain):
    # Citation-required: an uncited answer must NOT become a Finding.
    graph = {"nodes": _sample_nodes(), "edges": []}
    client = _FakeClient(graph, {"TEMPORAL": _UNCITED_ANSWER, "GRAPH_COMPLETION": _UNCITED_ANSWER})
    agent = TimelineAgent("odyssey", client=client)
    res = await agent.run()

    assert res.narrative is None  # suppressed
    assert res.events  # deterministic axis still returned


@pytest.mark.asyncio
async def test_run_since_filters_events(_active_brain):
    graph = {"nodes": _sample_nodes(), "edges": []}
    client = _FakeClient(graph, {"TEMPORAL": _CITED_ANSWER})
    agent = TimelineAgent("odyssey", client=client)
    res = await agent.run(since="2024-01-01")

    assert res.since == "2024-01-01"
    assert res.events and all(e.date >= "2024-01-01" for e in res.events)
    assert any("Takayasu" in e.description for e in res.events)
