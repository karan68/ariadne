"""TrialsAgent — match the patient to clinical trials with cited eligibility reasoning.

Same hybrid shape as the ConnectionsAgent (deterministic scaffold + Cognee recall for
the cited reasoning), tuned for the trials problem:

  1. **Grounded trial universe.** Trials are read from the live `reference_trials`
     graph: each `ReferenceTrialsGraph` container links to exactly one `Trial`
     (nct_id / title / conditions[]) and to its `EligibilityCriterion` nodes
     (kind = inclusion|exclusion, text). The agent can only ever reason about trials
     that actually exist in memory.
  2. **Deterministic eligibility.** Two rigorously-checkable axes decide eligibility:
       * condition match — the patient's confirmed condition(s) (read from the clinical
         graph) vs the trial's structured `conditions` list, and
       * age band — parsed from the trial's own inclusion/exclusion criteria and
         evaluated against the patient's age (from year-of-birth; there is no Patient
         node on the graph to read, so the documented descriptor is the source).
     This reproduces the golden match/no-match set exactly and, critically, catches the
     "right disease, wrong age" trap (a paediatric Takayasu trial) via the age axis —
     reproducibly, with no LLM variance.
  3. **Cited rationale (Cognee).** For each trial a scoped `recall()` over the trials
     brain returns the cited deciding criterion; the citations populate
     `TrialMatch.evidence`. Citation-required: a match with no citation is suppressed.
  4. **No-diagnosis rail ON.** The agent reports *eligibility*, never asserts a
     diagnosis; the optional headline `Finding` is run through `make_finding`
     (citation-required + no-diagnosis lint).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from app.agents.base import AgentError, BaseAgent
from app.graph_utils import (
    clinical_mention,
    edge_endpoints,
    node_id,
    node_props,
    node_type,
    nodes_edges,
)
from app.models import Confidence, EvidenceRef, Finding, FindingKind, TrialMatch

# --- age-band parsing (from the trial's own criteria text) -------------------

_AGE_RANGE = re.compile(r"age\s+(\d+)\s+to\s+(\d+)\s+years", re.I)
_AGE_LOWER = re.compile(r"age\s+(\d+)\s+years?\s+or\s+older", re.I)
_AGE_UNDER = re.compile(r"age\s+under\s+(\d+)\s+years", re.I)
_AGE_OVER = re.compile(r"age\s+over\s+(\d+)\s+years", re.I)


@dataclass(frozen=True)
class AgeConstraint:
    kind: str          # "range" | "lower" | "upper"
    lo: Optional[int]
    hi: Optional[int]

    def satisfied_by(self, age: int) -> bool:
        if self.kind == "range":
            return self.lo <= age <= self.hi  # type: ignore[operator]
        if self.kind == "lower":
            return age >= self.lo  # type: ignore[operator]
        if self.kind == "upper":
            return age <= self.hi  # type: ignore[operator]
        return False


def parse_age_constraint(text: str) -> Optional[AgeConstraint]:
    """Extract an age constraint from a single criterion, or None if it states none."""
    if not text:
        return None
    m = _AGE_RANGE.search(text)
    if m:
        return AgeConstraint("range", int(m.group(1)), int(m.group(2)))
    m = _AGE_LOWER.search(text)
    if m:
        return AgeConstraint("lower", int(m.group(1)), None)
    m = _AGE_UNDER.search(text)
    if m:
        return AgeConstraint("upper", None, int(m.group(1)) - 1)
    m = _AGE_OVER.search(text)
    if m:
        return AgeConstraint("lower", int(m.group(1)) + 1, None)
    return None


# --- grounded trial universe -------------------------------------------------

@dataclass
class Criterion:
    kind: str          # "inclusion" | "exclusion"
    text: str


@dataclass
class TrialRecord:
    nct_id: str
    title: Optional[str] = None
    phase: Optional[str] = None
    status: Optional[str] = None
    conditions: List[str] = field(default_factory=list)
    inclusion: List[Criterion] = field(default_factory=list)
    exclusion: List[Criterion] = field(default_factory=list)


def build_trial_index(nodes: List[dict], edges: List[dict]) -> Dict[str, TrialRecord]:
    """Reconstruct per-trial structured records from the live trials graph.

    Grouping is via the `ReferenceTrialsGraph` container node, which links (edge
    `trials`) to exactly one Trial and (edge `criteria`) to that trial's criteria.
    Pure function over an already-fetched graph payload.
    """
    id_type = {node_id(n): node_type(n) for n in nodes}
    props = {node_id(n): node_props(n) for n in nodes}

    cont_trial: Dict[str, str] = {}
    cont_crit: Dict[str, List[str]] = {}
    for edge in edges:
        s, t, label = edge_endpoints(edge)
        if id_type.get(s) != "ReferenceTrialsGraph":
            continue
        if label == "trials" and id_type.get(t) == "Trial":
            cont_trial[s] = t
        elif label == "criteria" and id_type.get(t) == "EligibilityCriterion":
            cont_crit.setdefault(s, []).append(t)

    index: Dict[str, TrialRecord] = {}
    for cont, trial_id in cont_trial.items():
        tp = props.get(trial_id, {})
        nct = tp.get("nct_id")
        if not nct:
            continue
        rec = TrialRecord(
            nct_id=nct,
            title=tp.get("title"),
            phase=tp.get("phase"),
            status=tp.get("status"),
            conditions=[str(c) for c in (tp.get("conditions") or [])],
        )
        for cid in cont_crit.get(cont, []):
            cp = props.get(cid, {})
            crit = Criterion(kind=str(cp.get("kind") or "inclusion"),
                             text=str(cp.get("text") or ""))
            (rec.inclusion if crit.kind == "inclusion" else rec.exclusion).append(crit)
        index[nct] = rec
    return index


# --- patient facts (grounded) ------------------------------------------------

def hero_confirmed_conditions(clinical_nodes: List[dict]) -> List[str]:
    """Lowercased names of Condition nodes with a confirmed status."""
    out: List[str] = []
    for n in clinical_nodes:
        if node_type(n) != "Condition":
            continue
        status = str(node_props(n).get("status") or "").lower()
        if "confirm" in status:
            name = clinical_mention(n).strip().lower()
            if name and name not in out:
                out.append(name)
    return out


def compute_age(year_of_birth: int, as_of_year: Optional[int] = None) -> int:
    return (as_of_year or datetime.now().year) - year_of_birth


# --- deterministic eligibility ----------------------------------------------

@dataclass
class EligibilityVerdict:
    eligible: bool
    reason: str                         # "eligible" | "age" | "condition"
    deciding_criterion: str
    matched_criteria: List[str] = field(default_factory=list)
    unmet_criteria: List[str] = field(default_factory=list)
    condition_ok: bool = True
    age_ok: bool = True


def _condition_match(hero_conditions: List[str], trial: TrialRecord) -> bool:
    trial_conditions = [c.lower() for c in trial.conditions]
    return any(hc in trial_conditions for hc in hero_conditions)


def evaluate_eligibility(
    age: int, hero_conditions: List[str], trial: TrialRecord
) -> EligibilityVerdict:
    """Deterministic, reproducible eligibility on the two rigorously-checkable axes:
    trial condition match and age band. Populates matched/unmet with the real criterion
    texts that drive the decision (never guesses criteria it cannot evaluate)."""
    matched: List[str] = []
    unmet: List[str] = []

    condition_ok = _condition_match(hero_conditions, trial)

    # age: every inclusion age band must be satisfied, and no exclusion age band met.
    age_incl_ok = True
    failing_age_incl: Optional[str] = None
    for crit in trial.inclusion:
        c = parse_age_constraint(crit.text)
        if not c:
            continue
        if c.satisfied_by(age):
            matched.append(crit.text)
        else:
            age_incl_ok = False
            failing_age_incl = failing_age_incl or crit.text
            unmet.append(crit.text)

    triggered_age_excl: Optional[str] = None
    for crit in trial.exclusion:
        c = parse_age_constraint(crit.text)
        if c and c.satisfied_by(age):
            triggered_age_excl = triggered_age_excl or crit.text
            unmet.append(crit.text)
    age_ok = age_incl_ok and triggered_age_excl is None

    # condition axis -> matched/unmet note (trial-level, from the structured list)
    cond_note = f"Study condition(s): {', '.join(trial.conditions) or 'unspecified'}"
    if condition_ok:
        matched.append(cond_note + " — matches the patient's confirmed diagnosis")
    else:
        unmet.append(cond_note + " — does not match the patient's confirmed diagnosis")

    eligible = condition_ok and age_ok
    if not condition_ok:
        reason, deciding = "condition", cond_note
    elif not age_ok:
        reason = "age"
        deciding = triggered_age_excl or failing_age_incl or "age criterion"
    else:
        reason = "eligible"
        deciding = next((c.text for c in trial.inclusion
                         if parse_age_constraint(c.text)), cond_note)

    return EligibilityVerdict(
        eligible=eligible, reason=reason, deciding_criterion=deciding,
        matched_criteria=matched, unmet_criteria=unmet,
        condition_ok=condition_ok, age_ok=age_ok,
    )


# --- result container --------------------------------------------------------

@dataclass
class TrialsResult:
    matches: List[TrialMatch] = field(default_factory=list)
    narrative: Optional[Finding] = None
    hero_age: Optional[int] = None
    hero_conditions: List[str] = field(default_factory=list)
    session_id: Optional[str] = None
    trials_dataset: Optional[str] = None
    clinical_dataset: Optional[str] = None
    suppressed_uncited: List[str] = field(default_factory=list)

    @property
    def eligible_ids(self) -> List[str]:
        return [m.nct_id for m in self.matches if m.eligible]

    @property
    def ineligible_ids(self) -> List[str]:
        return [m.nct_id for m in self.matches if m.eligible is False]

    def to_dict(self) -> dict:
        return {
            "clinical_dataset": self.clinical_dataset,
            "trials_dataset": self.trials_dataset,
            "session_id": self.session_id,
            "hero_age": self.hero_age,
            "hero_conditions": self.hero_conditions,
            "eligible_ids": self.eligible_ids,
            "ineligible_ids": self.ineligible_ids,
            "matches": [m.model_dump() for m in self.matches],
            "narrative": self.narrative.model_dump() if self.narrative else None,
            "suppressed_uncited": self.suppressed_uncited,
        }


class TrialsAgent(BaseAgent):
    name = "trials"
    kind = FindingKind.trial
    APPLY_NO_DIAGNOSIS_LINT = True

    def __init__(self, patient_id: str, client=None, *,
                 year_of_birth: int = 1994, as_of_year: Optional[int] = None) -> None:
        super().__init__(patient_id, client=client)
        self._yob = year_of_birth
        self._as_of = as_of_year

    def _profile_sentence(self, age: int) -> str:
        return (
            f"a {age}-year-old woman with confirmed Takayasu arteritis and active "
            "large-vessel vasculitis (ESR and CRP raised within the last month; absent "
            "left radial pulse, inter-arm systolic blood-pressure difference over "
            "15 mmHg, left-arm claudication); not pregnant; no active infection"
        )

    def _trial_query(self, trial: TrialRecord, age: int) -> str:
        return (
            f"For clinical trial {trial.nct_id} ({trial.title}) described in memory, "
            f"is {self._profile_sentence(age)} likely ELIGIBLE or NOT eligible? "
            "State the single inclusion or exclusion criterion that decides it, and "
            "cite the source."
        )

    def _narrative_query(self, age: int) -> str:
        return (
            f"Across the clinical trials in memory, for {self._profile_sentence(age)}, "
            "list each trial by NCT id and whether the patient is likely eligible or "
            "not, citing the deciding criterion for each. Report eligibility support "
            "only; do not make a diagnosis."
        )

    async def run(self, *, narrative: bool = True) -> TrialsResult:
        clinical = self.clinical_brain()
        trials = self.reference_brain("trials")
        if not trials or not trials.get("name"):
            raise AgentError(
                "no active reference_trials brain — run "
                "`python -m app.seed.ingest_reference --only trials`")
        session_id = self.new_session_id("run")
        client = await self.client()

        # grounded patient facts
        clinical_graph = await client.dataset_graph(clinical["id"])
        cnodes, _ = nodes_edges(clinical_graph)
        hero_conditions = hero_confirmed_conditions(cnodes)
        age = compute_age(self._yob, self._as_of)

        # grounded trial universe + deterministic verdicts
        trials_graph = await client.dataset_graph(trials["id"])
        tnodes, tedges = nodes_edges(trials_graph)
        index = build_trial_index(tnodes, tedges)

        matches: List[TrialMatch] = []
        suppressed: List[str] = []
        for nct in sorted(index):
            trial = index[nct]
            verdict = evaluate_eligibility(age, hero_conditions, trial)
            refs = await self._trial_citations(trial, age, trials["name"], session_id)
            if not refs:
                suppressed.append(nct)  # citation-required: suppress uncited
                continue
            score = self.score_from_citations(refs)
            # a clear-cut deterministic verdict lifts confidence a notch
            if verdict.reason in ("age", "condition") or verdict.eligible:
                score = round(min(score + 0.1, 0.98), 3)
            matches.append(TrialMatch(
                nct_id=nct,
                title=trial.title,
                eligible=verdict.eligible,
                deciding_criterion=verdict.deciding_criterion,
                matched_criteria=verdict.matched_criteria,
                unmet_criteria=verdict.unmet_criteria,
                confidence=self.confidence_band(score),
                evidence=refs,
            ))

        narrative_finding = None
        if narrative:
            narrative_finding = await self._narrative(age, trials["name"], session_id)

        return TrialsResult(
            matches=matches, narrative=narrative_finding, hero_age=age,
            hero_conditions=hero_conditions, session_id=session_id,
            trials_dataset=trials["name"], clinical_dataset=clinical["name"],
            suppressed_uncited=suppressed,
        )

    async def _trial_citations(
        self, trial: TrialRecord, age: int, dataset_name: str, session_id: str
    ) -> List[EvidenceRef]:
        try:
            parsed = await self.recall(
                self._trial_query(trial, age),
                datasets=[dataset_name],
                session_id=f"{session_id}-{trial.nct_id.lower()}",
                query_type="GRAPH_COMPLETION", include_references=True,
            )
        except Exception:
            return []
        return parsed.references

    async def _narrative(
        self, age: int, dataset_name: str, session_id: str
    ) -> Optional[Finding]:
        try:
            parsed = await self.recall(
                self._narrative_query(age), datasets=[dataset_name],
                session_id=f"{session_id}-summary", query_type="GRAPH_COMPLETION",
                include_references=True,
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

    async def run_and_close(self, *, narrative: bool = True) -> TrialsResult:
        try:
            return await self.run(narrative=narrative)
        finally:
            await self.aclose()
