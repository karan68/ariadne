"""P3 eval gate — cloud-native lifecycle. Grows one concern at a time; currently
gates principals + RBAC (build order: principals/RBAC -> Sessions -> improve -> forget).

  * OFFLINE (no cloud): the permission matrix is fail-closed and enforces the headline
    contract — family is denied the clinical brain (guarded_datasets -> []) while the
    provider is granted it; every swarm agent maps to the clinical brain (+ its
    reference brains); the provisioning plan grants family only the reference brains.
  * LIVE (mutates the tenant idempotently): provisioning creates the provider + family
    roles, grants each role READ on exactly the brains the matrix allows, and registers
    all six agents as principals; grants and registrations come back OK, and the
    app-level enforcement reproduces family->clinical->[] against the real registry.

Reuses the Check/EvalResult contract from the P1 gate.
"""

from __future__ import annotations

import time

from evals.p1_eval import Check, EvalResult

from app import principals as P
from app import registry
from app import sessions as SESS
from app import feedback as FB
from app import forget as FGT
from app.feedback import Candidate, FeedbackLedger, THUMBS_DOWN, THUMBS_UP
from app.principals import AppRole, BrainKind, Permission
from app.ontology import CUSTOM_EXTRACTION_PROMPT, clinical_graph_model_json
from app.cloud_client import CloudCogneeClient
from app.config import get_settings
from evals.metrics import precision_at_k

_PATIENT = "odyssey"


def _offline_checks(res: EvalResult) -> None:
    res.checks.append(Check(
        "RBAC: provider reads the clinical brain, family is denied",
        P.authorize(AppRole.PROVIDER, BrainKind.CLINICAL)
        and not P.authorize(AppRole.FAMILY, BrainKind.CLINICAL),
        f"provider={P.authorize(AppRole.PROVIDER, BrainKind.CLINICAL)} "
        f"family={P.authorize(AppRole.FAMILY, BrainKind.CLINICAL)}"))

    res.checks.append(Check(
        "RBAC: matrix is fail-closed for unknown role/brain",
        not P.authorize("stranger", BrainKind.CLINICAL)
        and not P.authorize(AppRole.FAMILY, "unknown"),
        "unknown principals denied by default"))

    res.checks.append(Check(
        "RBAC: only the owner may write/delete the clinical brain",
        P.authorize(AppRole.OWNER, BrainKind.CLINICAL, Permission.WRITE)
        and P.authorize(AppRole.OWNER, BrainKind.CLINICAL, Permission.DELETE)
        and not P.authorize(AppRole.PROVIDER, BrainKind.CLINICAL, Permission.WRITE),
        "owner rwd, provider read-only"))

    res.checks.append(Check(
        "RBAC: all three roles may read the public reference brains",
        all(P.authorize(r, BrainKind.LITERATURE) and P.authorize(r, BrainKind.TRIALS)
            for r in (AppRole.OWNER, AppRole.PROVIDER, AppRole.FAMILY)),
        "literature+trials readable by everyone"))

    res.checks.append(Check(
        "RBAC: every swarm agent maps to the clinical brain",
        all(BrainKind.CLINICAL in b for b in P.AGENT_BRAINS.values())
        and len(P.AGENT_BRAINS) == 6,
        f"{sorted(P.AGENT_BRAINS)}"))

    # enforcement primitive against the real registry (clinical brain is seeded live)
    fam = P.guarded_datasets(AppRole.FAMILY, _PATIENT, BrainKind.CLINICAL)
    prov = P.guarded_datasets(AppRole.PROVIDER, _PATIENT, BrainKind.CLINICAL)
    res.metrics["offline_family_clinical_datasets"] = fam
    res.metrics["offline_provider_clinical_datasets"] = prov
    res.checks.append(Check(
        "RBAC enforcement: family->clinical yields [] (denied at the boundary)",
        fam == [], f"got {fam}"))
    res.checks.append(Check(
        "RBAC enforcement: provider->clinical yields the seeded clinical dataset",
        len(prov) == 1 and prov[0].startswith("patient_odyssey_clinical"),
        f"got {prov}"))


