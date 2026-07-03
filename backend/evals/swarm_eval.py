"""Swarm eval harness — the consolidated, metric-scored P2 gate.

Where `p2_eval` asserts per-agent boolean checks one agent at a time, this harness
loads the labeled cases in `evals/cases/swarm.json` and scores each agent against
**quantitative metrics with thresholds**, producing a single scorecard:

  * temporal-ordering accuracy (Timeline)
  * precision@1 + HPO-match recall (Connections)
  * eligibility precision/recall (Trials)
  * signal-detection recall (Safety)
  * grounding correctness + element completeness (Justify)
  * citation-coverage == 100% and no-diagnosis-lint == 0 across the whole swarm

Two paths, same case file:
  * OFFLINE (`--offline`, CI-safe, no cloud): deterministic metrics computed from the
    agents' pure functions over small self-contained fixtures.
  * LIVE: runs all six agents once against the active hero brains and scores their real
    outputs, adding the citation/lint invariants that need real recalls.

Reuses the Check/EvalResult contract so it plugs into `run_evals` as `--phase swarm`.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List

from evals import metrics as M
from evals.p1_eval import Check, EvalResult

from app.agents.timeline import TimelineAgent, build_timeline_events
from app.agents.connections import (
    ConnectionsAgent,
    build_candidate_index,
    patient_phenotype,
    rank_candidates,
)
from app.agents.trials import (
    TrialsAgent,
    build_trial_index,
    compute_age,
    evaluate_eligibility,
)
from app.agents.briefing import BriefingAgent, select_highlights
from app.agents.safety import (
    SafetyAgent,
    build_medication_index,
    detect_duplications,
    detect_interactions,
)
from app.agents.justify import (
    JustifyAgent,
    confirmed_condition_display,
    select_prior_auth_drug,
)
from app.models import TimelineEvent, find_diagnosis_language
from app.normalize import Normalizer, hpo_display_map
from app.seed.odyssey_patient import GOLDEN, HERO_PATIENT
from app.seed.reference_data import REFERENCE_GOLDEN

CASES_PATH = Path(__file__).resolve().parent / "cases" / "swarm.json"


def load_cases(path: Path = CASES_PATH) -> Dict[str, Any]:
    """Load and index the labeled swarm cases by agent id."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["by_agent"] = {c["agent"]: c for c in doc["cases"]}
    return doc


# --- offline fixtures (self-contained; mirror the live graph shapes) ---------

def _timeline_nodes() -> List[dict]:
    def n(t, label, **props):
        return {"id": uuid.uuid4().hex, "label": label, "type": t, "properties": props}
    return [
        n("Encounter", "General Medicine", date="2021-02-10", setting="General Medicine"),
        n("LabResult", "LabResult_x", date="2021-05-01", analyte="ESR", value="62",
          unit="mm/hr", flag="high"),
        n("Condition", "Post-viral fatigue", date="2021-06-01", status="suspected"),
        n("ImagingStudy", "CTA", date="2024-02-08", modality="CT",
          body_site="aorta", impression="large-vessel arteritis"),
        n("Condition", "Takayasu arteritis", date="2024-03-01", status="confirmed"),
        n("Medication", "Prednisolone", start="2024-03-05", prescriber="Dr. S. Menon"),
        n("Encounter", "Rheumatology", date="2024-09-15", setting="Rheumatology"),
        n("Symptom", "Fatigue", onset="3 months"),  # free-text date -> excluded from axis
    ]


def _conn_clinical_nodes() -> List[dict]:
    def n(t, label, **props):
        return {"id": uuid.uuid4().hex, "label": label, "type": t, "properties": props}
    return [
        n("Symptom", "Fatigue"), n("Symptom", "Low-grade fever"),
        n("Symptom", "Night sweats"), n("Symptom", "Unintentional weight loss"),
        n("Symptom", "Arthralgia of knees"),
        n("Symptom", "Upper-limb claudication"),   # vascular discriminator
        n("Symptom", "Left hand cold sensation"),  # vascular discriminator
        n("Symptom", "No chest pain"),              # pertinent negative -> dropped
    ]


