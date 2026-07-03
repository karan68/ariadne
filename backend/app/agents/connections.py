"""ConnectionsAgent — differential *support* by connecting the patient's phenotype
constellation to cited medical-literature patterns.

The hard clinical reasoning (discriminating Takayasu arteritis from its mimics) is
done by Cognee's hybrid graph+vector `recall()` over the curated `reference_literature`
brain — that is the "Best Use of Cognee" story. Around that, this agent adds the
rigour that makes the output trustworthy decision support rather than an LLM guess:

  1. **Deterministic phenotype.** The patient's symptom nodes are normalized to an HPO
     term set (`normalize.resolve_hpo_set`), so matching is phenotype-driven, not fuzzy
     text. No dates or free text are invented.
  2. **Grounded candidate universe.** Candidates are limited to the conditions that
     actually exist as cited `LiteraturePattern` nodes in memory (read from the
     reference graph). The agent can never surface a condition the literature brain
     doesn't hold — a real anti-hallucination guardrail.
  3. **Reproducible ranking.** Candidates are ranked by a deterministic
     phenotype-overlap score with the large-vessel discriminators weighted, so the
     ranking is stable across runs (no LLM variance) and testable offline.
  4. **Citation-required per candidate.** Each surfaced candidate gets its own scoped
     recall; if that recall returns no citation, the candidate is *suppressed* (never
     shown uncited). Each evidence-path hop must carry >=1 source or it is dropped.
  5. **No-diagnosis rail.** `APPLY_NO_DIAGNOSIS_LINT = True`; summaries are framed as
     "consider / investigate", and `make_finding` suppresses anything assertive.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from app.agents.base import AgentError, BaseAgent
from app.graph_utils import clinical_mention, mentions_by_type, node_props, node_type, nodes_edges
from app.models import Confidence, EvidenceHop, EvidencePath, EvidenceRef, Finding, FindingKind
from app.normalize import Normalizer, hpo_display_map

# Large-vessel / vascular HPO terms — the features that discriminate a large-vessel
# vasculitis (Takayasu) from the constitutional-symptom mimics (lymphoma, SLE, IE,
# Still's). Weighted heavily in the overlap score so the ranking is clinically sound.
VASCULAR_HPO: Set[str] = {
    "HP:0004417",  # Intermittent claudication
    "HP:0025153",  # Reduced pulse
    "HP:0031955",  # Arterial bruit
    "HP:0031664",  # Blood pressure difference between arms
    "HP:0500015",  # Cold extremities
    "HP:0000822",  # Hypertension (renovascular)
}
VASCULAR_WEIGHT = 3


# --- pure, testable building blocks -----------------------------------------

@dataclass
class CandidateScore:
    """A ranked literature condition with the phenotype evidence behind its rank."""

    condition: str
    score: int
    overlap: List[str] = field(default_factory=list)          # matched HPO ids
    vascular: List[str] = field(default_factory=list)         # subset of overlap
    matched_features: List[str] = field(default_factory=list)  # human-readable
    vascular_features: List[str] = field(default_factory=list)
    n_pattern_features: int = 0

    def to_dict(self) -> dict:
        return {
            "condition": self.condition,
            "score": self.score,
            "overlap_count": len(self.overlap),
            "matched_features": self.matched_features,
            "vascular_features": self.vascular_features,
            "n_pattern_features": self.n_pattern_features,
        }


def patient_phenotype(nodes: List[dict], normalizer: Normalizer) -> "tuple[List[str], List[str]]":
    """Return (unique_symptom_displays, hpo_ids) from the clinical graph nodes.

    Deterministic: symptom mentions -> normalized HPO terms. Unmatched mentions
    (e.g. pertinent negatives like "No chest pain") are simply dropped, never guessed.
    """
    mentions = mentions_by_type(nodes).get("Symptom", [])
    displays: "OrderedDict[str, None]" = OrderedDict()
    hpo: List[str] = []
    for m in mentions:
        code = normalizer.normalize(m, "symptom")
        if not code:
            continue
        displays.setdefault(code.display, None)
        if code.code not in hpo:
            hpo.append(code.code)
    return list(displays), hpo


def build_candidate_index(
    literature_nodes: List[dict], normalizer: Normalizer
) -> Dict[str, dict]:
    """Group LiteraturePattern nodes by their `condition` property into a candidate
    index: condition -> {"features": [...], "hpo": set(...)}.

    This is the *grounded universe* — only conditions that exist as cited patterns in
    the reference brain can ever be surfaced.
    """
    index: Dict[str, dict] = {}
    for n in literature_nodes:
        if node_type(n) != "LiteraturePattern":
            continue
        props = node_props(n)
        condition = props.get("condition")
        if not condition:
            continue
        feats = [f for f in (props.get("features") or []) if isinstance(f, str)]
        entry = index.setdefault(condition, {"features": [], "hpo": set()})
        for f in feats:
            entry["features"].append(f)
            code = normalizer.normalize(f, "symptom")
            if code:
                entry["hpo"].add(code.code)
    return index


def rank_candidates(
    patient_hpo: Set[str], index: Dict[str, dict], hpo_display: Dict[str, str]
) -> List[CandidateScore]:
    """Deterministically rank candidate conditions by phenotype overlap.

    score = |overlap| + VASCULAR_WEIGHT * |overlap ∩ vascular discriminators|.
    Stable tie-break by score desc, then overlap size desc, then condition name.
    """
    patient_hpo = set(patient_hpo)
    scores: List[CandidateScore] = []
    for condition, entry in index.items():
        cand_hpo: Set[str] = entry["hpo"]
        overlap = sorted(patient_hpo & cand_hpo)
        vascular = sorted(set(overlap) & VASCULAR_HPO)
        score = len(overlap) + VASCULAR_WEIGHT * len(vascular)
        scores.append(CandidateScore(
            condition=condition,
            score=score,
            overlap=overlap,
            vascular=vascular,
            matched_features=[hpo_display.get(h, h) for h in overlap],
            vascular_features=[hpo_display.get(h, h) for h in vascular],
            n_pattern_features=len(entry["features"]),
        ))
    scores.sort(key=lambda c: (-c.score, -len(c.overlap), c.condition))
    return scores


def _candidate_summary(cand: CandidateScore) -> str:
    """Suggestive, decision-support phrasing (must pass the no-diagnosis lint)."""
    n = len(cand.overlap)
    feats = ", ".join(cand.matched_features[:6]) or "constitutional features"
    parts = [
        f"Consider {cand.condition}: the patient's constellation overlaps {n} "
        f"characteristic feature(s) of this literature pattern ({feats})."
    ]
    if cand.vascular_features:
        parts.append(
            "Notably it accounts for the large-vessel signs "
            f"({', '.join(cand.vascular_features)}), which the constitutional-symptom "
            "mimics do not."
        )
    parts.append(
        "Investigate and confirm independently — this is decision support, not a diagnosis."
    )
    return " ".join(parts)


def _build_path(cand: CandidateScore, refs: List[EvidenceRef]) -> Optional[EvidencePath]:
    """One or two cited hops (patient phenotype -> pattern, pattern -> discriminator).

    Every hop must carry >=1 EvidenceRef or it is dropped; an empty path -> None.
    """
    if not refs:
        return None
    subject = "patient phenotype: " + (", ".join(cand.matched_features[:4]) or "constellation")
    hops: List[EvidenceHop] = [
        EvidenceHop(subject=subject, relation="matches_literature_pattern",
                    object=cand.condition, evidence=list(refs)),
    ]
    if cand.vascular_features:
        hops.append(EvidenceHop(
            subject=cand.condition, relation="distinguished_by",
            object="large-vessel signs (" + ", ".join(cand.vascular_features) + ")",
            evidence=list(refs),
        ))
    hops = [h for h in hops if h.evidence]
    return EvidencePath(hops=hops) if hops else None


# --- result container --------------------------------------------------------

@dataclass
class ConnectionsResult:
    candidates: List[Finding] = field(default_factory=list)
    ranking: List[dict] = field(default_factory=list)
    narrative: Optional[Finding] = None
    patient_hpo: List[str] = field(default_factory=list)
    constellation: List[str] = field(default_factory=list)
    session_id: Optional[str] = None
    literature_dataset: Optional[str] = None
    clinical_dataset: Optional[str] = None

    @property
    def top_condition(self) -> Optional[str]:
        return self.ranking[0]["condition"] if self.ranking else None

    def to_dict(self) -> dict:
        return {
            "clinical_dataset": self.clinical_dataset,
            "literature_dataset": self.literature_dataset,
            "session_id": self.session_id,
            "patient_hpo": self.patient_hpo,
            "constellation": self.constellation,
            "top_condition": self.top_condition,
            "ranking": self.ranking,
            "candidates": [c.model_dump() for c in self.candidates],
            "narrative": self.narrative.model_dump() if self.narrative else None,
        }


class ConnectionsAgent(BaseAgent):
    name = "connections"
    kind = FindingKind.connection
    # Suggestive agent: it proposes candidates to *investigate*, never asserts a dx.
    APPLY_NO_DIAGNOSIS_LINT = True

    def __init__(self, patient_id: str, client=None) -> None:
        super().__init__(patient_id, client=client)
        self._normalizer = Normalizer()
        self._hpo_display = hpo_display_map()

    def _constellation_query(self, features: List[str]) -> str:
        feat = ", ".join(features) if features else "a multi-system constellation"
        return (
            f"A patient presents with this constellation of findings: {feat}. "
            "Based on the medical literature in memory, which conditions best explain "
            "it, and what distinguishes the most likely one from its mimics? "
            "Rank the candidates and cite sources."
        )

    @staticmethod
    def _candidate_query(condition: str) -> str:
        return (
            "Based on the medical literature in memory, what are the characteristic "
            f"features of {condition}, and which findings distinguish it from other "
            "large-vessel vasculitis mimics? Cite sources."
        )

    async def run(self, top_k: int = 3) -> ConnectionsResult:
        clinical = self.clinical_brain()
        lit = self.reference_brain("literature")
        if not lit or not lit.get("name"):
            raise AgentError(
                "no active reference_literature brain — run "
                "`python -m app.seed.ingest_reference --only literature`")
        session_id = self.new_session_id("run")

        client = await self.client()
        # 1) deterministic phenotype from the patient's clinical graph
        clinical_graph = await client.dataset_graph(clinical["id"])
        cnodes, _ = nodes_edges(clinical_graph)
        constellation, hpo = patient_phenotype(cnodes, self._normalizer)

        # 2) grounded candidate universe from the literature graph + reproducible rank
        lit_graph = await client.dataset_graph(lit["id"])
        lnodes, _ = nodes_edges(lit_graph)
        index = build_candidate_index(lnodes, self._normalizer)
        ranked = rank_candidates(set(hpo), index, self._hpo_display)

        # 3) cited discrimination narrative (Cognee hybrid recall over the literature)
        narrative = await self._narrative(constellation, lit["name"], session_id)

        # 4) per-candidate cited Findings (citation-required; suppress the uncited)
        candidates: List[Finding] = []
        for cand in ranked[: max(0, top_k)]:
            finding = await self._candidate_finding(cand, lit["name"], session_id)
            if finding is not None:
                candidates.append(finding)

        return ConnectionsResult(
            candidates=candidates,
            ranking=[c.to_dict() for c in ranked],
            narrative=narrative,
            patient_hpo=sorted(hpo),
            constellation=constellation,
            session_id=session_id,
            literature_dataset=lit["name"],
            clinical_dataset=clinical["name"],
        )

    async def _narrative(
        self, constellation: List[str], dataset_name: str, session_id: str
    ) -> Optional[Finding]:
        try:
            parsed = await self.recall(
                self._constellation_query(constellation),
                datasets=[dataset_name], session_id=f"{session_id}-differential",
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

    async def _candidate_finding(
        self, cand: CandidateScore, dataset_name: str, session_id: str
    ) -> Optional[Finding]:
        try:
            parsed = await self.recall(
                self._candidate_query(cand.condition),
                datasets=[dataset_name],
                session_id=f"{session_id}-{cand.condition[:24].replace(' ', '_').lower()}",
                query_type="GRAPH_COMPLETION", include_references=True,
            )
        except Exception:
            return None
        if not parsed.references:
            return None  # citation-required: no cited support -> suppress
        score = self.score_from_citations(parsed.references)
        # Blend the phenotype-overlap signal into the confidence band so a strong,
        # vascular-discriminating match (Takayasu) reads higher than a weak one.
        score = round(min(score + 0.05 * len(cand.vascular), 0.98), 3)
        return self.make_finding(
            summary=_candidate_summary(cand),
            evidence=parsed.references,
            confidence=self.confidence_band(score),
            confidence_score=score,
            session_id=session_id,
            path=_build_path(cand, parsed.references),
        )

    async def run_and_close(self, top_k: int = 3) -> ConnectionsResult:
        try:
            return await self.run(top_k=top_k)
        finally:
            await self.aclose()
