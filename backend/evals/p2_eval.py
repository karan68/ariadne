"""P2 eval gate — agent swarm. Grows one agent at a time; currently gates the
TimelineAgent (build order: Timeline -> Connections -> Trials -> Briefing ->
Safety -> Justify).

  * OFFLINE (no cloud): the deterministic timeline builder orders events by date,
    extracts real ISO dates, and never guesses a date for free-text onsets.
  * LIVE (reads the active clinical brain via the registry): the agent produces a
    non-trivial, date-ordered, multi-year timeline that includes the confirmed
    diagnosis event, a citation-backed narrative (citation-required), and a working
    "what changed since <date>" slice.

Reuses the Check/EvalResult contract from the P1 gate.
"""

from __future__ import annotations

import uuid

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
    hero_confirmed_conditions,
    parse_age_constraint,
)
from app.agents.briefing import (
    BriefingAgent,
    parse_open_questions,
    select_highlights,
)
from app.agents.safety import (
    SafetyAgent,
    build_medication_index,
    canonical_drug,
    detect_duplications,
    detect_interactions,
)
from app.agents.justify import (
    JustifyAgent,
    confirmed_condition_display,
    prior_therapy_drugs,
    select_prior_auth_drug,
)
from app.models import find_diagnosis_language, TimelineEvent
from app.normalize import Normalizer, hpo_display_map
from app.seed.odyssey_patient import GOLDEN, HERO_PATIENT
from app.seed.reference_data import REFERENCE_GOLDEN

_SINCE = "2024-01-01"


def _syn_nodes():
    def n(t, label, **props):
        return {"id": uuid.uuid4().hex, "label": label, "type": t, "properties": props}
    return [
        n("Encounter", "General Medicine", date="2021-02-10", setting="General Medicine",
          reason="fatigue"),
        n("LabResult", "LabResult_x", date="2021-02-10", analyte="ESR", value="62",
          unit="mm/hr", flag="high"),
        n("Medication", "Oral iron", start="2021-02-10", prescriber="Dr. A. Sharma"),
        n("Condition", "Takayasu arteritis", date="2024-03-01", status="confirmed"),
        n("Symptom", "Fatigue", onset="3 months"),  # free-text -> excluded
    ]


def _offline_checks(res: EvalResult) -> None:
    events = build_timeline_events(_syn_nodes())
    dates = [e.date for e in events]
    res.metrics["offline_event_count"] = len(events)
    res.checks.append(Check("timeline is date-ordered", dates == sorted(dates), str(dates)))
    res.checks.append(Check(
        "free-text onset excluded (no guessed dates)",
        all(e.type != "Symptom" for e in events),
        "Symptom with onset='3 months' must not appear on the dated axis",
    ))
    res.checks.append(Check(
        "ISO dates extracted from type-specific fields",
        "2021-02-10" in dates and "2024-03-01" in dates,
        str(dates),
    ))


async def _live_checks(res: EvalResult) -> None:
    patient_id = HERO_PATIENT["id"]
    agent = TimelineAgent(patient_id)
    try:
        result = await agent.run()
        recent = await agent.run(since=_SINCE)
    finally:
        await agent.aclose()

    res.live = True
    events = result.events
    dates = [e.date for e in events]
    res.metrics["dataset_name"] = result.dataset_name
    res.metrics["narrative_via"] = result.used_search_type
    res.metrics["event_count"] = len(events)
    res.metrics["span"] = list(result.span) if result.span else None

    res.checks.append(Check("timeline has >= 10 events", len(events) >= 10, f"got {len(events)}"))
    res.checks.append(Check("timeline date-ordered", dates == sorted(dates)))
    res.checks.append(Check(
        "timeline spans multiple years",
        bool(dates) and dates[0][:4] != dates[-1][:4],
        f"{dates[0] if dates else '-'}..{dates[-1] if dates else '-'}",
    ))

    dx_date = str(GOLDEN.get("true_diagnosis_date", "2024-03-01"))
    has_dx = any(e.date == dx_date and "takayasu" in e.description.lower() for e in events)
    res.checks.append(Check(
        f"confirmed-diagnosis event present on {dx_date}", has_dx,
        f"events@{dx_date}={[e.description[:40] for e in events if e.date == dx_date]}",
    ))

    narrative_cited = result.narrative is not None and len(result.narrative.evidence) > 0
    res.metrics["narrative_citations"] = len(result.narrative.evidence) if result.narrative else 0
    res.checks.append(Check("narrative is citation-backed", narrative_cited,
                            "narrative Finding must carry >=1 EvidenceRef"))

    since_ok = bool(recent.events) and all(e.date >= _SINCE for e in recent.events)
    res.metrics["since_event_count"] = len(recent.events)
    res.checks.append(Check(f"since-slice ({_SINCE}) narrows and is bounded", since_ok,
                            f"{len(recent.events)} events, all >= {_SINCE}: {since_ok}"))


