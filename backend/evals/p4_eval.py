"""P4 eval gate — signature features (time-travel counterfactual; red-thread next).

Two layers:
  * OFFLINE (no cloud): the pure temporal machinery is correct and deterministic —
    `months_between` arithmetic; `as_of_subgraph` never keeps a future-dated node;
    and `build_trace` over the **real** hero encounters + a clinically-faithful fixture
    literature index reproduces the honest anchors (constitutional lead 2021-09-02,
    first vascular-supported flag 2022-08-05, 18 completed months before the diagnosis)
    and the flag requires a genuine vascular discriminator.
  * LIVE (reads the active literature + clinical brains from the registry): the same
    counterfactual computed over the live literature graph reproduces the flag date and
    the 18-month lead, Takayasu is the clear #1 at the flag, and the as-of subgraph over
    the live clinical graph at the flag date excludes every future-dated node.

Reuses the Check/EvalResult contract from the P1 gate.
"""

from __future__ import annotations

from typing import Dict, List

from evals.p1_eval import Check, EvalResult

from app.graph_utils import nodes_edges
from app.normalize import Normalizer
from app.seed.odyssey_patient import ENCOUNTERS, HERO_PATIENT
from app import redthread as RT
from app.timetravel import (
    TARGET_CONDITION,
    as_of_subgraph,
    build_trace,
    constitutional_lead_date,
    first_vascular_flag_date,
    months_between,
    node_event_date,
    run_time_travel,
    summarize,
)

_EXPECT_FLAG = str(HERO_PATIENT["earliest_flaggable_date"])   # 2022-08-05
_EXPECT_MONTHS = int(HERO_PATIENT["months_earlier"])          # 18
_EXPECT_LEAD = "2021-09-02"
_DX_DATE = str(HERO_PATIENT["true_diagnosis_date"])           # 2024-03-01


# --------------------------------------------------------------------------- #
# offline fixture — a clinically-faithful literature index (LiteraturePattern
# nodes) built from feature terms the Normalizer maps to HPO. Only Takayasu
# carries the large-vessel (vascular) discriminators; the mimics overlap on the
# constitutional features only. This reproduces the live ranking's anchors
# without touching the cloud.
# --------------------------------------------------------------------------- #
FIXTURE_PATTERNS: Dict[str, List[str]] = {
    "Takayasu arteritis": [
        "fever", "fatigue", "weight loss", "arthralgia", "night sweats",
        "intermittent claudication", "arterial bruit", "hypertension",
        "reduced pulse", "cold extremities",
    ],
    "Giant cell arteritis": [
        "fever", "fatigue", "weight loss", "arthralgia", "headache",
        "visual impairment",
    ],
    "Infective endocarditis": [
        "fever", "fatigue", "weight loss", "night sweats",
    ],
    "Lymphoma": [
        "fever", "fatigue", "weight loss", "night sweats",
    ],
    "Systemic lupus erythematosus": [
        "fatigue", "arthralgia", "fever",
    ],
    "Adult-onset Still's disease": [
        "fever", "arthralgia",
    ],
    "Fibromuscular dysplasia": [
        "hypertension",
    ],
}


def fixture_literature_nodes() -> List[dict]:
    """Fixture LiteraturePattern graph nodes (same shape as the live dataset_graph)."""
    nodes: List[dict] = []
    for i, (cond, feats) in enumerate(FIXTURE_PATTERNS.items()):
        nodes.append({
            "id": f"lit-{i}",
            "label": f"LiteraturePattern_{i}",
            "type": "LiteraturePattern",
            "properties": {"condition": cond, "features": list(feats)},
        })
    return nodes


def fixture_candidate_index() -> Dict[str, dict]:
    from app.timetravel import build_candidate_index
    return build_candidate_index(fixture_literature_nodes(), Normalizer())