def _offline_sessions_checks(res: EvalResult) -> None:
    # session-id round-trips to (agent, patient, unix, suffix)
    p = SESS.parse_session_id("connections-odyssey-1783026394-run-takayasu")
    res.checks.append(Check(
        "Sessions: agent session id parses to (agent, patient, run)",
        p is not None and p.agent == "connections" and p.patient == "odyssey"
        and p.run_id == "connections-odyssey-1783026394",
        f"parsed={p}"))

    # a synthetic full-swarm stream folds into per-agent attribution, all six seen
    stream = {"sessions": [
        {"session_id": f"{name}-odyssey-{1000 + i}-run-x",
         "tokens_in": 10, "tokens_out": 2, "cost_usd": 0.0, "error_count": 0,
         "started_at": "2026-07-02T21:00:00+00:00",
         "last_activity_at": "2026-07-02T21:00:05+00:00"}
        for i, name in enumerate(SESS.AGENT_NAMES)]}
    by = SESS.group_by_agent(stream)
    res.metrics["offline_sessions_agents"] = sorted(by)
    res.checks.append(Check(
        "Sessions: per-agent grouping attributes every swarm agent",
        set(by) == set(SESS.AGENT_NAMES),
        f"agents={sorted(by)}"))

    # unparseable / non-Ariadne noise is excluded (fail-closed attribution)
    noisy = SESS.group_by_agent({"sessions": [
        {"session_id": "no-stamp-here"},
        {"session_id": "externaltool-odyssey-100-run"},
    ]}, ariadne_only=True)
    res.checks.append(Check(
        "Sessions: non-Ariadne / unparseable sessions are not attributed",
        noisy == {},
        f"got {sorted(noisy)}"))


def _improve_case():
    # a labeled Connections case where a red herring outranks a relevant dx at baseline
    return [
        Candidate("Takayasu arteritis", 0.90),
        Candidate("Lymphoma", 0.85),               # red herring
        Candidate("Giant cell arteritis", 0.60),
        Candidate("Fibromuscular dysplasia", 0.30),
    ], {"Takayasu arteritis", "Giant cell arteritis"}


def _offline_improve_checks(res: EvalResult) -> None:
    cands, gold = _improve_case()

    base = FB.ranked_labels(cands, FeedbackLedger())
    p_before = precision_at_k(base, gold, k=2)
    res.metrics["improve_precision_at2_before"] = p_before
    res.checks.append(Check(
        "improve(): baseline ranking surfaces a red herring in top-k (p@2=0.5)",
        p_before == 0.5 and base[1] == "Lymphoma",
        f"base={base[:2]} p@2={p_before}"))

    ledger = FeedbackLedger()
    ledger.add("Lymphoma", THUMBS_DOWN)            # clinician 👎 the red herring
    after = FB.ranked_labels(cands, ledger)
    p_after = precision_at_k(after, gold, k=2)
    res.metrics["improve_precision_at2_after"] = p_after
    res.checks.append(Check(
        "improve(): 👎 feedback raises precision@k and never regresses",
        p_after >= p_before and p_after == 1.0,
        f"after={after[:2]} p@2 {p_before} -> {p_after}"))

    # 👍 must not regress
    up = FeedbackLedger()
    up.add("Takayasu arteritis", THUMBS_UP)
    p_up = precision_at_k(FB.ranked_labels(cands, up), gold, k=2)
    res.checks.append(Check(
        "improve(): 👍 feedback does not regress precision@k",
        p_up >= p_before, f"p@2 up={p_up}"))

    # ruled-out negative memory: never re-suggested
    ro = FeedbackLedger()
    ro.rule_out("Lymphoma")
    ranked = FB.ranked_labels(cands, ro)
    res.checks.append(Check(
        "improve(): a ruled-out condition is suppressed and never re-suggested",
        "Lymphoma" not in ranked, f"ranked={ranked}"))


