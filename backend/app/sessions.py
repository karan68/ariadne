"""Sessions observability + audit — the Cloud-exclusive per-agent attribution layer.

Every agent runs its recalls under `session_id = f"{agent}-{patient}-{unix}-run-{key}"`
(see `agents/base.new_session_id` + the per-recall suffixes), so Cognee Cloud's Sessions
plane records one session row per recall, attributed to the agent that issued it. This
module turns that raw stream into an auditable, per-agent scorecard:

  * `parse_session_id` — recover (agent, patient, unix, suffix) from a session id.
  * `group_by_agent` — fold the raw `sessions` list into per-agent `AgentAttribution`
    (session + run counts, token totals, first/last activity, patients touched).
  * `observe(client, range)` — async: fetch sessions + aggregate stats + cost-by-model
    and return a structured `SessionsReport`.
  * `audit_trail(client, session_id)` — the Q&A log (question + cited answer) for one
    session: the human-readable "who asked what, what memory answered" audit record.

Grounded against the live tenant (2026-07): `GET /sessions` returns a dict
`{"sessions": [ {session_id, user_id, dataset_id, status, effective_status,
started_at, last_activity_at, ended_at, tokens_in, tokens_out, cost_usd, error_count,
last_model} ]}`; `GET /sessions/stats` returns aggregate token/spend/timing counters;
`GET /sessions/cost-by-model` returns a list of `{model, session_count, cost_usd,
tokens_in, tokens_out}`; `GET /sessions/{id}` adds `label`, `msg_count`, `tool_calls`
and a `qas` list of `{time, question, context, answer}`. Per-session token/cost fields
are 0/null on this tenant (the litellm proxy only reports them in the aggregate stats),
so this module treats the aggregate as the token/cost source of truth and the per-agent
grouping + Q&A log as the attribution/audit source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

#: canonical Ariadne agent names (mirror principals.AGENT_BRAINS keys). Used to tell
#: an Ariadne agent session apart from any other session on the tenant.
AGENT_NAMES = ("timeline", "connections", "trials", "briefing", "safety", "justify")


@dataclass
class ParsedSession:
    agent: str
    patient: str
    unix: int
    suffix: str          # the part after the unix stamp, e.g. "run-summary"
    raw: str

    @property
    def run_id(self) -> str:
        """Stable id for the agent *run* (all recalls of one run share this)."""
        return f"{self.agent}-{self.patient}-{self.unix}"


def parse_session_id(session_id: str) -> Optional[ParsedSession]:
    """Recover (agent, patient, unix, suffix) from `{agent}-{patient}-{unix}[-suffix]`.

    Robust to multi-token patient ids: the unix stamp is the first purely-numeric token
    after the agent, everything between is the patient, everything after is the suffix.
    Returns None if the id does not match the Ariadne session shape.
    """
    if not session_id or "-" not in session_id:
        return None
    parts = session_id.split("-")
    if len(parts) < 3:
        return None
    agent = parts[0]
    unix_idx = next((i for i in range(1, len(parts)) if parts[i].isdigit()), None)
    if unix_idx is None or unix_idx < 2:
        return None
    patient = "-".join(parts[1:unix_idx])
    if not patient:
        return None
    suffix = "-".join(parts[unix_idx + 1:])
    return ParsedSession(agent=agent, patient=patient, unix=int(parts[unix_idx]),
                         suffix=suffix, raw=session_id)


def is_ariadne_agent(agent: str) -> bool:
    return agent in AGENT_NAMES


@dataclass
class AgentAttribution:
    agent: str
    session_count: int = 0
    run_count: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    error_count: int = 0
    patients: set = field(default_factory=set)
    first_activity: Optional[str] = None
    last_activity: Optional[str] = None
    _runs: set = field(default_factory=set, repr=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent": self.agent,
            "session_count": self.session_count,
            "run_count": self.run_count,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": round(self.cost_usd, 6),
            "error_count": self.error_count,
            "patients": sorted(self.patients),
            "first_activity": self.first_activity,
            "last_activity": self.last_activity,
        }


def _sessions_list(payload: Any) -> List[dict]:
    """Normalize the `GET /sessions` payload (dict `{"sessions": [...]}` or a bare list)."""
    if isinstance(payload, dict):
        return payload.get("sessions", []) or []
    if isinstance(payload, list):
        return payload
    return []


def group_by_agent(payload: Any, *, ariadne_only: bool = True) -> Dict[str, AgentAttribution]:
    """Fold the raw sessions payload into per-agent attribution records."""
    out: Dict[str, AgentAttribution] = {}
    for s in _sessions_list(payload):
        sid = s.get("session_id") or ""
        parsed = parse_session_id(sid)
        if parsed is None:
            continue
        if ariadne_only and not is_ariadne_agent(parsed.agent):
            continue
        att = out.get(parsed.agent)
        if att is None:
            att = AgentAttribution(agent=parsed.agent)
            out[parsed.agent] = att
        att.session_count += 1
        att._runs.add(parsed.run_id)
        att.run_count = len(att._runs)
        att.tokens_in += int(s.get("tokens_in") or 0)
        att.tokens_out += int(s.get("tokens_out") or 0)
        att.cost_usd += float(s.get("cost_usd") or 0.0)
        att.error_count += int(s.get("error_count") or 0)
        if parsed.patient:
            att.patients.add(parsed.patient)
        started = s.get("started_at")
        last = s.get("last_activity_at") or started
        if started and (att.first_activity is None or started < att.first_activity):
            att.first_activity = started
        if last and (att.last_activity is None or last > att.last_activity):
            att.last_activity = last
    return out


@dataclass
class SessionsReport:
    range: str
    total_sessions: int
    stats: Dict[str, Any]
    cost_by_model: List[dict]
    by_agent: Dict[str, AgentAttribution]

    @property
    def agents_seen(self) -> List[str]:
        return sorted(self.by_agent)

    @property
    def all_agents_attributed(self) -> bool:
        return set(AGENT_NAMES).issubset(set(self.by_agent))

    @property
    def tokens_total(self) -> int:
        return int(self.stats.get("tokens_total")
                   or (int(self.stats.get("tokens_in") or 0) + int(self.stats.get("tokens_out") or 0)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "range": self.range,
            "total_sessions": self.total_sessions,
            "agents_seen": self.agents_seen,
            "all_agents_attributed": self.all_agents_attributed,
            "tokens_total": self.tokens_total,
            "stats": self.stats,
            "cost_by_model": self.cost_by_model,
            "by_agent": {a: att.to_dict() for a, att in sorted(self.by_agent.items())},
        }


async def observe(client, range: str = "all", limit: int = 200) -> SessionsReport:
    """Fetch + fold the Sessions plane into a per-agent attribution report."""
    raw = await client.sessions(range=range, limit=limit)
    stats = await client.session_stats(range=range)
    cost = await client.sessions_cost_by_model(range=range)
    sessions = _sessions_list(raw)
    by_agent = group_by_agent(raw, ariadne_only=True)
    return SessionsReport(
        range=range,
        total_sessions=len(sessions),
        stats=stats if isinstance(stats, dict) else {},
        cost_by_model=cost if isinstance(cost, list) else [],
        by_agent=by_agent,
    )


@dataclass
class AuditTurn:
    time: Optional[str]
    question: str
    answer: str


@dataclass
class SessionAudit:
    session_id: str
    agent: Optional[str]
    patient: Optional[str]
    label: Optional[str]
    turns: List[AuditTurn]

    @property
    def turn_count(self) -> int:
        return len(self.turns)


async def audit_trail(client, session_id: str) -> SessionAudit:
    """The human-readable Q&A audit log for one session (question + cited answer)."""
    detail = await client.session_detail(session_id)
    detail = detail if isinstance(detail, dict) else {}
    parsed = parse_session_id(session_id)
    turns = [
        AuditTurn(time=q.get("time"), question=q.get("question", ""), answer=q.get("answer", ""))
        for q in (detail.get("qas") or [])
    ]
    return SessionAudit(
        session_id=session_id,
        agent=parsed.agent if parsed else None,
        patient=parsed.patient if parsed else None,
        label=detail.get("label"),
        turns=turns,
    )
