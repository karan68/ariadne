"""Offline tests for the BriefingAgent — deterministic highlight selection +
open-question parsing, and the full run() path via a fake client (no net / no LLM)."""

import uuid

import pytest

from app import registry
from app.agents.briefing import (
    BriefingAgent,
    parse_open_questions,
    select_highlights,
)
from app.models import FindingKind, TimelineEvent


# --- select_highlights -------------------------------------------------------

def _events():
    return [
        TimelineEvent(date="2021-02-10", type="Encounter", description="General Medicine: fatigue"),
        TimelineEvent(date="2021-05-01", type="LabResult", description="ESR = 62 mm/hr (high)"),
        TimelineEvent(date="2022-03-14", type="Encounter", description="Emergency: dizziness"),
        TimelineEvent(date="2024-03-01", type="Condition", description="Takayasu arteritis [confirmed]"),
        TimelineEvent(date="2024-03-05", type="Medication", description="Started Prednisolone"),
        TimelineEvent(date="2024-09-15", type="Encounter", description="Rheumatology follow-up"),
    ]


def test_select_highlights_includes_onset_dx_and_recent():
    hl = select_highlights(_events(), recent=2)
    descs = [e.description for e in hl]
    # earliest onset
    assert hl[0].date == "2021-02-10"
    # confirmed diagnosis milestone
    assert any("Takayasu arteritis [confirmed]" in d for d in descs)
    # most recent
    assert hl[-1].date == "2024-09-15"
    # date-ordered
    assert [e.date for e in hl] == sorted(e.date for e in hl)


def test_select_highlights_empty():
    assert select_highlights([]) == []


def test_select_highlights_bounded():
    many = [TimelineEvent(date=f"2020-01-{d:02d}", type="Encounter", description=f"visit {d}")
            for d in range(1, 20)]
    hl = select_highlights(many, recent=3, max_items=6)
    assert len(hl) <= 6
    assert hl[0].date == "2020-01-01"
    assert hl[-1].date == "2020-01-19"


# --- parse_open_questions ----------------------------------------------------

def test_parse_open_questions_strips_bullets_bold_and_citations():
    answer = (
        "- **CT/MR angiography ordered on 30 Jun 2023 is still pending** – 20230630 "
        "Nephrology note【2023-06-30 — Nephrology (Dr. N. Das)】\n"
        "- **Decision on right-renal-artery angioplasty remains pending** – 15 Sep 2024 "
        "Rheumatology note【2024-09-15 — Rheumatology (Dr. S. Menon)】\n"
        "Some trailing prose that is not a bullet and should be ignored.\n"
    )
    qs = parse_open_questions(answer)
    assert len(qs) == 2
    assert qs[0] == "CT/MR angiography ordered on 30 Jun 2023 is still pending"
    assert qs[1] == "Decision on right-renal-artery angioplasty remains pending"
    assert all("【" not in q and "**" not in q for q in qs)


def test_parse_open_questions_caps_and_filters_short():
    answer = "\n".join(["- short"] + [f"- Pending follow-up number {i} to be actioned" for i in range(10)])
    qs = parse_open_questions(answer, max_items=4)
    assert len(qs) == 4
    assert all(len(q) >= 12 for q in qs)


# --- full run() via a fake client -------------------------------------------

def _clinical_graph():
    def n(t, label, **props):
        return {"id": uuid.uuid4().hex, "label": label, "type": t, "properties": props}
    return {"nodes": [
        n("Encounter", "General Medicine", date="2021-02-10", setting="General Medicine", reason="fatigue"),
        n("LabResult", "ESR", date="2021-05-01", analyte="ESR", value="62", unit="mm/hr", flag="high"),
        n("Condition", "Takayasu arteritis", date="2024-03-01", status="confirmed"),
        n("Medication", "Prednisolone", start="2024-03-05", prescriber="Dr. S. Menon"),
        n("Encounter", "Rheumatology", date="2024-09-15", setting="Rheumatology", reason="follow-up"),
        n("Symptom", "Fatigue", onset="3 months"),  # free-text -> excluded from axis
    ], "edges": []}


def _cited(body):
    return (f"{body}\n\nEvidence:\n- chunk 1 of document doc_0 "
            f"(data_id: {uuid.uuid4().hex}, chunk_id: {uuid.uuid4().hex}): \"quoted source\"\n")


_OPEN_Q_ANSWER = (
    "- **CT/MR angiography ordered on 30 Jun 2023 is still pending** – note【src】\n"
    "- **Right-renal-artery angioplasty decision remains pending** – note【src】\n"
    "- **Prednisolone taper requires continued monitoring** – note【src】\n"
)


class _FakeClient:
    def __init__(self, *, uncited=()):
        self._graph = _clinical_graph()
        self._uncited = set(uncited)  # any of {"summary","open_questions"}
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
        if "open clinical questions" in query_text:
            if "open_questions" in self._uncited:
                return [{"text": "- A pending item with no evidence block", "search_type": query_type}]
            return [{"text": _cited(_OPEN_Q_ANSWER), "search_type": query_type}]
        # summary
        if "summary" in self._uncited:
            return [{"text": "Active problems and meds, but no evidence block.", "search_type": query_type}]
        return [{"text": _cited("Active problems: large-vessel vasculitis. Meds: prednisolone. "
                                "Most recent: tapering steroids."), "search_type": query_type}]


@pytest.fixture
def _active_clinical(monkeypatch):
    def fake_get_active(pid, kind="clinical"):
        if kind == "clinical":
            return {"name": "patient_odyssey_clinical__x", "id": "clin-1"}
        return None
    monkeypatch.setattr(registry, "get_active", fake_get_active)


@pytest.mark.asyncio
async def test_run_produces_cited_brief(_active_clinical):
    agent = BriefingAgent("odyssey", client=_FakeClient())
    res = await agent.run()
    brief = res.brief

    assert brief.patient_id == "odyssey"
    assert brief.summary and "vasculitis" in brief.summary.lower()
    # highlights include the confirmed-diagnosis milestone
    assert any("Takayasu arteritis [confirmed]" in e.description for e in brief.timeline_highlights)
    # open questions parsed
    assert len(brief.open_questions) == 3
    assert all("【" not in q for q in brief.open_questions)
    # every part is backed by a cited finding
    assert len(brief.findings) == 2
    assert all(f.evidence and f.kind == FindingKind.briefing for f in brief.findings)
    assert res.suppressed == []


@pytest.mark.asyncio
async def test_run_suppresses_uncited_summary(_active_clinical):
    agent = BriefingAgent("odyssey", client=_FakeClient(uncited=["summary"]))
    res = await agent.run()
    assert res.brief.summary == ""
    assert "summary" in res.suppressed
    # open questions still present + cited
    assert res.brief.open_questions
    assert len(res.brief.findings) == 1


@pytest.mark.asyncio
async def test_run_suppresses_uncited_open_questions(_active_clinical):
    agent = BriefingAgent("odyssey", client=_FakeClient(uncited=["open_questions"]))
    res = await agent.run()
    assert res.brief.open_questions == []
    assert "open_questions" in res.suppressed
    assert res.brief.summary  # summary still present
    assert len(res.brief.findings) == 1


@pytest.mark.asyncio
async def test_run_closes_injected_client_only_on_run_and_close(_active_clinical):
    client = _FakeClient()
    agent = BriefingAgent("odyssey", client=client)
    await agent.run_and_close()
    # injected client is NOT owned, so disconnect must not be called
    assert ("disconnect",) not in client.calls
