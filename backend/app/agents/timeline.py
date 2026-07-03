"""TimelineAgent — reconstruct the patient's clinical course in date order.

Answers "when did this start / what changed since last visit?" with two products:

  1. A **deterministic, structured** `TimelineEvent[]` built directly from the graph
     nodes' event dates (Encounter/Condition/LabResult.date, Medication.start). This
     is reproducible (no LLM), powers the frontend timeline + the P4 time-travel
     slider, and never invents a date — nodes whose date is free-text (e.g. a
     Symptom.onset of "3 months") are excluded from the dated axis rather than
     guessed.

  2. A **cited narrative arc** via recall — TEMPORAL first (best-effort; this Cloud
     tenant 409s intermittently while it builds the temporal index), falling back to
     GRAPH_COMPLETION, which already yields excellent chronological, cited prose.
     The narrative is returned as a `Finding`, so it is only surfaced if it carries
     real citations (citation-required).

Safety rail (per spec): *facts only, all cited*. The narrative may quote a diagnosis
the record already documents (Timeline reports memory; it does not assert new
diagnoses), so the assertive-diagnosis lint is disabled for this agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.agents.base import BaseAgent
from app.graph_utils import clinical_mention, node_props, node_type, nodes_edges
from app.models import Confidence, Finding, FindingKind, TimelineEvent

# Leading ISO date (YYYY-MM-DD); we only trust a properly-formed date on the axis.
_ISO_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")

# Which property holds the event date, per node type.
_DATE_FIELD = {
    "Encounter": "date",
    "Condition": "date",
    "LabResult": "date",
    "Medication": "start",
    "ImagingStudy": "date",
    "Procedure": "date",
}


def _iso_date(value: object) -> Optional[str]:
    if isinstance(value, str):
        m = _ISO_PREFIX.match(value.strip())
        if m:
            return m.group(1)
    return None


def _event_date(node_type_name: str, props: dict) -> Optional[str]:
    fieldname = _DATE_FIELD.get(node_type_name)
    if not fieldname:
        return None
    return _iso_date(props.get(fieldname))


def _describe(node_type_name: str, node: dict, props: dict) -> str:
    label = clinical_mention(node)
    if node_type_name == "LabResult":
        value, unit, flag = props.get("value"), props.get("unit"), props.get("flag")
        out = label
        if value:
            out += f" = {value}{(' ' + unit) if unit else ''}"
        if flag:
            out += f" ({flag})"
        return out
    if node_type_name == "Condition":
        status = props.get("status")
        return f"{label}" + (f" [{status}]" if status else "")
    if node_type_name == "Medication":
        prescriber = props.get("prescriber")
        return f"Started {label}" + (f" — {prescriber}" if prescriber else "")
    if node_type_name == "Encounter":
        setting, reason = props.get("setting"), props.get("reason")
        head = setting or "Encounter"
        return f"{head}" + (f": {reason}" if reason else "")
    if node_type_name == "ImagingStudy":
        modality = props.get("modality")
        body_site = props.get("body_site")
        impression = props.get("impression")
        head = modality or (label if not label.startswith("ImagingStudy_") else "Imaging")
        if body_site:
            head += f" of {body_site}"
        if impression:
            head += f" — {impression}"
        return head
    if node_type_name == "Procedure":
        return label
    return label


def build_timeline_events(nodes: List[dict]) -> List[TimelineEvent]:
    """Deterministically extract date-ordered TimelineEvents from graph nodes.

    Pure function (no network/LLM) so it is fully unit-testable and reproducible.
    Only nodes carrying a valid ISO event date are placed on the axis.
    """
    events: List[TimelineEvent] = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        t = node_type(n)
        props = node_props(n)
        date = _event_date(t, props)
        if not date:
            continue
        events.append(TimelineEvent(date=date, type=t, description=_describe(t, n, props)))
    # Stable, date-then-type ordering for a deterministic axis.
    events.sort(key=lambda e: (e.date, e.type, e.description))
    return events


@dataclass
class TimelineResult:
    events: List[TimelineEvent] = field(default_factory=list)
    narrative: Optional[Finding] = None
    session_id: Optional[str] = None
    used_search_type: Optional[str] = None
    since: Optional[str] = None
    dataset_name: Optional[str] = None

    @property
    def span(self) -> Optional[tuple]:
        if not self.events:
            return None
        return self.events[0].date, self.events[-1].date

    def to_dict(self) -> dict:
        return {
            "dataset_name": self.dataset_name,
            "session_id": self.session_id,
            "used_search_type": self.used_search_type,
            "since": self.since,
            "span": self.span,
            "events": [e.model_dump() for e in self.events],
            "narrative": self.narrative.model_dump() if self.narrative else None,
        }


class TimelineAgent(BaseAgent):
    name = "timeline"
    kind = FindingKind.timeline
    # Timeline reports documented, cited history (which can include a diagnosis the
    # record itself records) — it does not assert new diagnoses, so the lint is off.
    APPLY_NO_DIAGNOSIS_LINT = False

    _NARRATIVE_QUERY = (
        "Reconstruct this patient's clinical course as a chronological timeline. "
        "For each step, give the date and what happened — key symptoms, abnormal lab "
        "values, medications started, and any documented diagnosis — in date order. "
        "Report only what the records document, and cite sources."
    )

    async def run(self, since: Optional[str] = None) -> TimelineResult:
        brain = self.clinical_brain()
        name, dataset_id = brain["name"], brain["id"]
        session_id = self.new_session_id("run")

        # 1) deterministic structured axis from the graph
        client = await self.client()
        graph = await client.dataset_graph(dataset_id)
        nodes, _edges = nodes_edges(graph)
        events = build_timeline_events(nodes)
        if since:
            events = [e for e in events if e.date >= since]

        # 2) cited narrative arc (TEMPORAL best-effort -> GRAPH_COMPLETION fallback)
        query = self._NARRATIVE_QUERY
        if since:
            query = f"Focus on what has changed since {since}. " + query

        parsed = None
        used = None
        for qt in ("TEMPORAL", "GRAPH_COMPLETION"):
            try:
                r = await self.recall(
                    query, datasets=[name], session_id=f"{session_id}-{qt.lower()}",
                    query_type=qt, include_references=True,
                )
                if (r.answer or "").strip():
                    parsed, used = r, qt
                    break
            except Exception:
                continue  # try the next strategy

        narrative = None
        if parsed is not None:
            score = self.score_from_citations(parsed.references)
            narrative = self.make_finding(
                summary=parsed.answer.strip(),
                evidence=parsed.references,
                confidence=self.confidence_band(score),
                confidence_score=score,
                session_id=session_id,
            )

        return TimelineResult(
            events=events, narrative=narrative, session_id=session_id,
            used_search_type=used, since=since, dataset_name=name,
        )

    async def run_and_close(self, since: Optional[str] = None) -> TimelineResult:
        try:
            return await self.run(since)
        finally:
            await self.aclose()