# --- ConnectionsAgent --------------------------------------------------------

def _conn_clinical_nodes():
    def n(t, label, **props):
        return {"id": uuid.uuid4().hex, "label": label, "type": t, "properties": props}
    return [
        n("Symptom", "Fatigue"), n("Symptom", "Low-grade fever"),
        n("Symptom", "Night sweats"), n("Symptom", "Unintentional weight loss"),
        n("Symptom", "Arthralgia of knees"),
        n("Symptom", "Upper-limb claudication"),   # -> vascular
        n("Symptom", "Left hand cold sensation"),  # -> vascular
        n("Symptom", "No chest pain"),              # pertinent negative -> dropped
    ]


def _conn_literature_nodes():
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


def _connections_offline_checks(res: EvalResult) -> None:
    norm = Normalizer()
    displays, hpo = patient_phenotype(_conn_clinical_nodes(), norm)
    index = build_candidate_index(_conn_literature_nodes(), norm)
    ranked = rank_candidates(set(hpo), index, hpo_display_map())

    res.metrics["offline_phenotype_size"] = len(hpo)
    res.metrics["offline_top_condition"] = ranked[0].condition if ranked else None

    res.checks.append(Check(
        "phenotype captures vascular discriminators",
        "HP:0004417" in hpo and "HP:0500015" in hpo,
        f"hpo={hpo}",
    ))
    res.checks.append(Check(
        "candidate universe grounded in literature conditions",
        set(index) == {"Takayasu arteritis", "Lymphoma", "Systemic lupus erythematosus"},
        str(sorted(index)),
    ))
    res.checks.append(Check(
        "ranking puts large-vessel pattern (Takayasu) first",
        bool(ranked) and ranked[0].condition == "Takayasu arteritis"
        and ranked[0].score > ranked[1].score,
        f"{[(c.condition, c.score) for c in ranked]}",
    ))
    res.checks.append(Check(
        "top candidate's rank is vascular-driven",
        bool(ranked) and bool(ranked[0].vascular),
        f"vascular={ranked[0].vascular_features if ranked else None}",
    ))


async def _connections_live_checks(res: EvalResult) -> None:
    agent = ConnectionsAgent(HERO_PATIENT["id"])
    try:
        result = await agent.run(top_k=3)
    finally:
        await agent.aclose()

    res.live = True
    res.metrics["connections_phenotype_size"] = len(result.patient_hpo)
    res.metrics["connections_top_condition"] = result.top_condition
    res.metrics["connections_candidate_count"] = len(result.candidates)
    res.metrics["connections_ranking"] = [
        (r["condition"], r["score"]) for r in result.ranking]

    expected_top = str(REFERENCE_GOLDEN["literature_top_condition"])
    res.checks.append(Check(
        "connections phenotype non-trivial (>= 8 HPO)",
        len(result.patient_hpo) >= 8, f"got {len(result.patient_hpo)}"))
    res.checks.append(Check(
        f"top-ranked candidate is {expected_top}",
        result.top_condition == expected_top, f"got {result.top_condition}"))
    res.checks.append(Check(
        "candidates surfaced and every one is cited",
        bool(result.candidates) and all(c.evidence for c in result.candidates),
        f"{len(result.candidates)} candidates"))
    res.checks.append(Check(
        "every evidence-path hop carries >= 1 citation",
        all((c.path is None) or all(h.evidence for h in c.path.hops)
            for c in result.candidates),
        "citation-required per hop"))
    res.checks.append(Check(
        "Takayasu surfaced as a cited candidate",
        any("takayasu" in c.summary.lower() for c in result.candidates)))
    res.checks.append(Check(
        "discrimination narrative is citation-backed",
        result.narrative is not None and len(result.narrative.evidence) > 0))
    res.checks.append(Check(
        "no surfaced summary trips the no-diagnosis lint",
        all(not find_diagnosis_language(c.summary) for c in result.candidates)
        and (result.narrative is None or not find_diagnosis_language(result.narrative.summary)),
        "decision-support language only"))


