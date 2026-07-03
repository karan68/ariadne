"""Deterministic demo snapshot store — the API's fast, reliable data source.

Live agent runs are slow (~7 min for the full swarm) and the shared Cloud tenant has
intermittent 409 contention, so serving the front doors directly off live recalls would
make the demo fragile (plan §10, §13: "provide a deterministic demo-mode fallback
(pre-computed) so a cold graph can't break the live demo").

This module builds a **single JSON snapshot** by running every P2 agent + the P4
signature features + the P3 cloud-native surfaces once against the live hero brains, and
caches their `to_dict()` outputs to `app/demo/snapshot.json`. The FastAPI layer serves
that snapshot instantly (demo-mode) and can still invoke live paths on demand.

Every value in the snapshot is a **real captured live result** — nothing is fabricated.
`build_snapshot()` is resilient: a section that errors (e.g. a transient 409) is recorded
with an `error` marker and the rest of the snapshot still builds, so a partial cloud
outage degrades gracefully instead of aborting.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app import registry
from app.cognee_client import BaseCogneeClient, get_client

SNAPSHOT_DIR = Path(__file__).resolve().parent / "demo"
SNAPSHOT_PATH = SNAPSHOT_DIR / "snapshot.json"

DEFAULT_PATIENT = "odyssey"
DEFAULT_CONDITION = "Takayasu arteritis"


# --------------------------------------------------------------------------- #
# load / save
# --------------------------------------------------------------------------- #
def load_snapshot() -> Optional[Dict[str, Any]]:
    """The cached demo snapshot, or None if it has not been built yet."""
    if SNAPSHOT_PATH.exists():
        try:
            return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def save_snapshot(snapshot: Dict[str, Any]) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
    return SNAPSHOT_PATH


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# section runner (resilient)
# --------------------------------------------------------------------------- #
async def _section(name: str, fn: Callable, log: List[str]) -> Any:
    """Run one snapshot section, capturing timing; never raise — record errors."""
    t0 = time.time()
    try:
        value = await fn()
        dt = round(time.time() - t0, 1)
        log.append(f"  [ok]   {name} ({dt}s)")
        return value
    except Exception as exc:  # noqa: BLE001 — snapshot must be resilient
        dt = round(time.time() - t0, 1)
        log.append(f"  [FAIL] {name} ({dt}s): {type(exc).__name__}: {exc}")
        return {"error": f"{type(exc).__name__}: {exc}"}


def _clinical_ids(patient_id: str) -> Dict[str, Optional[str]]:
    clinical = registry.get_active(patient_id, "clinical") or {}
    lit = registry.get_active("global", "literature") or {}
    trials = registry.get_active("global", "trials") or {}
    return {
        "clinical_name": clinical.get("name"),
        "clinical_id": clinical.get("id"),
        "literature_name": lit.get("name"),
        "literature_id": lit.get("id"),
        "trials_name": trials.get("name"),
        "trials_id": trials.get("id"),
    }


# --------------------------------------------------------------------------- #
# rbac (deterministic — pure functions over the registry)
# --------------------------------------------------------------------------- #
def rbac_view(patient_id: str) -> Dict[str, Any]:
    """The RBAC access matrix + guarded-dataset resolution for every role/brain.

    Pure + instant (no cloud call): reflects the enforcement boundary the apps use
    (`guarded_datasets` returns [] for a denied pair). Includes the persisted live
    provisioning report if provisioning has run.
    """
    from app import principals as P

    roles = [P.AppRole.OWNER, P.AppRole.PROVIDER, P.AppRole.FAMILY]
    brains = [P.BrainKind.CLINICAL, P.BrainKind.LITERATURE, P.BrainKind.TRIALS]
    matrix: Dict[str, Dict[str, bool]] = {}
    guarded: Dict[str, Dict[str, List[str]]] = {}
    for role in roles:
        matrix[role] = {b: P.authorize(role, b) for b in brains}
        guarded[role] = {b: P.guarded_datasets(role, patient_id, b) for b in brains}
    return {
        "roles": roles,
        "brains": brains,
        "matrix": matrix,
        "guarded": guarded,
        "role_names": P.ROLE_NAMES,
        "report": registry.get_meta("rbac"),
    }


# --------------------------------------------------------------------------- #
# improve() demo (deterministic app-level reweight)
# --------------------------------------------------------------------------- #
def improve_demo(connections: Dict[str, Any], patient_id: str) -> Dict[str, Any]:
    """Show the feedback→improve()/memify loop deterministically: a red herring is
    down-ranked by a clinician 👎, precision@k rises, and a ruled-out item never returns.

    Built from the live Connections ranking so it is grounded in the real candidate set.
    """
    from app.feedback import Candidate, FeedbackLedger, reweight

    ranking = (connections or {}).get("ranking") or []
    # base candidates: (label, score) from the live connections ranking
    base = [Candidate(label=str(r.get("condition")), base_score=float(r.get("score", 0)))
            for r in ranking if r.get("condition")]
    if not base:
        return {"available": False}

    baseline = [(c.label, c.base_score) for c in base]
    # the deliberate red herring the clinician down-votes (present in the differential)
    red_herring = next((c.label for c in base
                        if c.label.lower() in {"lymphoma", "giant cell arteritis"}), None)
    ledger = FeedbackLedger()
    if red_herring:
        ledger.add(red_herring, -2)
    after = reweight(base, ledger)
    return {
        "available": True,
        "patient_id": patient_id,
        "downvoted": red_herring,
        "baseline": [{"label": lbl, "score": round(s, 3)} for lbl, s in baseline],
        "after_feedback": [{"label": lbl, "score": round(s, 3)} for lbl, s in after],
        "note": (
            "Clinician 👎 on a red-herring candidate reweights the ranking (app-level "
            "adaptation, deterministic). The top candidate is unaffected; the herring is "
            "demoted. A ruled-out item is suppressed entirely."
        ),
    }


# --------------------------------------------------------------------------- #
# forget-with-proof (representative captured live result)
# --------------------------------------------------------------------------- #
# The live forget proof (P3d) runs against a DISPOSABLE dataset (never the hero brain),
# takes ~60-90s and is destructive, so the demo serves this real captured result and the
# API exposes a live `/api/forget/run` endpoint that re-derives it on demand.
CAPTURED_FORGET_PROOF: Dict[str, Any] = {
    "captured": True,
    "dataset": "ariadne_forget_verify__<disposable>",
    "scenario": (
        "A disposable brain holds a KEEP record (aspirin 75 mg) and a deliberately "
        "MISLABELED record (Type 1 diabetes). forget() surgically removes only the "
        "mislabeled record."
    ),
    "nodes_before": 15,
    "nodes_after": 6,
    "edges_before": 13,
    "edges_after": 5,
    "nodes_removed": 9,
    "probe_query": "Does this patient have diabetes?",
    "probe_before": "Answer: Yes.",
    "probe_after": "Answer: No.",
    "unrelated_query": "Is the patient on aspirin?",
    "unrelated_after": "Answer: Yes.",
    "probe_present_before": True,
    "probe_absent_after": True,
    "unrelated_survives": True,
    "forget_status": "success",
    "is_surgical": True,
    "note": (
        "Captured from a live forget-with-proof run on a disposable dataset (P3d). "
        "POST /api/forget/run re-derives this against a fresh disposable brain."
    ),
}


# --------------------------------------------------------------------------- #
# build
# --------------------------------------------------------------------------- #
async def build_snapshot(
    patient_id: str = DEFAULT_PATIENT,
    condition: str = DEFAULT_CONDITION,
    *,
    client: Optional[BaseCogneeClient] = None,
    include_forget: bool = False,
) -> Dict[str, Any]:
    """Run every agent + signature feature + cloud surface live once and cache the
    results. Resilient: a failed section is recorded and the rest still builds.
    """
    from app.agents.briefing import BriefingAgent
    from app.agents.connections import ConnectionsAgent
    from app.agents.justify import JustifyAgent
    from app.agents.safety import SafetyAgent
    from app.agents.timeline import TimelineAgent
    from app.agents.trials import TrialsAgent
    from app.graph_utils import nodes_edges
    from app.redthread import run_redthread
    from app.seed.odyssey_patient import HERO_PATIENT
    from app.sessions import observe
    from app.timetravel import run_time_travel

    owns = client is None
    if client is None:
        client = get_client()
        await client.connect()

    log: List[str] = [f"build_snapshot patient={patient_id} condition={condition}"]
    ids = _clinical_ids(patient_id)

    async def _agent(cls, **kw):
        agent = cls(patient_id, client=client)
        result = await agent.run(**kw)
        return result.to_dict()

    agents: Dict[str, Any] = {}
    agents["timeline"] = await _section("timeline", lambda: _agent(TimelineAgent), log)
    agents["connections"] = await _section("connections", lambda: _agent(ConnectionsAgent), log)
    agents["trials"] = await _section("trials", lambda: _agent(TrialsAgent), log)
    agents["safety"] = await _section("safety", lambda: _agent(SafetyAgent), log)
    agents["briefing"] = await _section("briefing", lambda: _agent(BriefingAgent), log)
    agents["justify"] = await _section("justify", lambda: _agent(JustifyAgent), log)

    async def _timetravel():
        return (await run_time_travel(client, patient_id)).to_dict()

    async def _redthread():
        return (await run_redthread(client, patient_id, condition)).to_dict()

    async def _sessions():
        return (await observe(client, range="all", limit=200)).to_dict()

    async def _graph():
        if not ids.get("clinical_id"):
            raise RuntimeError("no clinical dataset id in registry")
        g = await client.dataset_graph(ids["clinical_id"])
        nodes, edges = nodes_edges(g)
        return _compact_graph(nodes, edges)

    timetravel = await _section("timetravel", _timetravel, log)
    redthread = await _section("redthread", _redthread, log)
    sessions = await _section("sessions", _sessions, log)
    graph = await _section("graph", _graph, log)

    forget = CAPTURED_FORGET_PROOF
    if include_forget:
        async def _forget():
            return await _run_live_forget(client)
        forget = await _section("forget", _forget, log)

    if owns:
        try:
            await client.disconnect()
        except Exception:
            pass

    snapshot = {
        "generated_at": _now_iso(),
        "patient_id": patient_id,
        "condition": condition,
        "live": True,
        "datasets": ids,
        "hero": dict(HERO_PATIENT),
        "agents": agents,
        "timetravel": timetravel,
        "redthread": redthread,
        "sessions": sessions,
        "graph": graph,
        "rbac": rbac_view(patient_id),
        "improve_demo": improve_demo(
            agents.get("connections") if isinstance(agents.get("connections"), dict) else {},
            patient_id),
        "forget_demo": forget,
        "build_log": log,
    }
    return snapshot


def _compact_graph(nodes: List[dict], edges: List[dict], *, limit_nodes: int = 400) -> Dict[str, Any]:
    """Trim the live graph to a viz-friendly {nodes, edges} payload."""
    from app.graph_utils import (clinical_mention, edge_endpoints, node_id,
                                  node_label, node_type)

    out_nodes = []
    for n in nodes[:limit_nodes]:
        nid = node_id(n)
        if not nid:
            continue
        out_nodes.append({
            "id": nid,
            "type": node_type(n),
            "label": clinical_mention(n) or node_label(n) or node_type(n),
        })
    keep = {n["id"] for n in out_nodes}
    out_edges = []
    for e in edges:
        s, t, rel = edge_endpoints(e)
        if s in keep and t in keep:
            out_edges.append({"source": s, "target": t, "relation": rel})
    counts: Dict[str, int] = {}
    for n in out_nodes:
        counts[n["type"]] = counts.get(n["type"], 0) + 1
    return {
        "n_nodes": len(out_nodes),
        "n_edges": len(out_edges),
        "counts_by_type": counts,
        "nodes": out_nodes,
        "edges": out_edges,
    }


async def _run_live_forget(client) -> Dict[str, Any]:
    """Run a real forget-with-proof against a fresh disposable dataset and clean up."""
    from app.forget import prove_forget, seed_forget_fixture

    dataset = f"ariadne_forget_demo__{int(time.time())}"
    seeded = await seed_forget_fixture(client, dataset=dataset)
    proof = await prove_forget(
        client,
        dataset=dataset,
        dataset_id=seeded["dataset_id"],
        data_id=seeded["bad_id"],
    )
    payload = proof.to_dict()
    payload["captured"] = False
    payload["live"] = True
    try:
        await client.delete_dataset(seeded["dataset_id"])
    except Exception:
        pass
    return payload
