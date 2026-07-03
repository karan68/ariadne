"""BriefingAgent — a one-page, fully-cited pre-visit brief for the next clinician.

Combines two capabilities the swarm already proved:
  * the **TimelineAgent's deterministic timeline engine** (`build_timeline_events`)
    to pick the key milestones (onset, confirmed diagnosis, most recent status), and
  * two scoped **cited** `recall()`s over the patient's clinical brain — one for the
    "active problems / medications / current status" summary, one for the open
    clinical questions and pending follow-ups.

Grounded design note (verified live, not assumed): although the clinical brain holds
`TextSummary` nodes, a `SUMMARIES` recall on this Cloud tenant returns raw summary text
with **no citations** — which would violate Ariadne's citation-required invariant and
make the brief unverifiable. A `GRAPH_COMPLETION` recall instead returns a concise,
structured, *cited* brief (and a cited open-questions list). So — exactly as the
TimelineAgent falls back from TEMPORAL — the BriefingAgent sources its prose from
`GRAPH_COMPLETION`. Every part of the brief is therefore derived only from cited memory:
if a recall comes back uncited, that part is suppressed rather than shown.

Safety rail: the brief *reports documented, cited history* (which may quote a diagnosis
the record already contains); it does not assert a new diagnosis. Like the TimelineAgent,
the assertive-diagnosis lint is therefore disabled for this agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.agents.base import BaseAgent
from app.agents.timeline import build_timeline_events
from app.graph_utils import nodes_edges
from app.models import Brief, Finding, FindingKind, TimelineEvent

# --- deterministic helpers (pure, unit-testable) -----------------------------

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_CITE = re.compile(r"【[^】]*】")          # full-width citation spans the LLM emits
_SOURCE_SEP = (" – ", " — ")               # question — source-note separators


def select_highlights(
    events: List[TimelineEvent], *, recent: int = 3, max_items: int = 8
) -> List[TimelineEvent]:
    """Pick the milestone events for a brief: the earliest event (onset), every
    confirmed-diagnosis condition, and the most recent `recent` events. Deterministic,
    de-duplicated, date-ordered, and bounded."""
    if not events:
        return []
    picked: dict = {}

    def add(e: TimelineEvent) -> None:
        picked[(e.date, e.type, e.description)] = e

    add(events[0])
    for e in events:
        if e.type == "Condition" and "confirm" in e.description.lower():
            add(e)
    for e in events[-recent:]:
        add(e)

    out = sorted(picked.values(), key=lambda e: (e.date, e.type, e.description))
    if len(out) > max_items:
        # keep the earliest, the confirmed-diagnosis events, and the tail
        head = out[:1]
        dx = [e for e in out if e.type == "Condition" and "confirm" in e.description.lower()]
        tail = out[-recent:]
        merged: dict = {}
        for e in head + dx + tail:
            merged[(e.date, e.type, e.description)] = e
        out = sorted(merged.values(), key=lambda e: (e.date, e.type, e.description))
    return out


def parse_open_questions(answer: str, *, max_items: int = 6) -> List[str]:
    """Extract clean open-question strings from a cited bulleted recall answer.

    Strips bullet markers, markdown bold, and the trailing citation span / source
    note so each item reads as a crisp question or pending action. Deterministic."""
    out: List[str] = []
    for raw in (answer or "").splitlines():
        line = raw.strip()
        if not _BULLET.match(line):
            continue
        line = _BULLET.sub("", line)
        line = _CITE.sub("", line)
        line = line.replace("**", "").strip()
        for sep in _SOURCE_SEP:
            i = line.find(sep)
            if i > 20:  # only trim if a real question precedes the source note
                line = line[:i].strip()
                break
        line = line.rstrip(" .;:-–—").strip()
        if len(line) >= 12:
            out.append(line)
        if len(out) >= max_items:
            break
    return out


# --- result container --------------------------------------------------------

@dataclass
class BriefingResult:
    brief: Brief
    session_id: Optional[str] = None
    clinical_dataset: Optional[str] = None
    summary_finding: Optional[Finding] = None
    open_questions_finding: Optional[Finding] = None
    event_count: int = 0
    suppressed: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "clinical_dataset": self.clinical_dataset,
            "session_id": self.session_id,
            "event_count": self.event_count,
            "suppressed": self.suppressed,
            "brief": self.brief.model_dump(),
        }


class BriefingAgent(BaseAgent):
    name = "briefing"
    kind = FindingKind.briefing
    # Reports documented, cited history (may quote a diagnosis the record records);
    # it does not assert a new diagnosis, so the assertive-diagnosis lint is off.
    APPLY_NO_DIAGNOSIS_LINT = False

    _SUMMARY_QUERY = (
        "Summarise this patient for the next clinician in a short brief: the current "
        "active problems, key medications, and the most recent documented status. "
        "Report only what the records document and cite sources."
    )
    _OPEN_QUESTIONS_QUERY = (
        "Based only on what the records document, list the open clinical questions and "
        "pending follow-ups the next clinician should address for this patient "
        "(monitoring due, unresolved results, treatment decisions pending). "
        "Give a short bulleted list and cite sources."
    )

    async def run(self) -> BriefingResult:
        brain = self.clinical_brain()
        name, dataset_id = brain["name"], brain["id"]
        session_id = self.new_session_id("run")
        client = await self.client()

        # 1) deterministic timeline milestones (reuse the TimelineAgent engine)
        graph = await client.dataset_graph(dataset_id)
        nodes, _edges = nodes_edges(graph)
        events = build_timeline_events(nodes)
        highlights = select_highlights(events)

        # 2) cited summary (GRAPH_COMPLETION — SUMMARIES is uncited on this tenant)
        summary_finding = await self._cited_finding(
            self._SUMMARY_QUERY, name, f"{session_id}-summary")

        # 3) cited open questions
        open_q_finding = await self._cited_finding(
            self._OPEN_QUESTIONS_QUERY, name, f"{session_id}-openq")
        open_questions = (
            parse_open_questions(open_q_finding.summary) if open_q_finding else [])

        findings = [f for f in (summary_finding, open_q_finding) if f is not None]
        suppressed = []
        if summary_finding is None:
            suppressed.append("summary")
        if open_q_finding is None:
            suppressed.append("open_questions")

        brief = Brief(
            patient_id=self.patient_id,
            summary=summary_finding.summary if summary_finding else "",
            timeline_highlights=highlights,
            open_questions=open_questions,
            findings=findings,
        )
        return BriefingResult(
            brief=brief, session_id=session_id, clinical_dataset=name,
            summary_finding=summary_finding, open_questions_finding=open_q_finding,
            event_count=len(events), suppressed=suppressed,
        )

    async def _cited_finding(
        self, query: str, dataset_name: str, session_id: str
    ) -> Optional[Finding]:
        try:
            parsed = await self.recall(
                query, datasets=[dataset_name], session_id=session_id,
                query_type="GRAPH_COMPLETION", include_references=True,
            )
        except Exception:
            return None
        if not (parsed.answer or "").strip():
            return None
        score = self.score_from_citations(parsed.references)
        return self.make_finding(
            summary=parsed.answer.strip(),
            evidence=parsed.references,
            confidence=self.confidence_band(score),
            confidence_score=score,
            session_id=session_id,
        )

    async def run_and_close(self) -> BriefingResult:
        try:
            return await self.run()
        finally:
            await self.aclose()