# --- TrialsAgent -------------------------------------------------------------

def _trials_graph_nodes_edges():
    """Synthetic trials graph mirroring the live container-grouped shape."""
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
        ["Age 18 to 65 years.", "Confirmed Takayasu arteritis."],
        ["Active infection."])
    add("NCT09000004", ["Giant cell arteritis"],
        ["Age 50 years or older."], ["Age under 50 years.", "Takayasu arteritis."])
    add("NCT09000006", ["Takayasu arteritis"],
        ["Age 5 to 17 years."], ["Age 18 years or older."])
    return nodes, edges


def _trials_offline_checks(res: EvalResult) -> None:
    # age parser
    rng = parse_age_constraint("Age 18 to 65 years.")
    lower = parse_age_constraint("Age 50 years or older.")
    under = parse_age_constraint("Age under 50 years.")
    res.checks.append(Check(
        "age-band parser handles range/lower/under",
        bool(rng) and rng.satisfied_by(32) and bool(lower) and not lower.satisfied_by(32)
        and bool(under) and under.satisfied_by(32),
        f"range={rng}, lower={lower}, under={under}",
    ))

    nodes, edges = _trials_graph_nodes_edges()
    index = build_trial_index(nodes, edges)
    res.metrics["offline_trial_count"] = len(index)
    res.checks.append(Check(
        "trial index groups criteria under each trial",
        set(index) == {"NCT09000001", "NCT09000004", "NCT09000006"}
        and len(index["NCT09000001"].inclusion) == 2,
        str(sorted(index)),
    ))

    hero = ["takayasu arteritis", "hypertension"]
    age = compute_age(1994, as_of_year=2026)
    v1 = evaluate_eligibility(age, hero, index["NCT09000001"])
    v4 = evaluate_eligibility(age, hero, index["NCT09000004"])
    v6 = evaluate_eligibility(age, hero, index["NCT09000006"])
    res.checks.append(Check(
        "deterministic eligibility reproduces verdicts",
        v1.eligible and (not v4.eligible) and (not v6.eligible),
        f"NCT1={v1.eligible} NCT4={v4.eligible} NCT6={v6.eligible}",
    ))
    res.checks.append(Check(
        "paediatric trap: right disease, wrong age (age axis decides)",
        (not v6.eligible) and v6.condition_ok and (not v6.age_ok) and v6.reason == "age",
        f"cond_ok={v6.condition_ok} age_ok={v6.age_ok} reason={v6.reason}",
    ))


async def _trials_live_checks(res: EvalResult) -> None:
    yob = int(HERO_PATIENT["year_of_birth"])
    agent = TrialsAgent(HERO_PATIENT["id"], year_of_birth=yob)
    try:
        result = await agent.run()
    finally:
        await agent.aclose()

    res.live = True
    by_id = {m.nct_id: m for m in result.matches}
    should_match = {str(x) for x in REFERENCE_GOLDEN["trials_should_match"]}
    should_not = {str(x) for x in REFERENCE_GOLDEN["trials_should_not_match"]}
    got_eligible = {nct for nct, m in by_id.items() if m.eligible}
    got_ineligible = {nct for nct, m in by_id.items() if m.eligible is False}

    res.metrics["trials_hero_age"] = result.hero_age
    res.metrics["trials_match_count"] = len(result.matches)
    res.metrics["trials_eligible"] = sorted(got_eligible)
    res.metrics["trials_ineligible"] = sorted(got_ineligible)

    res.checks.append(Check(
        "all 6 seeded trials read from the grounded universe",
        (should_match | should_not).issubset(set(by_id)),
        f"got {sorted(by_id)}"))
    res.checks.append(Check(
        "eligibility reproduces the golden match/no-match set",
        got_eligible == should_match and got_ineligible == should_not,
        f"match={sorted(got_eligible)} no-match={sorted(got_ineligible)}"))
    paeds = by_id.get("NCT09000006")
    res.checks.append(Check(
        "paediatric trial (NCT09000006) not eligible via age",
        bool(paeds) and paeds.eligible is False
        and ("18 years or older" in paeds.deciding_criterion
             or "age" in paeds.deciding_criterion.lower()),
        f"deciding={paeds.deciding_criterion if paeds else None}"))
    res.checks.append(Check(
        "every surfaced trial match is cited",
        bool(result.matches) and all(m.evidence for m in result.matches),
        f"{len(result.matches)} matches"))
    res.checks.append(Check(
        "trials narrative is citation-backed",
        result.narrative is not None and len(result.narrative.evidence) > 0))
    res.checks.append(Check(
        "no surfaced trials text trips the no-diagnosis lint",
        all(not find_diagnosis_language(m.deciding_criterion) for m in result.matches)
        and (result.narrative is None
             or not find_diagnosis_language(result.narrative.summary)),
        "decision-support language only"))