def _conn_literature_nodes() -> List[dict]:
    def pat(cond, feats):
        return {"id": uuid.uuid4().hex, "label": "LiteraturePattern_" + uuid.uuid4().hex,
                "type": "LiteraturePattern",
                "properties": {"condition": cond, "features": feats, "source": "test"}}
    return [
        pat("Takayasu arteritis", ["fever", "night sweats", "unintentional weight loss",
            "fatigue", "arthralgia", "left-arm claudication", "diminished radial pulse",
            "subclavian bruit"]),
        pat("Lymphoma", ["recurrent fever", "drenching night sweats",
            "unintentional weight loss", "fatigue", "anaemia"]),
        pat("Systemic lupus erythematosus", ["fatigue", "fever", "arthralgia", "malar rash"]),
    ]


def _trials_graph() -> tuple:
    """Synthetic trials graph mirroring the live container-grouped shape.
    Golden partition for this fixture: NCT09000001 eligible; 004 (GCA) and 006
    (paediatric age trap) ineligible."""
    nodes, edges = [], []

    def node(t, **props):
        return {"id": uuid.uuid4().hex, "label": t + "_" + uuid.uuid4().hex,
                "type": t, "properties": props}

    def add(nct, conditions, incl, excl):
        cont = node("ReferenceTrialsGraph")
        trial = node("Trial", nct_id=nct, title=nct + " study", conditions=conditions)
        nodes.extend([cont, trial])
        edges.append({"source": cont["id"], "target": trial["id"], "label": "trials"})
        for text in incl:
            c = node("EligibilityCriterion", kind="inclusion", text=text)
            nodes.append(c)
            edges.append({"source": cont["id"], "target": c["id"], "label": "criteria"})
        for text in excl:
            c = node("EligibilityCriterion", kind="exclusion", text=text)
            nodes.append(c)
            edges.append({"source": cont["id"], "target": c["id"], "label": "criteria"})

    add("NCT09000001", ["Takayasu arteritis"],
        ["Age 18 to 65 years.", "Confirmed Takayasu arteritis."], ["Active infection."])
    add("NCT09000004", ["Giant cell arteritis"],
        ["Age 50 years or older."], ["Age under 50 years.", "Takayasu arteritis."])
    add("NCT09000006", ["Takayasu arteritis"],
        ["Age 5 to 17 years."], ["Age 18 years or older."])
    return nodes, edges, {"NCT09000001"}


def _safety_med_nodes() -> List[dict]:
    def n(label, prescriber=None, **props):
        p = dict(props)
        if prescriber:
            p["prescriber"] = prescriber
        return {"id": uuid.uuid4().hex, "label": label, "type": "Medication", "properties": p}
    return [
        n("Oral iron", "Dr. A. Sharma"), n("oral iron", "Dr. R. Iyer"),
        n("Amlodipine", "Dr. N. Das"), n("Ramipril", "Dr. N. Das"),
        n("prednisolone", "Dr. S. Menon"), n("methotrexate", "Dr. S. Menon"),
        n("aspirin", "Dr. S. Menon"), n("tocilizumab", "Dr. S. Menon"),
    ]


def _justify_nodes() -> List[dict]:
    def med(label, prescriber):
        return {"id": uuid.uuid4().hex, "label": label, "type": "Medication",
                "properties": {"prescriber": prescriber}}

    def cond(label, status, date):
        return {"id": uuid.uuid4().hex, "label": label, "type": "Condition",
                "properties": {"status": status, "date": date}}
    return [
        med("prednisolone", "Dr. S. Menon"), med("methotrexate", "Dr. S. Menon"),
        med("tocilizumab", "Dr. S. Menon"), med("aspirin", "Dr. S. Menon"),
        cond("iron deficiency", "confirmed", "2021-05-18"),
        cond("Hypertension", "confirmed", "2023-06-30"),
        cond("Takayasu arteritis", "confirmed", "2024-03-01"),
    ]