def _offline_forget_checks(res: EvalResult) -> None:
    """The forget-with-proof verdict is deterministic (robust yes/no parsing over the
    captured recalls), so the surgical/non-surgical logic is gated offline against the
    exact live answer phrasings."""
    # robust verdict parsing over the real live answer shapes
    res.checks.append(Check(
        "forget(): yes/no recall verdict parses robustly (before=yes, after=no)",
        FGT.verdict("**Answer: Yes.** The patient has Type 1 diabetes mellitus.") == "yes"
        and FGT.verdict("**Answer: No.**  Neither node mentions diabetes.") == "no"
        and FGT.mentions("The patient takes aspirin for cardiovascular protection.", "aspirin"),
        "verdict + mentions heuristics"))

    surgical = FGT.ForgetProof(
        dataset="d", data_id="x", forget_status="success",
        nodes_before=15, nodes_after=6, edges_before=13, edges_after=5,
        probe_query=FGT.BAD_QUERY, probe_term=FGT.BAD_TERM,
        probe_before="**Answer: Yes.** confirmed Type 1 diabetes mellitus.",
        probe_after="**Answer: No.** the graph only mentions cardiology and aspirin.",
        unrelated_query=FGT.KEEP_QUERY, unrelated_term=FGT.KEEP_TERM,
        unrelated_after="The patient takes aspirin for cardiovascular protection.")
    res.checks.append(Check(
        "forget(): a surgical forget is recognised (graph shrank, fact gone, unrelated kept)",
        surgical.is_surgical and surgical.nodes_removed == 9,
        f"proof={surgical.to_dict()}"))

    # a forget that did NOT remove the fact (still recallable) is NOT surgical
    leaky = FGT.ForgetProof(
        dataset="d", data_id="x", forget_status="success",
        nodes_before=15, nodes_after=15, edges_before=13, edges_after=13,
        probe_query=FGT.BAD_QUERY, probe_term=FGT.BAD_TERM,
        probe_before="**Answer: Yes.** confirmed diabetes.",
        probe_after="**Answer: Yes.** still confirmed diabetes.",
        unrelated_query=FGT.KEEP_QUERY, unrelated_term=FGT.KEEP_TERM,
        unrelated_after="The patient takes aspirin for cardiovascular protection.")
    res.checks.append(Check(
        "forget(): a non-removing forget is correctly rejected (fact still recallable)",
        not leaky.is_surgical and not leaky.graph_shrank and not leaky.probe_absent_after,
        "leaky forget rejected"))

    # collateral damage (unrelated fact destroyed) is NOT surgical
    collateral = FGT.ForgetProof(
        dataset="d", data_id="x", forget_status="success",
        nodes_before=15, nodes_after=2, edges_before=13, edges_after=1,
        probe_query=FGT.BAD_QUERY, probe_term=FGT.BAD_TERM,
        probe_before="**Answer: Yes.** confirmed diabetes.",
        probe_after="**Answer: No.** no diabetes.",
        unrelated_query=FGT.KEEP_QUERY, unrelated_term=FGT.KEEP_TERM,
        unrelated_after="**Answer: No.** the graph has no information about aspirin.")
    res.checks.append(Check(
        "forget(): collateral damage to unrelated memory is rejected (aspirin destroyed)",
        not collateral.is_surgical and not collateral.unrelated_survives,
        "collateral damage rejected"))