# --------------------------------------------------------------------------- #
# offline checks
# --------------------------------------------------------------------------- #
def _offline_checks(res: EvalResult) -> None:
    # months_between arithmetic (completed calendar months)
    cases = [
        ("2022-08-05", "2024-03-01", 18),
        ("2021-09-02", "2024-03-01", 29),
        ("2024-01-01", "2024-03-01", 2),
        ("2024-02-15", "2024-03-01", 0),   # day-of-month not yet reached
        ("2023-03-01", "2024-03-01", 12),
    ]
    ok = all(months_between(a, b) == exp for a, b, exp in cases)
    res.checks.append(Check(
        "time-travel: months_between counts completed calendar months",
        ok, f"cases={[(a,b,months_between(a,b)) for a,b,_ in cases]}"))

    # as_of_subgraph never keeps a future-dated node
    fixture_nodes = [
        {"id": "e1", "type": "Encounter", "properties": {"date": "2021-02-10"}},
        {"id": "e2", "type": "LabResult", "properties": {"date": "2022-08-05"}},
        {"id": "e3", "type": "Condition", "properties": {"date": "2024-03-01"}},
        {"id": "m1", "type": "Medication", "properties": {"start": "2024-03-20"}},
        {"id": "s1", "type": "Symptom", "properties": {"onset": "3 months"}},  # undated
    ]
    kept, excluded = as_of_subgraph(fixture_nodes, "2022-08-05")
    kept_dates = [node_event_date(n) for n in kept]
    excl_dates = [node_event_date(n) for n in excluded]
    res.checks.append(Check(
        "time-travel: as_of_subgraph excludes every future-dated node",
        all(d is not None and d <= "2022-08-05" for d in kept_dates)
        and all(d is not None and d > "2022-08-05" for d in excl_dates)
        and len(kept) == 2 and len(excluded) == 2,
        f"kept={kept_dates} excluded={excl_dates}"))

    # build_trace over the REAL encounters + fixture index reproduces the anchors
    index = fixture_candidate_index()
    norm = Normalizer()
    trace = build_trace(ENCOUNTERS, index, norm)
    lead = constitutional_lead_date(trace)
    flag = first_vascular_flag_date(trace)
    res.metrics["offline_constitutional_lead"] = lead
    res.metrics["offline_first_flag"] = flag

    res.checks.append(Check(
        f"time-travel: constitutional lead reproduces {_EXPECT_LEAD}",
        lead == _EXPECT_LEAD, f"lead={lead}"))
    res.checks.append(Check(
        f"time-travel: first vascular-supported flag reproduces {_EXPECT_FLAG}",
        flag == _EXPECT_FLAG, f"flag={flag}"))
    res.checks.append(Check(
        f"time-travel: months_earlier reproduces {_EXPECT_MONTHS}",
        flag is not None and months_between(flag, _DX_DATE) == _EXPECT_MONTHS,
        f"months={months_between(flag, _DX_DATE) if flag else None}"))

    flag_step = next((s for s in trace if s.date == flag), None)
    res.checks.append(Check(
        "time-travel: the flag step has Takayasu #1 AND a genuine vascular sign",
        bool(flag_step) and flag_step.top_condition == TARGET_CONDITION
        and flag_step.top_is_clear and flag_step.has_vascular,
        f"top={getattr(flag_step,'top_condition',None)} "
        f"vascular={getattr(flag_step,'vascular_hpo',None)}"))

    # honesty: before any vascular sign there is no flag (constitutional overlap alone
    # must not trigger the headline flag)
    pre_vasc = [s for s in trace if s.date < _EXPECT_FLAG]
    res.checks.append(Check(
        "time-travel: no vascular-supported flag before the first vascular sign",
        all(not s.has_vascular for s in pre_vasc),
        f"pre-flag vascular steps={[s.date for s in pre_vasc if s.has_vascular]}"))

    # the whole summary object is internally consistent
    result = summarize(ENCOUNTERS, index, norm)
    res.checks.append(Check(
        "time-travel: summarize() lead <= flag < diagnosis, target reachable",
        result.constitutional_lead_date is not None
        and result.first_flag_date is not None
        and result.constitutional_lead_date <= result.first_flag_date
        and result.first_flag_date < result.true_diagnosis_date
        and TARGET_CONDITION in result.candidates,
        f"lead={result.constitutional_lead_date} flag={result.first_flag_date} "
        f"dx={result.true_diagnosis_date}"))