# --- deterministic (offline) metric scores -----------------------------------

def _offline_scores() -> Dict[str, Dict[str, float]]:
    scores: Dict[str, Dict[str, float]] = {}

    # Timeline
    events = build_timeline_events(_timeline_nodes())
    dx_date, dx_term = GOLDEN.get("true_diagnosis_date", "2024-03-01"), "takayasu"
    scores["timeline"] = {
        "temporal_ordering_accuracy": M.temporal_ordering_accuracy([e.date for e in events]),
        "dx_milestone_recall": 1.0 if any(
            e.date == dx_date and dx_term in e.description.lower() for e in events) else 0.0,
    }

    # Connections
    norm = Normalizer()
    _, hpo = patient_phenotype(_conn_clinical_nodes(), norm)
    index = build_candidate_index(_conn_literature_nodes(), norm)
    ranked = rank_candidates(set(hpo), index, hpo_display_map())
    ranked_conditions = [c.condition for c in ranked]
    scores["connections"] = {
        "precision_at_1": M.precision_at_k(ranked_conditions, {"Takayasu arteritis"}, 1),
    }

    # Trials
    t_nodes, t_edges, t_gold = _trials_graph()
    t_index = build_trial_index(t_nodes, t_edges)
    age = compute_age(int(HERO_PATIENT["year_of_birth"]), as_of_year=2026)
    hero_conditions = ["takayasu arteritis", "hypertension"]
    eligible = {nct for nct, rec in t_index.items()
                if evaluate_eligibility(age, hero_conditions, rec).eligible}
    scores["trials"] = {
        "eligibility_precision": M.set_precision(eligible, t_gold),
        "eligibility_recall": M.set_recall(eligible, t_gold),
    }

    # Safety
    s_index = build_medication_index(_safety_med_nodes())
    detected = {s.rule_id for s in detect_interactions(s_index)}
    detected |= {"duplication:" + d.canonical for d in detect_duplications(s_index)}
    gold_signals = {"antimetabolite-nsaid", "immunosuppressant-stack", "duplication:iron"}
    scores["safety"] = {
        "signal_detection_recall": M.set_recall(detected, gold_signals),
    }

    # Briefing (reuse the timeline fixture's confirmed-dx milestone)
    b_events = [
        TimelineEvent(date="2021-02-10", type="Encounter", description="General Medicine: fatigue"),
        TimelineEvent(date="2024-03-01", type="Condition", description="Takayasu arteritis [confirmed]"),
        TimelineEvent(date="2024-09-15", type="Encounter", description="Rheumatology follow-up"),
    ]
    highlights = select_highlights(b_events, recent=2)
    scores["briefing"] = {
        "dx_milestone_recall": 1.0 if any(
            e.date == dx_date and dx_term in e.description.lower() for e in highlights) else 0.0,
    }

    # Justify
    j_nodes = _justify_nodes()
    j_index = build_medication_index(j_nodes)
    drug = (select_prior_auth_drug(j_index) or "").lower()
    indication = (confirmed_condition_display(j_nodes) or "").lower()
    scores["justify"] = {
        "grounding_correctness": 1.0 if drug == "tocilizumab" and "takayasu" in indication else 0.0,
    }

    return scores


# --- live metric scores ------------------------------------------------------

def _lint_texts(texts: List[str]) -> int:
    return M.lint_violation_count(texts, find_diagnosis_language)