async def _live_checks(res: EvalResult) -> None:
    res.live = True
    settings = get_settings()
    client = CloudCogneeClient(settings)
    await client.connect()
    try:
        report = await P.provision(client, patient_id=_PATIENT)

        # roles created / resolved
        role_ids = {r.role: r.role_id for r in report.roles}
        res.metrics["rbac_role_ids"] = role_ids
        res.checks.append(Check(
            "provisioning resolves both provider + family role ids",
            all(role_ids.get(r) for r in (AppRole.PROVIDER, AppRole.FAMILY)),
            f"{role_ids}"))

        # grants: family got only the reference brains, provider got clinical too
        fam = next(r for r in report.roles if r.role == AppRole.FAMILY)
        prov = next(r for r in report.roles if r.role == AppRole.PROVIDER)
        fam_brains = {g["brain"] for g in fam.grants}
        prov_brains = {g["brain"] for g in prov.grants}
        res.metrics["rbac_family_grants"] = sorted(fam_brains)
        res.metrics["rbac_provider_grants"] = sorted(prov_brains)
        res.checks.append(Check(
            "family role is NOT granted the clinical brain (only reference brains)",
            BrainKind.CLINICAL not in fam_brains
            and {BrainKind.LITERATURE, BrainKind.TRIALS}.issubset(fam_brains),
            f"family grants={sorted(fam_brains)}"))
        res.checks.append(Check(
            "provider role IS granted the clinical brain plus the reference brains",
            {BrainKind.CLINICAL, BrainKind.LITERATURE, BrainKind.TRIALS} == prov_brains,
            f"provider grants={sorted(prov_brains)}"))
        res.checks.append(Check(
            "every provisioned grant was accepted by the Cloud (200)",
            report.all_grants_ok,
            f"{[(r.role, [g['ok'] for g in r.grants]) for r in report.roles]}"))

        # agent principals registered
        agent_ids = {a.name: a.principal_id for a in report.agents}
        res.metrics["rbac_agent_principals"] = agent_ids
        res.checks.append(Check(
            "all six swarm agents registered as principals with an id",
            set(agent_ids) == set(P.AGENT_BRAINS) and all(agent_ids.values()),
            f"{agent_ids}"))

        # roles are actually present in the tenant (read-back)
        live_roles = await client.list_roles()
        live_names = {r.get("name") for r in (live_roles or [])}
        res.metrics["rbac_live_role_names"] = sorted(n for n in live_names if n)
        res.checks.append(Check(
            "provider + family roles are readable back from the tenant",
            {"ariadne_provider", "ariadne_family"}.issubset(live_names),
            f"{sorted(n for n in live_names if n)}"))

        # persisted to the registry for the app/apps to consult
        persisted = registry.get_meta("rbac")
        res.checks.append(Check(
            "provisioning report persisted to the registry",
            bool(persisted) and bool(persisted.get("roles")) and bool(persisted.get("agents")),
            "registry _rbac written"))

        # live enforcement contract still holds against the real seeded brains
        fam_ds = P.guarded_datasets(AppRole.FAMILY, _PATIENT, BrainKind.CLINICAL)
        prov_ds = P.guarded_datasets(AppRole.PROVIDER, _PATIENT, BrainKind.CLINICAL)
        res.checks.append(Check(
            "enforcement boundary: family->clinical->[] , provider->clinical->dataset",
            fam_ds == [] and len(prov_ds) == 1,
            f"family={fam_ds} provider={prov_ds}"))

        await _live_sessions_checks(res, client)
        await _live_improve_checks(res, client)
        await _live_forget_checks(res, client)
    finally:
        await client.disconnect()


