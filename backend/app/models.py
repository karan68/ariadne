"""Domain + API contracts for Ariadne.

Two product-critical invariants live here and are enforced by validators so they
are impossible to bypass:

  1. Citation-required: every Finding must carry >=1 EvidenceRef. Uncited findings
     are rejected, not shown.
  2. No-diagnosis language: `assert_no_diagnosis()` lints synthesized text for
     assertive diagnostic claims; agents run their output through it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Confidence(str, Enum):
    low = "low"
    moderate = "moderate"
    high = "high"


class FindingKind(str, Enum):
    timeline = "timeline"
    connection = "connection"
    trial = "trial"
    safety = "safety"
    briefing = "briefing"
    justify = "justify"


class EvidenceRef(BaseModel):
    """A pointer back to the exact ingested source for a claim."""

    data_id: Optional[str] = None
    chunk_id: Optional[str] = None
    document_name: Optional[str] = None
    snippet: Optional[str] = None
    source_date: Optional[str] = None


class EvidenceHop(BaseModel):
    """One hop in a multi-hop graph path (the 'red thread')."""

    subject: str
    relation: str
    object: str
    evidence: List[EvidenceRef] = Field(default_factory=list)


class EvidencePath(BaseModel):
    hops: List[EvidenceHop] = Field(default_factory=list)


class Finding(BaseModel):
    """A single cited insight produced by an agent. Uncited findings are invalid."""

    id: str
    kind: FindingKind
    summary: str
    confidence: Confidence = Confidence.low
    confidence_score: float = 0.0
    evidence: List[EvidenceRef]
    path: Optional[EvidencePath] = None
    agent: str
    session_id: Optional[str] = None
    created_at: str = Field(default_factory=_utcnow)
    disclaimer: str = "Decision support only. Not a diagnosis. Confirm independently."

    @field_validator("evidence")
    @classmethod
    def _require_evidence(cls, v: List[EvidenceRef]) -> List[EvidenceRef]:
        if not v:
            raise ValueError("citation-required: a Finding must include at least one EvidenceRef")
        return v


class TimelineEvent(BaseModel):
    date: str
    type: str
    description: str
    evidence: List[EvidenceRef] = Field(default_factory=list)


class TrialMatch(BaseModel):
    nct_id: str
    title: Optional[str] = None
    eligible: Optional[bool] = None
    deciding_criterion: Optional[str] = None
    matched_criteria: List[str] = Field(default_factory=list)
    unmet_criteria: List[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.low
    evidence: List[EvidenceRef] = Field(default_factory=list)


class SafetyAlert(BaseModel):
    kind: str = Field(description="interaction | duplication")
    medications: List[str] = Field(default_factory=list)
    severity: str = "unknown"
    rationale: str = ""
    evidence: List[EvidenceRef] = Field(default_factory=list)


class Brief(BaseModel):
    patient_id: str
    generated_at: str = Field(default_factory=_utcnow)
    summary: str = ""
    timeline_highlights: List[TimelineEvent] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)


class PriorAuthElement(BaseModel):
    """One required element of a prior-authorisation evidence packet.

    An element is `satisfied` only when it is backed by >= 1 citation from memory;
    an uncited element is left unsatisfied (never fabricated) so a reviewer can see
    exactly which part of the packet is missing.
    """

    key: str = Field(description="diagnosis | active_disease | prior_therapy | supporting_evidence")
    label: str
    content: str = ""
    satisfied: bool = False
    source: str = ""  # "clinical" | "reference"
    evidence: List[EvidenceRef] = Field(default_factory=list)


class PriorAuthPacket(BaseModel):
    """A prior-authorisation evidence packet — assembled from cited memory, never
    submitted. A packet is `complete` only when every required element is cited."""

    patient_id: str
    requested_drug: str = ""
    indication: str = ""
    generated_at: str = Field(default_factory=_utcnow)
    elements: List[PriorAuthElement] = Field(default_factory=list)
    findings: List[Finding] = Field(default_factory=list)

    @property
    def satisfied_elements(self) -> List[PriorAuthElement]:
        return [e for e in self.elements if e.satisfied]

    @property
    def missing_elements(self) -> List[str]:
        return [e.key for e in self.elements if not e.satisfied]

    @property
    def complete(self) -> bool:
        return bool(self.elements) and all(e.satisfied for e in self.elements)


# --- No-diagnosis guard ------------------------------------------------------

# Assertive diagnostic phrasing we must never emit (decision-support framing only).
_FORBIDDEN_PATTERNS = [
    r"\byou have\b",
    r"\bthe diagnosis is\b",
    r"\bpatient (?:has|is diagnosed with)\b",
    r"\bdiagnosed with\b",
    r"\bthis is (?:definitely|certainly)\b",
    r"\bconfirms? (?:the )?diagnosis\b",
]
_FORBIDDEN_RE = re.compile("|".join(_FORBIDDEN_PATTERNS), re.IGNORECASE)


class DiagnosisLanguageError(ValueError):
    pass


def find_diagnosis_language(text: str) -> List[str]:
    """Return the assertive diagnostic phrases found in `text` (empty == clean)."""
    return [m.group(0) for m in _FORBIDDEN_RE.finditer(text or "")]


def assert_no_diagnosis(text: str) -> str:
    hits = find_diagnosis_language(text)
    if hits:
        raise DiagnosisLanguageError(f"assertive diagnostic language: {hits}")
    return text