# --------------------------------------------------------------------------- #
# offline red-thread fixture — mirrors the live topology:
#   ClinicalKnowledgeGraph --<rel>--> entity
#   DocumentChunk --contains--> ClinicalKnowledgeGraph
#   DocumentChunk --is_part_of--> TextDocument
# --------------------------------------------------------------------------- #
def _redthread_fixture():
    nodes = [
        {"id": "sym1", "type": "Symptom", "properties": {"name": "Intermittent claudication"}},
        {"id": "sym2", "type": "Symptom", "properties": {"name": "Fatigue"}},        # non-vascular
        {"id": "orphan", "type": "Symptom", "properties": {"name": "Arterial bruit"}},  # no doc link
        {"id": "cg", "type": "ClinicalKnowledgeGraph", "properties": {}},
        {"id": "chunk", "type": "DocumentChunk", "properties": {"text": "left-arm claudication on exertion"}},
        {"id": "doc", "type": "TextDocument", "label": "doc_0", "properties": {}},
    ]
    edges = [
        {"source": "cg", "target": "sym1", "label": "symptoms"},
        {"source": "cg", "target": "sym2", "label": "symptoms"},
        {"source": "cg", "target": "orphan", "label": "symptoms"},
        {"source": "chunk", "target": "cg", "label": "contains"},
        {"source": "chunk", "target": "doc", "label": "is_part_of"},
        # note: `orphan` reaches the container but the container's chunk is present,
        # so it actually resolves too — to force an unresolved case, add a second
        # container with no chunk:
        {"source": "cg2", "target": "orphan2", "label": "symptoms"},
    ]
    nodes.append({"id": "cg2", "type": "ClinicalKnowledgeGraph", "properties": {}})
    nodes.append({"id": "orphan2", "type": "Symptom", "properties": {"name": "Cold extremities"}})
    return nodes, edges


def _offline_redthread_checks(res: EvalResult) -> None:
    nodes, edges = _redthread_fixture()
    norm = Normalizer()

    # trace a real phenotype anchor to its source document over real edges
    thread = RT.trace_provenance("sym1", nodes, edges)
    res.checks.append(Check(
        "red-thread: a phenotype node traces to its source document over real edges",
        thread.resolved and thread.document_id == "doc" and thread.chunk_id == "chunk"
        and len(thread.hops) == 3,
        f"resolved={thread.resolved} hops={len(thread.hops)} doc={thread.document_id}"))

    # every hop is a real edge in the graph (the gate assertion)
    triples = RT.edge_triples(edges)
    res.checks.append(Check(
        "red-thread: every hop of the traced thread exists in the graph",
        all(h.triple in triples for h in thread.hops),
        f"hops={[h.triple for h in thread.hops]}"))

    # an anchor whose container has no document chunk stays UNRESOLVED (never fabricated)
    unresolved = RT.trace_provenance("orphan2", nodes, edges)
    res.checks.append(Check(
        "red-thread: an un-sourced anchor is not fabricated a citation",
        not unresolved.resolved and unresolved.document_id is None,
        f"resolved={unresolved.resolved}"))

    # vascular anchor selection picks the discriminators, not the constitutional symptom
    anchors = RT.find_phenotype_anchors(nodes, {"HP:0004417", "HP:0500015"}, norm)
    labels = {RT.clinical_mention(a) for a in anchors}
    res.checks.append(Check(
        "red-thread: anchor selection picks the vascular discriminators",
        any("claudication" in l.lower() for l in labels)
        and not any(l.lower() == "fatigue" for l in labels),
        f"anchors={labels}"))

    # a bundle validates end-to-end and reports the quote
    bundle = RT.RedThreadBundle(condition=TARGET_CONDITION)
    bundle.patient_threads.append(thread)
    ok = RT.validate(bundle, edges)
    res.checks.append(Check(
        "red-thread: bundle.validate confirms all edges exist + carries the quote",
        ok and bundle.all_edges_exist and bool(thread.quote),
        f"all_edges_exist={bundle.all_edges_exist} quote={thread.quote!r}"))


