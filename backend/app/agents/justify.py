"""JustifyAgent — assemble a prior-authorisation evidence packet for a specialty
(biologic) therapy, every element cited, from the patient's own memory.

Prior authorisation is where longitudinal, cited memory pays for itself: a payer
requires proof of (1) the confirmed indication, (2) active disease, (3) conventional
first-line therapy already tried ("step therapy"), and (4) evidence supporting the
requested drug for that indication. Each of those facts is scattered across years of
notes — exactly what Ariadne's graph holds and can cite.

Same hybrid shape as the rest of the swarm, grounded in a live inspection of the graph:

  * **The requested drug is read from the graph, not assumed.** `build_medication_index`
    (shared with SafetyAgent) canonicalises the patient's Medication nodes; the drug that
    needs prior authorisation is the *biologic* in that set (class-tagged, curated
    knowledge — biologics are the specialty drugs payers gate). For the hero this is
    tocilizumab; nothing is invented.
  * **The indication is the patient's confirmed condition** (read from the clinical graph,
    reusing the TrialsAgent's `hero_confirmed_conditions`).
  * **Prior conventional therapy is the patient's other documented immunosuppressants**
    (the glucocorticoid + steroid-sparing DMARD already in the med index) — the real
    step-therapy the record supports.

For each required element a scoped `recall()` returns the citations that prove the fact;
the element is marked *satisfied* only when cited (**citation-required** — an uncited
element is left unsatisfied and surfaced as missing, never fabricated). The packet is
*complete* only when every element is cited. It **assembles, it does not submit** — the
output is an evidence packet a human reviews and files.

No-diagnosis rail is OFF: like Timeline/Briefing, this agent reports the *documented,
cited* diagnosis (a prior-auth request necessarily states the indication the record
already confirms); it asserts nothing new.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from app.agents.base import AgentError, BaseAgent
from app.agents.safety import MedicationRecord, build_medication_index, drug_display
from app.graph_utils import clinical_mention, node_props, node_type, nodes_edges
from app.models import (
    Confidence,
    EvidenceRef,
    Finding,
    FindingKind,
    PriorAuthElement,
    PriorAuthPacket,
)

# The fixed checklist a prior-auth packet must satisfy (deterministic ordering).
REQUIRED_ELEMENTS: List[tuple] = [
    ("diagnosis", "Confirmed diagnosis (indication)", "clinical"),
    ("active_disease", "Documented active disease", "clinical"),
    ("prior_therapy", "Prior conventional therapy tried (step therapy)", "clinical"),
    ("supporting_evidence", "Evidence supporting the requested therapy", "reference"),
]


# --- grounded packet inputs (pure) -------------------------------------------

def select_prior_auth_drug(index: Dict[str, MedicationRecord]) -> Optional[str]:
    """The drug that needs prior authorisation = the biologic in the grounded med set.

    Curated class knowledge (biologics are the specialty drugs payers gate); the set of
    candidates is only ever the patient's own documented medications. Deterministic: if
    more than one biologic is present, the alphabetically-first canonical key is chosen."""
    biologics = sorted(k for k, r in index.items() if "biologic" in r.classes)
    return biologics[0] if biologics else None


def prior_therapy_drugs(index: Dict[str, MedicationRecord], requested: str) -> List[str]:
    """Conventional immunosuppressants already documented (the step-therapy set): every
    drug tagged immunosuppressant except the requested biologic itself."""
    return sorted(
        k for k, r in index.items()
        if "immunosuppressant" in r.classes and k != requested
    )


def confirmed_condition_display(nodes: List[dict]) -> Optional[str]:
    """Original-cased name of the *most recently confirmed* Condition node — the
    definitive diagnosis, which is the indication the specialty therapy targets.

    A diagnostic odyssey leaves several Condition nodes carrying a 'confirmed' status
    (earlier mislabels/comorbidities — e.g. an early 'iron deficiency' and later
    'hypertension' — each 'confirmed' in its day). The definitive diagnosis is the
    latest-dated confirmed condition, so we pick by date rather than graph order.
    Deterministic; undated confirmed nodes lose to any dated one."""
    confirmed: List[tuple] = []
    for n in nodes:
        if node_type(n) != "Condition":
            continue
        status = str(node_props(n).get("status") or "").lower()
        if "confirm" not in status:
            continue
        name = clinical_mention(n).strip()
        if not name:
            continue
        date = str(node_props(n).get("date") or "")
        confirmed.append((date, name))
    if not confirmed:
        return None
    confirmed.sort(key=lambda t: t[0])  # ISO dates sort lexically; "" sorts first (loses)
    return confirmed[-1][1]


# --- result container --------------------------------------------------------

@dataclass
class JustifyResult:
    packet: PriorAuthPacket
    narrative: Optional[Finding] = None
    session_id: Optional[str] = None
    clinical_dataset: Optional[str] = None
    reference_dataset: Optional[str] = None
    suppressed_uncited: List[str] = field(default_factory=list)

    @property
    def requested_drug(self) -> str:
        return self.packet.requested_drug

    @property
    def complete(self) -> bool:
        return self.packet.complete

    @property
    def missing_elements(self) -> List[str]:
        return self.packet.missing_elements

    def to_dict(self) -> dict:
        return {
            "clinical_dataset": self.clinical_dataset,
            "reference_dataset": self.reference_dataset,
            "session_id": self.session_id,
            "requested_drug": self.packet.requested_drug,
            "indication": self.packet.indication,
            "complete": self.packet.complete,
            "missing_elements": self.packet.missing_elements,
            "elements": [e.model_dump() for e in self.packet.elements],
            "narrative": self.narrative.model_dump() if self.narrative else None,
            "suppressed_uncited": self.suppressed_uncited,
        }


class JustifyAgent(BaseAgent):
    name = "justify"
    kind = FindingKind.justify
    APPLY_NO_DIAGNOSIS_LINT = False  # reports the documented, cited diagnosis (like Briefing)

    # --- element queries (grounded in the requested drug + indication) -------
    def _element_query(self, key: str, *, drug: str, indication: str,
                       prior: List[str]) -> str:
        drug_name = drug_display(drug)
        ind = indication or "the patient's confirmed condition"
        if key == "diagnosis":
            return ("What is this patient's confirmed diagnosis and on what date was it "
                    "confirmed? Cite the source note.")
        if key == "active_disease":
            return (
                f"Summarise the documented evidence of ACTIVE {ind} with vascular "
                "involvement (persistently raised ESR/CRP, absent or diminished pulse, "
                "limb claudication, inter-arm blood-pressure difference, renal-artery "
                "stenosis). Cite the source notes.")
        if key == "prior_therapy":
            prior_names = ", ".join(drug_display(p) for p in prior) or \
                "conventional first-line immunosuppressants"
            return (
                f"Which conventional first-line therapies ({prior_names}) has this patient "
                f"already been treated with before or alongside {drug_name}, as documented "
                "in the record? Cite the source notes.")
        # supporting_evidence -> reference brain
        return (
            f"What evidence in memory supports {drug_name} for active {ind}? "
            "Cite the source.")

    def _narrative_query(self, *, drug: str, indication: str, prior: List[str]) -> str:
        drug_name = drug_display(drug)
        ind = indication or "the patient's confirmed condition"
        prior_names = ", ".join(drug_display(p) for p in prior) or \
            "conventional first-line therapy"
        return (
            f"For a prior-authorisation request for {drug_name} in this patient with {ind}, "
            "summarise the medical-necessity rationale strictly from the documented record: "
            f"the confirmed diagnosis, the active disease, and the conventional therapies "
            f"already used ({prior_names}). Cite the source notes and report documented "
            "facts only.")

    async def run(self, *, narrative: bool = True) -> JustifyResult:
        clinical = self.clinical_brain()
        reference = self.reference_brain("trials")
        if not reference or not reference.get("name"):
            raise AgentError(
                "no active reference_trials brain — run "
                "`python -m app.seed.ingest_reference --only trials`")
        session_id = self.new_session_id("run")
        client = await self.client()

        # grounded packet inputs
        clinical_graph = await client.dataset_graph(clinical["id"])
        cnodes, _ = nodes_edges(clinical_graph)
        index = build_medication_index(cnodes)
        requested = select_prior_auth_drug(index)
        indication = confirmed_condition_display(cnodes) or ""
        prior = prior_therapy_drugs(index, requested) if requested else []

        packet = PriorAuthPacket(
            patient_id=self.patient_id,
            requested_drug=drug_display(requested) if requested else "",
            indication=indication,
        )
        suppressed: List[str] = []

        if requested is None:
            # nothing to justify — no biologic documented; return an empty (incomplete) packet
            return JustifyResult(
                packet=packet, narrative=None, session_id=session_id,
                clinical_dataset=clinical["name"], reference_dataset=reference["name"],
                suppressed_uncited=["no-prior-auth-drug"])

        for key, label, source in REQUIRED_ELEMENTS:
            dataset = clinical["name"] if source == "clinical" else reference["name"]
            query = self._element_query(key, drug=requested, indication=indication,
                                        prior=prior)
            refs, answer = await self._element_recall(
                query, dataset, f"{session_id}-{key}")
            element = PriorAuthElement(key=key, label=label, source=source)
            if refs and answer:
                score = self.score_from_citations(refs)
                finding = self.make_finding(
                    summary=answer, evidence=refs,
                    confidence=self.confidence_band(score), confidence_score=score,
                    session_id=session_id)
                if finding is not None:
                    element.content = answer
                    element.evidence = refs
                    element.satisfied = True
                    packet.findings.append(finding)
            if not element.satisfied:
                suppressed.append(key)  # citation-required: leave unsatisfied, surface as missing
            packet.elements.append(element)

        narrative_finding = None
        if narrative:
            narrative_finding = await self._narrative(
                clinical["name"], session_id, drug=requested, indication=indication,
                prior=prior)

        return JustifyResult(
            packet=packet, narrative=narrative_finding, session_id=session_id,
            clinical_dataset=clinical["name"], reference_dataset=reference["name"],
            suppressed_uncited=suppressed)

    async def _element_recall(self, query: str, dataset_name: str,
                              session_id: str) -> tuple:
        try:
            parsed = await self.recall(
                query, datasets=[dataset_name], session_id=session_id,
                query_type="GRAPH_COMPLETION", include_references=True)
        except Exception:
            return [], ""
        return parsed.references, (parsed.answer or "").strip()

    async def _narrative(self, dataset_name: str, session_id: str, *, drug: str,
                         indication: str, prior: List[str]) -> Optional[Finding]:
        try:
            parsed = await self.recall(
                self._narrative_query(drug=drug, indication=indication, prior=prior),
                datasets=[dataset_name], session_id=f"{session_id}-summary",
                query_type="GRAPH_COMPLETION", include_references=True)
        except Exception:
            return None
        if not (parsed.answer or "").strip():
            return None
        score = self.score_from_citations(parsed.references)
        return self.make_finding(
            summary=parsed.answer.strip(), evidence=parsed.references,
            confidence=self.confidence_band(score), confidence_score=score,
            session_id=session_id)

    async def run_and_close(self, *, narrative: bool = True) -> JustifyResult:
        try:
            return await self.run(narrative=narrative)
        finally:
            await self.aclose()