async def _live_sessions_checks(res: EvalResult, client: CloudCogneeClient) -> None:
    """Sessions attribution proven against the real tenant: every swarm agent that has
    ever run (P2/swarm gates exercise all six) is attributed on range='all', the
    aggregate token counters are populated, and one session's Q&A audit log is readable.
    """
    report = await SESS.observe(client, range="all", limit=200)
    res.metrics["sessions_total"] = report.total_sessions
    res.metrics["sessions_agents_seen"] = report.agents_seen
    res.metrics["sessions_by_agent"] = {a: att.session_count for a, att in report.by_agent.items()}
    res.metrics["sessions_tokens_total"] = report.tokens_total

    res.checks.append(Check(
        "Sessions: the tenant has attributed agent sessions",
        report.total_sessions > 0 and len(report.by_agent) > 0,
        f"total={report.total_sessions} agents={report.agents_seen}"))
    res.checks.append(Check(
        "Sessions: all six swarm agents are attributed (range=all)",
        report.all_agents_attributed,
        f"seen={report.agents_seen}"))
    res.checks.append(Check(
        "Sessions: aggregate token accounting is populated",
        report.tokens_total > 0,
        f"tokens_total={report.tokens_total}"))
    res.checks.append(Check(
        "Sessions: cost-by-model breakdown is present",
        len(report.cost_by_model) > 0,
        f"models={[m.get('model') for m in report.cost_by_model]}"))

    # one session's Q&A audit trail is readable (the who-asked-what audit record)
    raw = await client.sessions(range="all", limit=1)
    first = (raw.get("sessions") if isinstance(raw, dict) else raw) or []
    if first:
        sid = first[0].get("session_id")
        audit = await SESS.audit_trail(client, sid)
        res.metrics["sessions_audit_example"] = {
            "session_id": sid, "agent": audit.agent, "turns": audit.turn_count}
        res.checks.append(Check(
            "Sessions: a session exposes a Q&A audit trail (question + cited answer)",
            audit.turn_count >= 1 and bool(audit.turns[0].question) and bool(audit.turns[0].answer),
            f"agent={audit.agent} turns={audit.turn_count}"))


async def _live_improve_checks(res: EvalResult, client: CloudCogneeClient) -> None:
    """The improve() feedback loop proven live. Two provable-live facts, split by
    reliability:

      * GATED (reliable QA-capture path): a finding is persisted as a feedback-able QA
        that reads back carrying `used_graph_element_ids` (the exact graph nodes a
        later memify pass would reweight). This never touches the contended feedback
        dispatch, so it is a stable gate.
      * BEST-EFFORT (contended feedback dispatch): the 👎 score persists + the
        `feedback_weights_applied` memify-staging flag appears. The Cloud `remember`
        pipeline intermittently returns a transient 409 under shared-tenant lock
        contention (same family as TEMPORAL recall), so this is a NON-GATING check —
        PASS when it lands, `warn` when the tenant is contended.

    The deterministic app-level precision@k lift (`_offline_improve_checks`, always
    run) is the *gated* proof of measured self-improvement.
    """
    sid = f"ariadne-improve-eval-{int(time.time())}"
    qa_id = await FB.record_qa(
        client, session_id=sid,
        question="Is lymphoma a likely explanation for this patient's presentation?",
        answer="Lymphoma is a candidate but lower-ranked than large-vessel vasculitis.",
        used_node_ids=["node-a", "node-b"], context="connections eval probe")
    res.metrics["improve_live_qa_id"] = qa_id
    res.checks.append(Check(
        "improve(): a finding is persisted as a feedback-able QA (qa_id returned)",
        bool(qa_id), f"qa_id={qa_id}"))

    # GATED: the QA reads back with the nodes to reweight — no feedback POST needed.
    pre = await FB.read_feedback_state(client, session_id=sid, qa_id=qa_id)
    res.metrics["improve_live_qa_used_nodes"] = pre.used_node_ids
    res.checks.append(Check(
        "improve(): the QA reads back recording used_graph_element_ids (nodes to reweight)",
        pre.found and len(pre.used_node_ids) >= 1,
        f"found={pre.found} used_node_ids={pre.used_node_ids}"))

    # BEST-EFFORT: chain a 👎 and read the score back. Transient 409 -> non-gating warn.
    landed, state = await FB.try_submit_feedback(
        client, session_id=sid, qa_id=qa_id, score=THUMBS_DOWN,
        text="down-weight: red herring", retries=3)
    res.metrics["improve_live_feedback_landed"] = landed
    res.metrics["improve_live_feedback_state"] = state.to_dict()
    if landed:
        res.checks.append(Check(
            "improve(): 👎 feedback persists to the QA (score read back = -1)",
            state.found and state.score == THUMBS_DOWN,
            f"state={state.to_dict()}", gating=False))
        res.checks.append(Check(
            "improve(): memify staging flag present on the QA (feedback_weights_applied)",
            state.weights_applied is not None,
            f"feedback_weights_applied={state.weights_applied}", gating=False))
        # batch improve_findings round-trips (👍 + 👎) — best-effort too.
        q_up = await FB.record_qa(client, session_id=sid,
                                  question="Is Takayasu the confirmed diagnosis?",
                                  answer="Yes, confirmed 2024-03-01.", used_node_ids=["node-c"])
        up_landed, _ = await FB.try_submit_feedback(
            client, session_id=sid, qa_id=q_up, score=THUMBS_UP, text="correct", retries=3)
        res.checks.append(Check(
            "improve(): batch improve_findings persists feedback (👍 + 👎)",
            up_landed, f"up_landed={up_landed}", gating=False))
    else:
        res.checks.append(Check(
            "improve(): live feedback dispatch (best-effort; transient 409 tolerated)",
            False,
            "shared-tenant remember lock contended right now — QA capture + app-level "
            "precision@k lift are the gated proof; feedback verb verified separately",
            gating=False))


