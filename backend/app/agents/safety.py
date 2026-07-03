"""SafetyAgent — polypharmacy safety: drug-drug interactions + cross-prescriber
therapeutic duplication, every alert cited.

Same hybrid shape as the rest of the swarm (deterministic, reproducible scaffold +
Cognee recall for the *cited* evidence), grounded in a live inspection of the graph:

  * **No `INTERACTS_WITH` edges exist** in the clinical graph — Medication nodes are
    linked only to the ClinicalKnowledgeGraph container. So interactions cannot be read
    off the graph; they are derived from a small, well-established, class-based rule set
    (curated domain knowledge, exactly like the HPO/SNOMED maps or the trial age-band
    parser) applied ONLY to the medications the patient's memory actually contains.
  * **Medications are duplicated across casings/prescribers** (e.g. iron is prescribed
    by two different clinicians). Deduplicating by a canonical drug name while keeping
    the set of prescribers yields a real, deterministic *cross-prescriber duplication*
    signal for fragmented care.

For every deterministic signal, a scoped `recall()` over the clinical brain returns the
citations proving the drugs are co-documented; the curated concern text supplies the
(controlled, lint-clean) rationale and the severity. **Citation-required: an alert with
no citation is suppressed.** No-diagnosis rail is ON — the agent *flags for review*,
it never asserts a diagnosis or changes a medication.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from app.agents.base import AgentError, BaseAgent
from app.graph_utils import clinical_mention, node_props, node_type, nodes_edges
from app.models import EvidenceRef, Finding, FindingKind, SafetyAlert, find_diagnosis_language

# --- curated drug knowledge (grounded, not per-patient invented) -------------

# Raw extracted names -> canonical drug key. Only normalises spelling/casing/synonyms;
# it never invents a drug the record doesn't contain.
_SYNONYMS = {
    "oral iron": "iron",
    "iron": "iron",
    "ferrous sulfate": "iron",
    "ferrous sulphate": "iron",
}

# Canonical drug -> therapeutic classes used by the interaction rules.
CLASS_MAP: Dict[str, Set[str]] = {
    "methotrexate": {"antimetabolite", "immunosuppressant"},
    "prednisolone": {"corticosteroid", "immunosuppressant"},
    "tocilizumab": {"biologic", "immunosuppressant"},
    "aspirin": {"nsaid", "antiplatelet"},
    "amlodipine": {"ccb", "antihypertensive"},
    "ramipril": {"acei", "antihypertensive"},
    "iron": {"iron"},
}

# Nice display names (fallback: the canonical key).
_DISPLAY = {"iron": "iron therapy"}

# Pairwise interaction rules keyed on therapeutic class (well-established).
PAIRWISE_RULES = [
    {
        "id": "antimetabolite-nsaid",
        "class_a": "antimetabolite",
        "class_b": "nsaid",
        "severity": "major",
        "concern": (
            "NSAIDs and salicylates can reduce methotrexate renal clearance, raising the "
            "risk of methotrexate toxicity (myelosuppression, mucositis, hepatotoxicity). "
            "Review this combination and monitor full blood count, liver enzymes and renal "
            "function."
        ),
    },
]

# Cumulative-burden rules (>= N drugs sharing a class).
CUMULATIVE_RULES = [
    {
        "id": "immunosuppressant-stack",
        "class_tag": "immunosuppressant",
        "min_count": 2,
        "severity": "moderate",
        "concern": (
            "Multiple immunosuppressants are documented concurrently, raising cumulative "
            "infection risk. Review the combined immunosuppressive burden and ensure "
            "infection surveillance, vaccination status and prophylaxis as appropriate."
        ),
    },
]

_WS = re.compile(r"\s+")


def canonical_drug(name: str) -> Optional[str]:
    """Normalise a raw Medication label to a canonical drug key, or None if empty."""
    if not name:
        return None
    key = _WS.sub(" ", name.strip().lower())
    if key in _SYNONYMS:
        return _SYNONYMS[key]
    # single-token brand/generic names normalise to themselves
    return key


def drug_display(canonical: str) -> str:
    return _DISPLAY.get(canonical, canonical)


def drug_classes(canonical: str) -> Set[str]:
    return set(CLASS_MAP.get(canonical, set()))


# --- grounded medication universe -------------------------------------------

@dataclass
class MedicationRecord:
    canonical: str
    display: str
    prescribers: Set[str] = field(default_factory=set)
    raw_names: Set[str] = field(default_factory=set)
    classes: Set[str] = field(default_factory=set)


def build_medication_index(nodes: List[dict]) -> Dict[str, MedicationRecord]:
    """Deduplicate Medication nodes into canonical drug records (grounded universe).

    Pure function over an already-fetched graph payload. Keeps the set of prescribers
    per drug (the basis for cross-prescriber duplication) and the therapeutic classes
    (the basis for interaction rules)."""
    index: Dict[str, MedicationRecord] = {}
    for n in nodes:
        if node_type(n) != "Medication":
            continue
        raw = clinical_mention(n).strip()
        canonical = canonical_drug(raw)
        if not canonical:
            continue
        rec = index.get(canonical)
        if rec is None:
            rec = MedicationRecord(canonical=canonical, display=drug_display(canonical),
                                   classes=drug_classes(canonical))
            index[canonical] = rec
        rec.raw_names.add(raw)
        prescriber = node_props(n).get("prescriber")
        if prescriber:
            rec.prescribers.add(str(prescriber).strip())
    return index


# --- deterministic signals ---------------------------------------------------

@dataclass
class InteractionSignal:
    rule_id: str
    medications: List[str]     # canonical keys
    severity: str
    concern: str
    cumulative: bool = False


@dataclass
class DuplicationSignal:
    canonical: str
    prescribers: List[str]


def detect_interactions(index: Dict[str, MedicationRecord]) -> List[InteractionSignal]:
    """Apply curated class-based rules to the grounded drug set. Deterministic."""
    signals: List[InteractionSignal] = []

    for rule in PAIRWISE_RULES:
        a_drugs = sorted(k for k, r in index.items() if rule["class_a"] in r.classes)
        b_drugs = sorted(k for k, r in index.items() if rule["class_b"] in r.classes)
        seen: Set[frozenset] = set()
        for a in a_drugs:
            for b in b_drugs:
                if a == b:
                    continue
                pair = frozenset((a, b))
                if pair in seen:
                    continue
                seen.add(pair)
                signals.append(InteractionSignal(
                    rule_id=rule["id"], medications=sorted(pair),
                    severity=rule["severity"], concern=rule["concern"]))

    for rule in CUMULATIVE_RULES:
        members = sorted(k for k, r in index.items() if rule["class_tag"] in r.classes)
        if len(members) >= rule["min_count"]:
            signals.append(InteractionSignal(
                rule_id=rule["id"], medications=members,
                severity=rule["severity"], concern=rule["concern"], cumulative=True))

    return signals


def detect_duplications(index: Dict[str, MedicationRecord]) -> List[DuplicationSignal]:
    """Same drug documented from >= 2 distinct prescribers = cross-prescriber
    duplication. Deterministic."""
    out: List[DuplicationSignal] = []
    for canonical, rec in sorted(index.items()):
        if len(rec.prescribers) >= 2:
            out.append(DuplicationSignal(canonical=canonical,
                                         prescribers=sorted(rec.prescribers)))
    return out


# --- result container --------------------------------------------------------

@dataclass
class SafetyResult:
    alerts: List[SafetyAlert] = field(default_factory=list)
    narrative: Optional[Finding] = None
    session_id: Optional[str] = None
    clinical_dataset: Optional[str] = None
    medications: List[str] = field(default_factory=list)
    suppressed_uncited: List[str] = field(default_factory=list)

    @property
    def interaction_alerts(self) -> List[SafetyAlert]:
        return [a for a in self.alerts if a.kind == "interaction"]

    @property
    def duplication_alerts(self) -> List[SafetyAlert]:
        return [a for a in self.alerts if a.kind == "duplication"]

    def to_dict(self) -> dict:
        return {
            "clinical_dataset": self.clinical_dataset,
            "session_id": self.session_id,
            "medications": self.medications,
            "alerts": [a.model_dump() for a in self.alerts],
            "narrative": self.narrative.model_dump() if self.narrative else None,
            "suppressed_uncited": self.suppressed_uncited,
        }


class SafetyAgent(BaseAgent):
    name = "safety"
    kind = FindingKind.safety
    APPLY_NO_DIAGNOSIS_LINT = True

    def _pair_query(self, meds: List[str], *, cumulative: bool) -> str:
        names = ", ".join(drug_display(m) for m in meds)
        lead = ("the combined immunosuppressive burden from" if cumulative
                else "a potential interaction between")
        return (
            f"This patient's records document {names}. For a clinician reviewing {lead} "
            f"these medications, confirm that each is documented and cite the source notes "
            "where they appear. Report medication-safety considerations only; do not make "
            "a diagnosis."
        )

    def _dup_query(self, dup: DuplicationSignal) -> str:
        who = ", ".join(dup.prescribers)
        return (
            f"This patient's records document {drug_display(dup.canonical)} prescribed by "
            f"more than one clinician ({who}). Confirm where it is documented and by whom, "
            "citing the source notes. Report only what the records document; do not make a "
            "diagnosis."
        )

    _NARRATIVE_QUERY = (
        "Across this patient's documented medications (from all prescribers), identify any "
        "potential drug-drug interactions or therapeutic duplications a clinician should "
        "review. For each, name the medications involved and cite the source notes where "
        "they are documented. Report safety considerations only; do not make a diagnosis."
    )

    async def run(self, *, narrative: bool = True) -> SafetyResult:
        brain = self.clinical_brain()
        name, dataset_id = brain["name"], brain["id"]
        if not name:
            raise AgentError("no active clinical brain")
        session_id = self.new_session_id("run")
        client = await self.client()

        graph = await client.dataset_graph(dataset_id)
        nodes, _edges = nodes_edges(graph)
        index = build_medication_index(nodes)

        interactions = detect_interactions(index)
        duplications = detect_duplications(index)

        alerts: List[SafetyAlert] = []
        suppressed: List[str] = []

        for sig in interactions:
            refs = await self._citations(
                self._pair_query(sig.medications, cumulative=sig.cumulative),
                name, f"{session_id}-{sig.rule_id}")
            if not refs or find_diagnosis_language(sig.concern):
                suppressed.append(sig.rule_id)
                continue
            alerts.append(SafetyAlert(
                kind="interaction",
                medications=[drug_display(m) for m in sig.medications],
                severity=sig.severity, rationale=sig.concern, evidence=refs))

        for dup in duplications:
            refs = await self._citations(
                self._dup_query(dup), name, f"{session_id}-dup-{dup.canonical}")
            rationale = (
                f"The same medication ({drug_display(dup.canonical)}) is documented from "
                f"more than one prescriber ({', '.join(dup.prescribers)}). Confirm this is "
                "intentional and not unintended duplication across fragmented care."
            )
            if not refs or find_diagnosis_language(rationale):
                suppressed.append(f"dup-{dup.canonical}")
                continue
            alerts.append(SafetyAlert(
                kind="duplication", medications=[drug_display(dup.canonical)],
                severity="moderate", rationale=rationale, evidence=refs))

        narrative_finding = None
        if narrative:
            narrative_finding = await self._narrative(name, session_id)

        return SafetyResult(
            alerts=alerts, narrative=narrative_finding, session_id=session_id,
            clinical_dataset=name,
            medications=sorted(drug_display(k) for k in index),
            suppressed_uncited=suppressed,
        )

    async def _citations(self, query: str, dataset_name: str,
                         session_id: str) -> List[EvidenceRef]:
        try:
            parsed = await self.recall(
                query, datasets=[dataset_name], session_id=session_id,
                query_type="GRAPH_COMPLETION", include_references=True)
        except Exception:
            return []
        return parsed.references

    async def _narrative(self, dataset_name: str, session_id: str) -> Optional[Finding]:
        try:
            parsed = await self.recall(
                self._NARRATIVE_QUERY, datasets=[dataset_name],
                session_id=f"{session_id}-summary", query_type="GRAPH_COMPLETION",
                include_references=True)
        except Exception:
            return None
        if not (parsed.answer or "").strip():
            return None
        score = self.score_from_citations(parsed.references)
        return self.make_finding(
            summary=parsed.answer.strip(), evidence=parsed.references,
            confidence=self.confidence_band(score), confidence_score=score,
            session_id=session_id)

    async def run_and_close(self, *, narrative: bool = True) -> SafetyResult:
        try:
            return await self.run(narrative=narrative)
        finally:
            await self.aclose()