async def _live_scores(res: EvalResult) -> Dict[str, Dict[str, float]]:
    scores: Dict[str, Dict[str, float]] = {}
    pid = HERO_PATIENT["id"]
    dx_date, dx_term = GOLDEN.get("true_diagnosis_date", "2024-03-01"), "takayasu"
    cited_items: List[Any] = []   # every surfaced claim -> citation coverage
    lint_surfaces: List[str] = []  # suggestive-agent text -> no-diagnosis lint

    # Timeline
    agent = TimelineAgent(pid)
    try:
        tl = await agent.run()
    finally:
        await agent.aclose()
    scores["timeline"] = {
        "temporal_ordering_accuracy": M.temporal_ordering_accuracy([e.date for e in tl.events]),
        "dx_milestone_recall": 1.0 if any(
            e.date == dx_date and dx_term in e.description.lower() for e in tl.events) else 0.0,
        "event_count": float(len(tl.events)),
    }
    if tl.narrative:
        cited_items.append(tl.narrative)
    res.metrics["timeline_event_count"] = len(tl.events)

    # Connections
    agent = ConnectionsAgent(pid)
    try:
        cn = await agent.run(top_k=3)
    finally:
        await agent.aclose()
    ranked_conditions = [r["condition"] for r in cn.ranking]
    gold_hpo = set(GOLDEN["symptoms_hpo"].values())
    scores["connections"] = {
        "precision_at_1": M.precision_at_k(
            ranked_conditions, {REFERENCE_GOLDEN["literature_top_condition"]}, 1),
        "hpo_recall": M.set_recall(cn.patient_hpo, gold_hpo),
        "citation_coverage": M.citation_coverage(cn.candidates),
        "lint_violations": float(_lint_texts(
            [c.summary for c in cn.candidates]
            + ([cn.narrative.summary] if cn.narrative else []))),
    }
    cited_items.extend(cn.candidates)
    if cn.narrative:
        cited_items.append(cn.narrative)
    lint_surfaces.extend(c.summary for c in cn.candidates)
    if cn.narrative:
        lint_surfaces.append(cn.narrative.summary)
    res.metrics["connections_ranking"] = [(r["condition"], r["score"]) for r in cn.ranking]

    # Trials
    agent = TrialsAgent(pid, year_of_birth=int(HERO_PATIENT["year_of_birth"]))
    try:
        tr = await agent.run()
    finally:
        await agent.aclose()
    should_match = {str(x) for x in REFERENCE_GOLDEN["trials_should_match"]}
    got_eligible = {m.nct_id for m in tr.matches if m.eligible}
    scores["trials"] = {
        "eligibility_precision": M.set_precision(got_eligible, should_match),
        "eligibility_recall": M.set_recall(got_eligible, should_match),
        "citation_coverage": M.citation_coverage(tr.matches),
        "lint_violations": float(_lint_texts(
            [m.deciding_criterion for m in tr.matches]
            + ([tr.narrative.summary] if tr.narrative else []))),
    }
    cited_items.extend(tr.matches)
    if tr.narrative:
        cited_items.append(tr.narrative)
    lint_surfaces.extend(m.deciding_criterion for m in tr.matches)
    if tr.narrative:
        lint_surfaces.append(tr.narrative.summary)
    res.metrics["trials_eligible"] = sorted(got_eligible)

    # Safety
    agent = SafetyAgent(pid)
    try:
        sf = await agent.run()
    finally:
        await agent.aclose()
    detected = set()
    for a in sf.interaction_alerts:
        meds = {m.lower() for m in a.medications}
        if "methotrexate" in meds and "aspirin" in meds:
            detected.add("antimetabolite-nsaid")
        if len(a.medications) >= 3:
            detected.add("immunosuppressant-stack")
    for a in sf.duplication_alerts:
        if any("iron" in m.lower() for m in a.medications):
            detected.add("duplication:iron")
    gold_signals = {"antimetabolite-nsaid", "immunosuppressant-stack", "duplication:iron"}
    scores["safety"] = {
        "signal_detection_recall": M.set_recall(detected, gold_signals),
        "citation_coverage": M.citation_coverage(sf.alerts),
        "lint_violations": float(_lint_texts(
            [a.rationale for a in sf.alerts]
            + ([sf.narrative.summary] if sf.narrative else []))),
    }
    cited_items.extend(sf.alerts)
    if sf.narrative:
        cited_items.append(sf.narrative)
    lint_surfaces.extend(a.rationale for a in sf.alerts)
    if sf.narrative:
        lint_surfaces.append(sf.narrative.summary)
    res.metrics["safety_alert_count"] = len(sf.alerts)

    # Briefing
    agent = BriefingAgent(pid)
    try:
        bf = await agent.run()
    finally:
        await agent.aclose()
    brief = bf.brief
    scores["briefing"] = {
        "dx_milestone_recall": 1.0 if any(
            e.date == dx_date and dx_term in e.description.lower()
            for e in brief.timeline_highlights) else 0.0,
        "open_questions_count": float(len(brief.open_questions)),
        "citation_coverage": M.citation_coverage(brief.findings),
    }
    cited_items.extend(brief.findings)
    res.metrics["briefing_open_question_count"] = len(brief.open_questions)

    # Justify
    agent = JustifyAgent(pid)
    try:
        jf = await agent.run()
    finally:
        await agent.aclose()
    p = jf.packet
    satisfied = [e for e in p.elements if e.satisfied]
    scores["justify"] = {
        "grounding_correctness": 1.0 if p.requested_drug.lower().startswith("tocilizumab")
        and "takayasu" in (p.indication or "").lower() else 0.0,
        "element_completeness": M.coverage_fraction(len(satisfied), 4),
        "citation_coverage": M.citation_coverage(p.elements),
    }
    cited_items.extend(p.elements)
    if jf.narrative:
        cited_items.append(jf.narrative)
    res.metrics["justify_complete"] = p.complete

    # Cross-cutting swarm invariants
    scores["swarm"] = {
        "swarm_citation_coverage": M.citation_coverage(cited_items),
        "swarm_lint_violations": float(_lint_texts(lint_surfaces)),
    }
    res.metrics["swarm_item_count"] = len(cited_items)
    return scores