# --------------------------------------------------------------------------- #
# live checks
# --------------------------------------------------------------------------- #
async def _live_checks(res: EvalResult) -> None:
    res.live = True
    from app.cognee_client import get_client
    from app import registry

    client = get_client()
    await client.connect()
    try:
        result = await run_time_travel(client, "odyssey")
        res.metrics["timetravel"] = {
            "constitutional_lead_date": result.constitutional_lead_date,
            "first_flag_date": result.first_flag_date,
            "months_earlier": result.months_earlier,
            "candidates": result.candidates,
        }

        res.checks.append(Check(
            "time-travel (live): the target is reachable in the literature universe",
            result.true_diagnosis in result.candidates,
            f"candidates={result.candidates}"))
        res.checks.append(Check(
            f"time-travel (live): first vascular-supported flag == {_EXPECT_FLAG}",
            result.first_flag_date == _EXPECT_FLAG,
            f"flag={result.first_flag_date}"))
        res.checks.append(Check(
            f"time-travel (live): flaggable {_EXPECT_MONTHS} months earlier",
            result.months_earlier == _EXPECT_MONTHS,
            f"months={result.months_earlier}"))
        res.checks.append(Check(
            "time-travel (live): at the flag, Takayasu is the clear #1 with a vascular sign",
            bool(result.flag_step) and result.flag_step.top_condition == TARGET_CONDITION
            and result.flag_step.top_is_clear and result.flag_step.has_vascular,
            f"flag_step={result.flag_step.to_dict() if result.flag_step else None}"))
        res.checks.append(Check(
            "time-travel (live): the flag precedes the real diagnosis by >= 12 months",
            result.months_earlier >= 12, f"months={result.months_earlier}"))

        # as-of subgraph over the LIVE clinical graph at the flag date excludes future
        clinical = registry.get_active("odyssey", "clinical")
        if clinical and clinical.get("id"):
            graph = await client.dataset_graph(clinical["id"])
            nodes, _ = nodes_edges(graph)
            kept, excluded = as_of_subgraph(nodes, _EXPECT_FLAG)
            kept_future = [node_event_date(n) for n in kept
                           if (node_event_date(n) or "") > _EXPECT_FLAG]
            res.metrics["asof_kept"] = len(kept)
            res.metrics["asof_excluded"] = len(excluded)
            res.checks.append(Check(
                "time-travel (live): as-of subgraph at the flag keeps no future-dated node",
                len(kept_future) == 0 and len(kept) > 0 and len(excluded) > 0,
                f"kept={len(kept)} excluded={len(excluded)} future_leaks={kept_future}"))
            # the graph provably grows over time (fewer nodes as-of the flag than at dx)
            kept_dx, _ = as_of_subgraph(nodes, _DX_DATE)
            res.checks.append(Check(
                "time-travel (live): the as-of graph grows over time (flag < diagnosis)",
                len(kept) < len(kept_dx),
                f"as-of flag={len(kept)} < as-of dx={len(kept_dx)}"))
        else:
            res.checks.append(Check(
                "time-travel (live): active clinical brain resolves for as-of subgraph",
                False, "no active clinical dataset in the registry"))

        # ---- red-thread (live, graph-backed provenance) ----
        bundle = await RT.run_redthread(client, "odyssey", TARGET_CONDITION)
        res.metrics["redthread"] = bundle.to_dict()
        resolved_patient = [t for t in bundle.patient_threads if t.resolved]
        resolved_lit = [t for t in bundle.literature_threads if t.resolved]

        res.checks.append(Check(
            "red-thread (live): every hop of every thread is a real graph edge",
            bundle.all_edges_exist,
            f"all_edges_exist={bundle.all_edges_exist} "
            f"unresolved={bundle.unresolved_anchors}"))
        res.checks.append(Check(
            "red-thread (live): >= 2 patient discriminator threads resolve to a source",
            len(resolved_patient) >= 2, f"resolved_patient={len(resolved_patient)}"))
        res.checks.append(Check(
            "red-thread (live): the literature pattern is provenance-traced",
            len(resolved_lit) >= 1, f"resolved_lit={len(resolved_lit)}"))
        res.checks.append(Check(
            "red-thread (live): every resolved thread ends at a document with a quote",
            all(t.document_id and t.chunk_id and t.quote for t in bundle.threads),
            f"threads={len(bundle.threads)}"))
        res.checks.append(Check(
            "red-thread (live): the confirmed condition is one of the anchors",
            any("takayasu" in (t.anchor_label or "").lower() for t in bundle.threads),
            "confirmed-condition thread present"))
    finally:
        await client.disconnect()


async def run_p4(offline: bool = False) -> EvalResult:
    res = EvalResult(phase="p4")
    _offline_checks(res)
    _offline_redthread_checks(res)
    if not offline:
        await _live_checks(res)
    return res