async def _live_forget_checks(res: EvalResult, client: CloudCogneeClient) -> None:
    """forget-with-proof, proven live on a **disposable** dataset (never the hero brain):
    seed a KEEP doc + a deliberately mislabeled BAD doc, forget the BAD data_id, and
    assert the graph shrank, the mislabeled fact is no longer recallable, and the
    unrelated fact survives. The dataset is deleted afterward (healthy datasets delete
    cleanly)."""
    dataset = f"ariadne_forget_eval__{time.strftime('%Y%m%d%H%M%S')}"
    graph_model = clinical_graph_model_json()
    dataset_id: str = ""
    try:
        dataset_id, _keep_id, bad_id = await FGT.seed_forget_fixture(
            client, dataset=dataset, graph_model=graph_model,
            custom_prompt=CUSTOM_EXTRACTION_PROMPT)
        res.checks.append(Check(
            "forget(): disposable fixture seeded (KEEP + mislabeled BAD records)",
            bool(dataset_id) and bool(bad_id), f"dataset_id={dataset_id} bad_id={bad_id}"))
        if not (dataset_id and bad_id):
            return

        proof = await FGT.prove_forget(
            client, dataset=dataset, dataset_id=dataset_id, data_id=bad_id)
        res.metrics["forget_proof"] = proof.to_dict()

        res.checks.append(Check(
            "forget(): the mislabeled fact was recallable before deletion",
            proof.probe_present_before, f"before={proof.probe_before[:80]!r}"))
        res.checks.append(Check(
            "forget(): the Cloud reported the deletion succeeded",
            proof.deletion_succeeded, f"status={proof.forget_status}"))
        res.checks.append(Check(
            "forget(): the graph shrank (nodes removed by the surgical delete)",
            proof.graph_shrank,
            f"nodes {proof.nodes_before}->{proof.nodes_after} (removed {proof.nodes_removed})"))
        res.checks.append(Check(
            "forget(): the mislabeled fact is no longer recallable (Yes -> No)",
            proof.probe_absent_after, f"after={proof.probe_after[:80]!r}"))
        res.checks.append(Check(
            "forget(): unrelated memory survives (aspirin still recallable)",
            proof.unrelated_survives, f"unrelated={proof.unrelated_after[:80]!r}"))
        res.checks.append(Check(
            "forget(): the delete is surgical (fact gone, graph shrank, unrelated kept)",
            proof.is_surgical, f"proof={proof.to_dict()}"))
    finally:
        if dataset_id:
            try:
                await client.delete_dataset(dataset_id)
            except Exception:
                pass


async def run_p3(offline: bool = False) -> EvalResult:
    res = EvalResult(phase="p3")
    _offline_checks(res)
    _offline_sessions_checks(res)
    _offline_improve_checks(res)
    _offline_forget_checks(res)
    if not offline:
        await _live_checks(res)
    return res