# --- BriefingAgent -----------------------------------------------------------

def _briefing_offline_checks(res: EvalResult) -> None:
    events = [
        TimelineEvent(date="2021-02-10", type="Encounter", description="General Medicine: fatigue"),
        TimelineEvent(date="2021-05-01", type="LabResult", description="ESR = 62 mm/hr (high)"),
        TimelineEvent(date="2024-03-01", type="Condition", description="Takayasu arteritis [confirmed]"),
        TimelineEvent(date="2024-03-05", type="Medication", description="Started Prednisolone"),
        TimelineEvent(date="2024-09-15", type="Encounter", description="Rheumatology follow-up"),
    ]
    hl = select_highlights(events, recent=2)
    res.checks.append(Check(
        "briefing highlights include onset, confirmed dx, and most recent",
        bool(hl) and hl[0].date == "2021-02-10"
        and any("confirm" in e.description.lower() for e in hl)
        and hl[-1].date == "2024-09-15",
        f"{[(e.date, e.description) for e in hl]}",
    ))

    answer = (
        "- **CT/MR angiography ordered on 30 Jun 2023 is still pending** – note【2023-06-30】\n"
        "- **Right-renal-artery angioplasty decision remains pending** – note【2024-09-15】\n"
        "ignore this non-bullet trailing line\n"
    )
    qs = parse_open_questions(answer)
    res.checks.append(Check(
        "briefing parses clean open questions (no bullets/bold/citations)",
        len(qs) == 2 and all("【" not in q and "**" not in q for q in qs)
        and qs[0].startswith("CT/MR angiography"),
        str(qs),
    ))


async def _briefing_live_checks(res: EvalResult) -> None:
    agent = BriefingAgent(HERO_PATIENT["id"])
    try:
        result = await agent.run()
    finally:
        await agent.aclose()

    res.live = True
    brief = result.brief
    dx_date = str(GOLDEN.get("true_diagnosis_date", "2024-03-01"))

    res.metrics["briefing_event_count"] = result.event_count
    res.metrics["briefing_highlight_count"] = len(brief.timeline_highlights)
    res.metrics["briefing_open_question_count"] = len(brief.open_questions)
    res.metrics["briefing_suppressed"] = result.suppressed

    res.checks.append(Check(
        "briefing summary present and citation-backed",
        bool(brief.summary.strip()) and result.summary_finding is not None
        and len(result.summary_finding.evidence) > 0,
        f"summary_len={len(brief.summary)}"))
    res.checks.append(Check(
        "briefing highlights include the confirmed-diagnosis milestone",
        any(e.date == dx_date and "takayasu" in e.description.lower()
            for e in brief.timeline_highlights),
        f"@{dx_date}: {[e.description for e in brief.timeline_highlights if e.date == dx_date]}"))
    res.checks.append(Check(
        "briefing surfaces >= 3 cited open questions",
        len(brief.open_questions) >= 3 and result.open_questions_finding is not None
        and len(result.open_questions_finding.evidence) > 0,
        f"{len(brief.open_questions)} questions"))
    res.checks.append(Check(
        "briefing derived only from cited memory (nothing uncited surfaced)",
        result.suppressed == [] and bool(brief.findings)
        and all(f.evidence for f in brief.findings),
        f"suppressed={result.suppressed}, findings={len(brief.findings)}"))


def _safety_med_nodes():
    def n(label, prescriber=None, **props):
        p = dict(props)
        if prescriber:
            p["prescriber"] = prescriber
        return {"id": uuid.uuid4().hex, "label": label, "type": "Medication", "properties": p}
    return [
        n("Oral iron", "Dr. A. Sharma"),
        n("oral iron", "Dr. R. Iyer"),
        n("Amlodipine", "Dr. N. Das"),
        n("Ramipril", "Dr. N. Das"),
        n("prednisolone", "Dr. S. Menon"),
        n("methotrexate", "Dr. S. Menon"),
        n("aspirin", "Dr. S. Menon"),
        n("tocilizumab", "Dr. S. Menon"),
    ]


