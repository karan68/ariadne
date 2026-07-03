"""Ariadne backend API (FastAPI) — the front-door API for the patient + clinician apps.

Design (plan §8, §10, §13): the API is **snapshot-backed by default**. A single
deterministic snapshot (`app/demo_store.py`, built live once) makes every endpoint
instant and demo-proof — a cold/contended cloud can never break the demo. Endpoints that
are fast + deterministic (RBAC enforcement, the improve() reweight) compute live from the
registry/snapshot; the slow/destructive live paths (full agent re-run, forget) are opt-in.

One app, role-switched (plan §1a, §2): the same brains back both front doors; the
`/api/rbac/check` endpoint is the enforcement boundary that returns `[]` for a denied
(role, brain) pair, so a family principal literally cannot see the clinical brain.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import demo_store
from .config import get_settings

app = FastAPI(
    title="Ariadne",
    version="1.0.0",
    description="A patient-owned clinical memory & insight layer on Cognee Cloud.",
)

# The React front doors run on the Vite dev server; allow local origins in dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# snapshot access
# --------------------------------------------------------------------------- #
def _snapshot() -> Dict[str, Any]:
    snap = demo_store.load_snapshot()
    if snap is None:
        raise HTTPException(
            status_code=503,
            detail="demo snapshot not built — run `python -m scripts.build_snapshot`",
        )
    return snap


def _section(name: str) -> Any:
    snap = _snapshot()
    if name in snap:
        return snap[name]
    agents = snap.get("agents", {})
    if name in agents:
        return agents[name]
    raise HTTPException(status_code=404, detail=f"no '{name}' in snapshot")


# --------------------------------------------------------------------------- #
# health / config / meta
# --------------------------------------------------------------------------- #
@app.get("/health")
def health() -> dict:
    s = get_settings()
    snap = demo_store.load_snapshot()
    return {
        "status": "ok",
        "service": "ariadne",
        "cognee_mode": s.mode,
        "snapshot": bool(snap),
        "snapshot_generated_at": (snap or {}).get("generated_at"),
    }


@app.get("/config")
def config() -> dict:
    s = get_settings()
    return {
        "cognee_mode": s.mode,
        "llm_model": s.llm_model,
        "embedding_model": s.embedding_model,
        "embedding_dimensions": s.embedding_dimensions,
        "reference_literature": s.reference_literature,
        "reference_trials": s.reference_trials,
        "min_confidence": s.min_confidence,
    }


@app.get("/api/snapshot")
def snapshot() -> dict:
    """The whole demo snapshot (the frontend can hydrate from one call)."""
    return _snapshot()


@app.get("/api/meta")
def meta() -> dict:
    snap = _snapshot()
    return {
        "generated_at": snap.get("generated_at"),
        "patient_id": snap.get("patient_id"),
        "condition": snap.get("condition"),
        "live": snap.get("live"),
        "datasets": snap.get("datasets"),
        "hero": snap.get("hero"),
        "build_log": snap.get("build_log"),
    }


# --------------------------------------------------------------------------- #
# patient / clinician read surfaces (snapshot-backed)
# --------------------------------------------------------------------------- #
@app.get("/api/patient/{patient_id}")
def patient(patient_id: str) -> dict:
    snap = _snapshot()
    return {"patient_id": snap.get("patient_id"), "hero": snap.get("hero"),
            "datasets": snap.get("datasets")}


@app.get("/api/patient/{patient_id}/timeline")
def timeline(patient_id: str) -> Any:
    return _section("timeline")


@app.get("/api/patient/{patient_id}/briefing")
def briefing(patient_id: str) -> Any:
    return _section("briefing")


@app.get("/api/patient/{patient_id}/connections")
def connections(patient_id: str) -> Any:
    return _section("connections")


@app.get("/api/patient/{patient_id}/trials")
def trials(patient_id: str) -> Any:
    return _section("trials")


@app.get("/api/patient/{patient_id}/safety")
def safety(patient_id: str) -> Any:
    return _section("safety")


@app.get("/api/patient/{patient_id}/justify")
def justify(patient_id: str) -> Any:
    return _section("justify")


@app.get("/api/patient/{patient_id}/timetravel")
def timetravel(patient_id: str) -> Any:
    return _section("timetravel")


@app.get("/api/patient/{patient_id}/redthread")
def redthread(patient_id: str) -> Any:
    return _section("redthread")


@app.get("/api/patient/{patient_id}/graph")
def graph(patient_id: str) -> Any:
    return _section("graph")


# --------------------------------------------------------------------------- #
# RBAC (deterministic live — the enforcement boundary)
# --------------------------------------------------------------------------- #
@app.get("/api/rbac")
def rbac() -> Any:
    """The full access matrix + guarded datasets + the live provisioning report."""
    return _snapshot().get("rbac") or demo_store.rbac_view("odyssey")


class RbacCheck(BaseModel):
    role: str
    brain: str
    patient_id: str = "odyssey"


@app.post("/api/rbac/check")
def rbac_check(body: RbacCheck) -> dict:
    """Resolve the datasets a (role, brain) recall may target — `[]` if denied.

    This is the live enforcement point: a family principal asking for the clinical
    brain gets `[]` (access denied), a provider gets the dataset name.
    """
    from app import principals as P

    allowed = P.authorize(body.role, body.brain)
    datasets = P.guarded_datasets(body.role, body.patient_id, body.brain)
    return {
        "role": body.role,
        "brain": body.brain,
        "patient_id": body.patient_id,
        "authorized": allowed,
        "datasets": datasets,
        "denied": not allowed,
        "explanation": (
            f"{body.role} is authorized to read the {body.brain} brain."
            if allowed else
            f"{body.role} is NOT authorized for the {body.brain} brain — recall returns []."
        ),
    }


# --------------------------------------------------------------------------- #
# Sessions observability
# --------------------------------------------------------------------------- #
@app.get("/api/sessions")
def sessions() -> Any:
    return _section("sessions")


@app.get("/api/sessions/{session_id}/audit")
async def session_audit(session_id: str) -> dict:
    """Live per-session Q&A audit trail (best-effort; the write path is reliable)."""
    from app.cognee_client import get_client
    from app.sessions import audit_trail

    client = get_client()
    try:
        await client.connect()
        audit = await audit_trail(client, session_id)
        return {
            "session_id": audit.session_id, "agent": audit.agent,
            "patient": audit.patient, "label": audit.label,
            "turn_count": audit.turn_count,
            "turns": [{"time": t.time, "question": t.question, "answer": t.answer}
                      for t in audit.turns],
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"session audit failed: {exc}")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# improve() feedback loop (deterministic app-level reweight)
# --------------------------------------------------------------------------- #
@app.get("/api/improve")
def improve() -> Any:
    """The precomputed improve() demo from the snapshot."""
    return _snapshot().get("improve_demo") or {"available": False}


class ImproveRequest(BaseModel):
    downvote: Optional[str] = None
    upvote: Optional[str] = None
    rule_out: Optional[str] = None
    patient_id: str = "odyssey"


@app.post("/api/improve")
def improve_run(body: ImproveRequest) -> dict:
    """Apply a clinician 👍/👎/ruled-out to the live Connections ranking and re-rank.

    Deterministic app-level adaptation (the observable improve()/memify boundary): the
    top candidate is unaffected, a down-voted red herring is demoted, a ruled-out item
    is suppressed entirely.
    """
    from app.feedback import Candidate, FeedbackLedger, reweight

    conn = _section("connections")
    ranking = (conn or {}).get("ranking") or []
    base = [Candidate(label=str(r.get("condition")), base_score=float(r.get("score", 0)))
            for r in ranking if r.get("condition")]
    if not base:
        raise HTTPException(status_code=404, detail="no connections ranking in snapshot")

    ledger = FeedbackLedger()
    if body.downvote:
        ledger.add(body.downvote, -2)
    if body.upvote:
        ledger.add(body.upvote, +2)
    if body.rule_out:
        ledger.rule_out(body.rule_out)

    baseline = [{"label": c.label, "score": round(c.base_score, 3)} for c in base]
    after = [{"label": lbl, "score": round(s, 3)} for lbl, s in reweight(base, ledger)]
    return {
        "patient_id": body.patient_id,
        "downvoted": body.downvote,
        "upvoted": body.upvote,
        "ruled_out": body.rule_out,
        "baseline": baseline,
        "after_feedback": after,
    }


# --------------------------------------------------------------------------- #
# forget-with-proof
# --------------------------------------------------------------------------- #
@app.get("/api/forget")
def forget() -> Any:
    """The captured forget-with-proof result (served for the demo)."""
    return _snapshot().get("forget_demo") or demo_store.CAPTURED_FORGET_PROOF


@app.post("/api/forget/run")
async def forget_run() -> dict:
    """Run a REAL forget-with-proof against a fresh disposable dataset (never the hero
    brain) and clean up. Slow (~60-90s) + destructive-on-a-throwaway; opt-in."""
    from app.cognee_client import get_client

    client = get_client()
    try:
        await client.connect()
        return await demo_store._run_live_forget(client)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"live forget failed: {exc}")
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
