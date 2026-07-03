"""Shared base for the Ariadne agent swarm.

Responsibilities every agent shares:
  * resolve the logical brain (patient clinical / global reference) -> physical
    Cloud dataset via the registry,
  * run recall(s) under a per-agent `session_id` = f"{agent}-{patient}-{unix}" so
    the Cloud Sessions page attributes tokens/cost/feedback per agent,
  * parse the recall into citations (`recall_parse`) — the single choke point for
    the citation-required invariant,
  * enforce the two product guardrails from `app.models`:
      - citation-required: `make_finding()` returns None (suppressed) if a claim
        has no evidence, so uncited findings can never be surfaced,
      - no-diagnosis: `guard_no_diagnosis()` lints assertive diagnostic language;
        agents that *suggest* (Connections/Trials) set `APPLY_NO_DIAGNOSIS_LINT`,
        while agents that *report documented, cited history* (Timeline/Briefing)
        may quote a diagnosis the record already contains.

Agents accept an optional injected client so tests/demo-mode can pass a fake/mock.
"""

from __future__ import annotations

import time
import uuid
from typing import List, Optional

from app import registry
from app.cognee_client import BaseCogneeClient, get_client
from app.models import (
    Confidence,
    DiagnosisLanguageError,
    EvidencePath,
    EvidenceRef,
    Finding,
    FindingKind,
    assert_no_diagnosis,
)
from app.recall_parse import RecallResult, parse_recall

GLOBAL_PATIENT = "global"


class AgentError(RuntimeError):
    pass


class BaseAgent:
    #: short agent name, used in session ids + Finding.agent
    name: str = "agent"
    #: default FindingKind for this agent's findings
    kind: FindingKind = FindingKind.briefing
    #: whether to reject assertive diagnostic language in this agent's own prose
    APPLY_NO_DIAGNOSIS_LINT: bool = True

    def __init__(self, patient_id: str, client: Optional[BaseCogneeClient] = None) -> None:
        self.patient_id = patient_id
        self._client = client
        self._owns_client = client is None

    # --- brain resolution ----------------------------------------------------
    def clinical_brain(self) -> dict:
        entry = registry.get_active(self.patient_id, "clinical")
        if not entry or not entry.get("name"):
            raise AgentError(
                f"no active clinical brain for '{self.patient_id}' — run `python -m app.seed.ingest`")
        return entry

    def reference_brain(self, kind: str) -> Optional[dict]:
        """Return the global reference brain ('literature' | 'trials'), or None."""
        return registry.get_active(GLOBAL_PATIENT, kind)

    def new_session_id(self, suffix: str = "") -> str:
        base = f"{self.name}-{self.patient_id}-{int(time.time())}"
        return f"{base}-{suffix}" if suffix else base

    # --- client lifecycle ----------------------------------------------------
    async def client(self) -> BaseCogneeClient:
        if self._client is None:
            self._client = get_client()
            await self._client.connect()
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.disconnect()
            self._client = None

    # --- recall --------------------------------------------------------------
    async def recall(
        self,
        query: str,
        *,
        datasets: List[str],
        session_id: str,
        query_type: str = "GRAPH_COMPLETION",
        include_references: bool = True,
        top_k: Optional[int] = None,
        node_name: Optional[List[str]] = None,
    ) -> RecallResult:
        client = await self.client()
        resp = await client.recall(
            query_text=query,
            query_type=query_type,
            datasets=datasets,
            session_id=session_id,
            include_references=include_references,
            top_k=top_k,
            node_name=node_name,
        )
        return parse_recall(resp)

    # --- guardrails ----------------------------------------------------------
    def guard_no_diagnosis(self, text: str) -> str:
        """Raise DiagnosisLanguageError if `text` asserts a diagnosis (when enabled)."""
        if self.APPLY_NO_DIAGNOSIS_LINT:
            return assert_no_diagnosis(text)
        return text

    def make_finding(
        self,
        *,
        summary: str,
        evidence: List[EvidenceRef],
        confidence: Confidence = Confidence.low,
        confidence_score: float = 0.0,
        session_id: Optional[str] = None,
        path: Optional[EvidencePath] = None,
        kind: Optional[FindingKind] = None,
    ) -> Optional[Finding]:
        """Build a cited Finding, enforcing citation-required + (optional) no-diagnosis.

        Returns None (the finding is *suppressed*, per policy) when there is no
        evidence, or when the summary asserts a diagnosis and the lint is enabled.
        """
        if not evidence:
            return None
        try:
            self.guard_no_diagnosis(summary)
        except DiagnosisLanguageError:
            return None
        return Finding(
            id=f"{self.name}-{uuid.uuid4().hex[:12]}",
            kind=kind or self.kind,
            summary=summary,
            confidence=confidence,
            confidence_score=confidence_score,
            evidence=evidence,
            path=path,
            agent=self.name,
            session_id=session_id,
        )

    @staticmethod
    def score_from_citations(refs: List[EvidenceRef], *, base: float = 0.4,
                             per_cite: float = 0.12, cap: float = 0.95) -> float:
        """Honest, monotone confidence: more distinct cited sources -> higher, capped."""
        distinct = {(r.data_id or r.chunk_id) for r in refs if (r.data_id or r.chunk_id)}
        if not distinct:
            return 0.0
        return round(min(base + per_cite * len(distinct), cap), 3)

    @staticmethod
    def confidence_band(score: float) -> Confidence:
        if score >= 0.75:
            return Confidence.high
        if score >= 0.5:
            return Confidence.moderate
        return Confidence.low