def _safety_offline_checks(res: EvalResult) -> None:
    res.checks.append(Check(
        "safety canonicalises casing/synonyms to a single drug",
        canonical_drug("Oral iron") == "iron" and canonical_drug("Ferrous sulfate") == "iron"
        and canonical_drug("Methotrexate") == "methotrexate",
        f'oral iron->{canonical_drug("Oral iron")}',
    ))

    index = build_medication_index(_safety_med_nodes())
    res.checks.append(Check(
        "safety med index dedupes to the grounded distinct-drug universe",
        set(index) == {"iron", "amlodipine", "ramipril", "prednisolone",
                       "methotrexate", "aspirin", "tocilizumab"},
        f"{sorted(index)}",
    ))

    sigs = detect_interactions(index)
    by_rule = {s.rule_id: s for s in sigs}
    res.checks.append(Check(
        "safety detects the methotrexate + NSAID interaction",
        "antimetabolite-nsaid" in by_rule
        and set(by_rule["antimetabolite-nsaid"].medications) == {"methotrexate", "aspirin"},
        f"{[s.rule_id for s in sigs]}",
    ))
    res.checks.append(Check(
        "safety detects the cumulative immunosuppressant burden",
        "immunosuppressant-stack" in by_rule
        and set(by_rule["immunosuppressant-stack"].medications)
        == {"methotrexate", "prednisolone", "tocilizumab"},
        f'{by_rule.get("immunosuppressant-stack")}',
    ))
    res.checks.append(Check(
        "safety does not over-flag the antihypertensive pair",
        all(set(s.medications) != {"amlodipine", "ramipril"} for s in sigs),
        f"{[sorted(s.medications) for s in sigs]}",
    ))

    dups = detect_duplications(index)
    res.checks.append(Check(
        "safety detects the cross-prescriber iron duplication only",
        len(dups) == 1 and dups[0].canonical == "iron"
        and dups[0].prescribers == ["Dr. A. Sharma", "Dr. R. Iyer"],
        f"{[(d.canonical, d.prescribers) for d in dups]}",
    ))


async def _safety_live_checks(res: EvalResult) -> None:
    agent = SafetyAgent(HERO_PATIENT["id"])
    try:
        result = await agent.run()
    finally:
        await agent.aclose()

    res.live = True
    res.metrics["safety_med_count"] = len(result.medications)
    res.metrics["safety_alert_count"] = len(result.alerts)
    res.metrics["safety_suppressed"] = result.suppressed_uncited

    res.checks.append(Check(
        "safety reads a non-trivial grounded medication universe (>=6)",
        len(result.medications) >= 6,
        f"{len(result.medications)}: {result.medications}"))

    mtx_aspirin = next(
        (a for a in result.interaction_alerts
         if any("methotrexate" in m.lower() for m in a.medications)
         and any("aspirin" in m.lower() for m in a.medications)), None)
    res.checks.append(Check(
        "safety surfaces the methotrexate + aspirin interaction, cited",
        mtx_aspirin is not None and len(mtx_aspirin.evidence) > 0,
        f"{mtx_aspirin.medications if mtx_aspirin else None}"))

    stack = next((a for a in result.interaction_alerts if len(a.medications) >= 3), None)
    res.checks.append(Check(
        "safety surfaces the cumulative immunosuppressant stack, cited",
        stack is not None and len(stack.evidence) > 0,
        f"{stack.medications if stack else None}"))

    dup = next((a for a in result.duplication_alerts
                if any("iron" in m.lower() for m in a.medications)), None)
    res.checks.append(Check(
        "safety surfaces the cross-prescriber iron duplication, cited",
        dup is not None and len(dup.evidence) > 0,
        f"{dup.medications if dup else None}"))

    res.checks.append(Check(
        "safety: every surfaced alert is cited (citation-required)",
        bool(result.alerts) and all(a.evidence for a in result.alerts),
        f"{[(a.kind, len(a.evidence)) for a in result.alerts]}"))
    res.checks.append(Check(
        "safety narrative is citation-backed",
        result.narrative is not None and len(result.narrative.evidence) > 0,
        f"narrative={'present' if result.narrative else 'None'}"))
    lint_ok = all(not find_diagnosis_language(a.rationale) for a in result.alerts)
    if result.narrative:
        lint_ok = lint_ok and not find_diagnosis_language(result.narrative.summary)
    res.checks.append(Check(
        "safety surfaced text is free of assertive diagnosis language",
        lint_ok, "no-diagnosis lint"))