# --- scoring / gating --------------------------------------------------------

def _passes(value: Any, threshold: float, compare: str) -> bool:
    if value is None:
        return False
    return value <= threshold if compare == "le" else value >= threshold


def _meta_checks(res: EvalResult, cases: Dict[str, Any]) -> None:
    """Guard against gold drift: the small literal gold in swarm.json must match the
    authoritative seed constants."""
    by = cases["by_agent"]
    res.checks.append(Check(
        "cases gold: connections top_condition matches REFERENCE_GOLDEN",
        by["connections"]["gold"]["top_condition"] == REFERENCE_GOLDEN["literature_top_condition"],
        f'{by["connections"]["gold"]["top_condition"]}'))
    res.checks.append(Check(
        "cases gold: trials should_match matches REFERENCE_GOLDEN",
        [str(x) for x in by["trials"]["gold"]["should_match"]]
        == [str(x) for x in REFERENCE_GOLDEN["trials_should_match"]],
        f'{by["trials"]["gold"]["should_match"]}'))
    res.checks.append(Check(
        "cases gold: timeline dx_date matches GOLDEN",
        by["timeline"]["gold"]["dx_date"] == str(GOLDEN.get("true_diagnosis_date", "2024-03-01")),
        f'{by["timeline"]["gold"]["dx_date"]}'))


def _apply(res: EvalResult, cases: Dict[str, Any],
           scores: Dict[str, Dict[str, float]], live: bool) -> None:
    for case in cases["cases"]:
        agent = case["agent"]
        for name, cfg in case["metrics"].items():
            if cfg.get("live_only") and not live:
                continue
            compare = cfg.get("compare", "ge")
            threshold = cfg["threshold"]
            value = scores.get(agent, {}).get(name)
            res.metrics[f"{agent}.{name}"] = value
            res.checks.append(Check(
                f"{agent}.{name} {compare} {threshold}",
                _passes(value, threshold, compare),
                f"value={value}"))


async def run_swarm(offline: bool = False) -> EvalResult:
    res = EvalResult(phase="swarm")
    cases = load_cases()
    _meta_checks(res, cases)
    if offline:
        _apply(res, cases, _offline_scores(), live=False)
    else:
        res.live = True
        _apply(res, cases, await _live_scores(res), live=True)
    return res
