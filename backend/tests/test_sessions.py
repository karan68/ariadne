"""Offline unit tests for app/sessions.py — the Sessions attribution/audit layer.

Uses recorded live shapes (a fake client) so the grouping + parsing + report logic is
fully deterministic without touching the Cloud.
"""

from __future__ import annotations

from app import sessions as S
from app.sessions import AGENT_NAMES


# --- session-id parsing ------------------------------------------------------

def test_parse_basic_session_id():
    p = S.parse_session_id("justify-odyssey-1783026394-run-summary")
    assert p is not None
    assert p.agent == "justify"
    assert p.patient == "odyssey"
    assert p.unix == 1783026394
    assert p.suffix == "run-summary"
    assert p.run_id == "justify-odyssey-1783026394"


def test_parse_multi_token_patient():
    p = S.parse_session_id("timeline-patient-x1-1700000000-run-temporal")
    assert p is not None
    assert p.agent == "timeline"
    assert p.patient == "patient-x1"
    assert p.unix == 1700000000
    assert p.suffix == "run-temporal"


def test_parse_no_suffix():
    p = S.parse_session_id("safety-global-1699999999")
    assert p is not None
    assert p.suffix == ""
    assert p.run_id == "safety-global-1699999999"


def test_parse_rejects_non_ariadne_shapes():
    assert S.parse_session_id("") is None
    assert S.parse_session_id("nodashes") is None
    assert S.parse_session_id("agent-patient") is None          # no numeric stamp
    assert S.parse_session_id("agent-nonnumeric-run") is None   # no numeric stamp


def test_is_ariadne_agent():
    assert S.is_ariadne_agent("connections")
    assert not S.is_ariadne_agent("randomtool")


# --- grouping ----------------------------------------------------------------

def _session(sid, ti=0, to=0, cost=0.0, err=0,
             started="2026-07-02T21:00:00+00:00", last=None):
    return {
        "session_id": sid,
        "tokens_in": ti, "tokens_out": to, "cost_usd": cost, "error_count": err,
        "started_at": started, "last_activity_at": last or started,
    }


def test_group_by_agent_counts_and_runs():
    payload = {"sessions": [
        _session("justify-odyssey-100-run-summary", ti=5, to=1),
        _session("justify-odyssey-100-run-diagnosis", ti=7, to=2),   # same run
        _session("justify-odyssey-200-run-summary", ti=3, to=1),     # new run
        _session("safety-odyssey-300-run-alert", ti=4, to=1),
        _session("noise-session-with-no-stamp"),                     # ignored (unparseable)
    ]}
    by = S.group_by_agent(payload)
    assert set(by) == {"justify", "safety"}
    j = by["justify"]
    assert j.session_count == 3
    assert j.run_count == 2                 # two distinct unix runs
    assert j.tokens_in == 15 and j.tokens_out == 4
    assert j.patients == {"odyssey"}
    assert by["safety"].session_count == 1


def test_group_by_agent_ariadne_only_filter():
    payload = {"sessions": [
        _session("timeline-odyssey-100-run"),
        _session("someexternalthing-odyssey-100-run"),   # parseable but not an Ariadne agent
    ]}
    only = S.group_by_agent(payload, ariadne_only=True)
    assert set(only) == {"timeline"}
    allp = S.group_by_agent(payload, ariadne_only=False)
    assert set(allp) == {"timeline", "someexternalthing"}


def test_group_by_agent_first_last_activity():
    payload = {"sessions": [
        _session("trials-odyssey-100-run-a", started="2026-07-02T10:00:00+00:00"),
        _session("trials-odyssey-100-run-b", started="2026-07-02T12:00:00+00:00"),
    ]}
    att = S.group_by_agent(payload)["trials"]
    assert att.first_activity == "2026-07-02T10:00:00+00:00"
    assert att.last_activity == "2026-07-02T12:00:00+00:00"


def test_group_accepts_bare_list_payload():
    att = S.group_by_agent([_session("briefing-odyssey-100-run-summary")])
    assert set(att) == {"briefing"}


# --- observe() over a fake client -------------------------------------------

class _FakeSessionsClient:
    """Records nothing; returns recorded live-shaped payloads."""

    def __init__(self, sessions_payload, stats_payload, cost_payload, detail_payload=None):
        self._sessions = sessions_payload
        self._stats = stats_payload
        self._cost = cost_payload
        self._detail = detail_payload or {}

    async def sessions(self, range="all", limit=200):
        return self._sessions

    async def session_stats(self, range="all"):
        return self._stats

    async def sessions_cost_by_model(self, range="all"):
        return self._cost

    async def session_detail(self, session_id):
        return self._detail


def _full_swarm_payload():
    return {"sessions": [
        _session(f"{name}-odyssey-{1000 + i}-run-x", ti=10, to=2)
        for i, name in enumerate(AGENT_NAMES)
    ]}


async def test_observe_builds_report():
    stats = {"range": "all", "sessions": 6, "tokens_in": 60, "tokens_out": 12,
             "tokens_total": 72, "total_spend_usd": 0.0}
    cost = [{"model": "litellm_proxy/litellm", "session_count": 6,
             "cost_usd": 0.0, "tokens_in": 60, "tokens_out": 12}]
    client = _FakeSessionsClient(_full_swarm_payload(), stats, cost)
    report = await S.observe(client, range="all")
    assert report.total_sessions == 6
    assert report.all_agents_attributed
    assert set(report.agents_seen) == set(AGENT_NAMES)
    assert report.tokens_total == 72
    d = report.to_dict()
    assert d["all_agents_attributed"] is True
    assert set(d["by_agent"]) == set(AGENT_NAMES)


async def test_observe_partial_swarm_not_all_attributed():
    payload = {"sessions": [_session("timeline-odyssey-1-run")]}
    client = _FakeSessionsClient(payload, {"tokens_total": 0}, [])
    report = await S.observe(client, range="all")
    assert not report.all_agents_attributed
    assert report.agents_seen == ["timeline"]


async def test_audit_trail_parses_qas():
    detail = {
        "session_id": "justify-odyssey-100-run-summary",
        "label": "medical-necessity rationale",
        "qas": [
            {"time": "2026-07-02T21:07:17", "question": "Summarise...", "answer": "The patient..."},
        ],
    }
    client = _FakeSessionsClient({}, {}, [], detail_payload=detail)
    audit = await S.audit_trail(client, "justify-odyssey-100-run-summary")
    assert audit.agent == "justify"
    assert audit.patient == "odyssey"
    assert audit.turn_count == 1
    assert audit.turns[0].question.startswith("Summarise")
    assert audit.label == "medical-necessity rationale"