def _justify_nodes():
    def med(label, prescriber=None, **props):
        p = dict(props)
        if prescriber:
            p["prescriber"] = prescriber
        return {"id": uuid.uuid4().hex, "label": label, "type": "Medication", "properties": p}

    def cond(label, status, date=None):
        p = {"status": status}
        if date:
            p["date"] = date
        return {"id": uuid.uuid4().hex, "label": label, "type": "Condition", "properties": p}

    return [
        med("prednisolone", "Dr. S. Menon"),
        med("methotrexate", "Dr. S. Menon"),
        med("tocilizumab", "Dr. S. Menon"),
        med("aspirin", "Dr. S. Menon"),
        med("Amlodipine", "Dr. N. Das"),
        cond("iron deficiency", "confirmed", "2021-05-18"),
        cond("Hypertension", "confirmed", "2023-06-30"),
        cond("Takayasu arteritis", "confirmed", "2024-03-01"),
        cond("fibromyalgia", "suspected", "2021-11-20"),
    ]


def _justify_offline_checks(res: EvalResult) -> None:
    nodes = _justify_nodes()
    index = build_medication_index(nodes)

    res.checks.append(Check(
        "justify selects the biologic (tocilizumab) as the prior-auth drug",
        select_prior_auth_drug(index) == "tocilizumab",
        f"{select_prior_auth_drug(index)}",
    ))
    prior = prior_therapy_drugs(index, "tocilizumab")
    res.checks.append(Check(
        "justify step-therapy set is the conventional immunosuppressants (not the biologic)",
        prior == ["methotrexate", "prednisolone"],
        f"{prior}",
    ))
    res.checks.append(Check(
        "justify indication is the most-recently-confirmed condition (odyssey trap)",
        confirmed_condition_display(nodes) == "Takayasu arteritis",
        f"{confirmed_condition_display(nodes)}",
    ))


async def _justify_live_checks(res: EvalResult) -> None:
    agent = JustifyAgent(HERO_PATIENT["id"])
    try:
        result = await agent.run()
    finally:
        await agent.aclose()

    res.live = True
    p = result.packet
    res.metrics["justify_requested_drug"] = p.requested_drug
    res.metrics["justify_indication"] = p.indication
    res.metrics["justify_complete"] = p.complete
    res.metrics["justify_missing"] = p.missing_elements

    res.checks.append(Check(
        "justify requests prior-auth for the biologic tocilizumab",
        p.requested_drug.lower().startswith("tocilizumab"),
        f"{p.requested_drug}"))
    res.checks.append(Check(
        "justify indication is the confirmed Takayasu diagnosis",
        "takayasu" in (p.indication or "").lower(),
        f"{p.indication}"))
    res.checks.append(Check(
        "justify assembles the four required elements in order",
        [e.key for e in p.elements]
        == ["diagnosis", "active_disease", "prior_therapy", "supporting_evidence"],
        f"{[e.key for e in p.elements]}"))
    res.checks.append(Check(
        "justify: every packet element is citation-backed",
        bool(p.elements) and all(e.satisfied and e.evidence for e in p.elements),
        f"{[(e.key, len(e.evidence)) for e in p.elements]}"))
    se = next((e for e in p.elements if e.key == "supporting_evidence"), None)
    res.checks.append(Check(
        "justify supporting evidence is drawn from the reference brain, cited",
        se is not None and se.source == "reference" and len(se.evidence) > 0,
        f"{se.source if se else None}/{len(se.evidence) if se else 0}"))
    res.checks.append(Check(
        "justify packet is complete (nothing missing / suppressed)",
        p.complete and not p.missing_elements and result.suppressed_uncited == [],
        f"missing={p.missing_elements}, suppressed={result.suppressed_uncited}"))
    res.checks.append(Check(
        "justify medical-necessity narrative is cited",
        result.narrative is not None and len(result.narrative.evidence) > 0,
        f"narrative={'present' if result.narrative else 'None'}"))


async def run_p2(offline: bool = False) -> EvalResult:
    res = EvalResult(phase="p2")
    _offline_checks(res)
    _connections_offline_checks(res)
    _trials_offline_checks(res)
    _briefing_offline_checks(res)
    _safety_offline_checks(res)
    _justify_offline_checks(res)
    if not offline:
        await _live_checks(res)
        await _connections_live_checks(res)
        await _trials_live_checks(res)
        await _briefing_live_checks(res)
        await _safety_live_checks(res)
        await _justify_live_checks(res)
    return res
