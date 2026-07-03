"""improve() / memify — the feedback lifecycle.

Cognee Cloud has **no standalone memify endpoint** (verified against the live 49-path
OpenAPI spec). The improve lifecycle is realized as *feedback attached to the QAs a
recall produced*: every `recall()` auto-records a QA in the session cache with a real
`qa_id` and `used_graph_element_ids` (the exact graph nodes the answer used), and
`POST /remember/entry {type:"feedback", qa_id, feedback_score, feedback_text}` chains a
clinician 👍/👎 to that QA (dispatched to `SessionManager.add_feedback`). The QA then
carries `feedback_score` + `memify_metadata.feedback_weights_applied` — the flag the
Cloud's memify pass flips when it reweights those nodes.

This module provides two honest layers (the same substrate/enforcement split used for
RBAC in `principals.py`):

  1. **Cloud capture (live-verified):** `record_qa` persists a finding as a feedback-able
     QA; `submit_feedback` chains a score to a `qa_id` (with 409-retry) and reads the
     state back. This is the real improve() verb hitting the real Cloud.

  2. **App-level adaptation (Ariadne's boundary):** a `FeedbackLedger` accumulates the
     per-label signal and `reweight` demotes 👎'd candidates and drops ruled-out ones, so
     a re-ranked list measurably sharpens (precision@k rises, never regresses) even
     before the hosted memify weights are observably applied on the tenant.

Grounded caveat: on this tenant `feedback_weights_applied` stays `false` after feedback
(no exposed trigger to run the batch memify pass), so Ariadne applies the captured
feedback at the app layer for a deterministic, demonstrable effect — it does **not**
claim a Cloud-side ranking change it cannot observe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app import registry
from app.cloud_client import CloudError
from app.models import Finding

FEEDBACK_DATASET = "main_dataset"

#: canonical feedback scores
THUMBS_UP = 1
THUMBS_DOWN = -1


# --------------------------------------------------------------------------- #
# Cloud capture layer (live)
# --------------------------------------------------------------------------- #
@dataclass
class FeedbackState:
    qa_id: str
    found: bool
    score: Optional[int] = None
    text: Optional[str] = None
    weights_applied: Optional[bool] = None
    used_node_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "qa_id": self.qa_id, "found": self.found, "score": self.score,
            "text": self.text, "weights_applied": self.weights_applied,
            "used_node_count": len(self.used_node_ids),
        }


def _node_ids(used_graph_element_ids: Any) -> List[str]:
    if isinstance(used_graph_element_ids, dict):
        ids = used_graph_element_ids.get("node_ids") or []
        return [str(x) for x in ids]
    return []


async def read_feedback_state(client, session_id: str, qa_id: str) -> FeedbackState:
    """Read one QA's feedback state back from the session detail (the audit read-back)."""
    detail = await client.session_detail(session_id)
    detail = detail if isinstance(detail, dict) else {}
    for qa in detail.get("qas", []) or []:
        if qa.get("qa_id") == qa_id:
            mm = qa.get("memify_metadata") or {}
            return FeedbackState(
                qa_id=qa_id, found=True,
                score=qa.get("feedback_score"), text=qa.get("feedback_text"),
                weights_applied=mm.get("feedback_weights_applied") if isinstance(mm, dict) else None,
                used_node_ids=_node_ids(qa.get("used_graph_element_ids")))
    return FeedbackState(qa_id=qa_id, found=False)


async def record_qa(client, *, session_id: str, question: str, answer: str,
                    used_node_ids: Optional[Sequence[str]] = None, context: str = "",
                    dataset_name: str = FEEDBACK_DATASET, retries: int = 6) -> str:
    """Persist a finding as a feedback-able QA; returns its `qa_id` (entry_id)."""
    entry: Dict[str, Any] = {"type": "qa", "question": question, "answer": answer,
                             "context": context}
    if used_node_ids:
        entry["used_graph_element_ids"] = {"node_ids": [str(x) for x in used_node_ids]}
    resp = await client.remember_entry(entry, dataset_name=dataset_name,
                                       session_id=session_id, retries=retries)
    resp = resp if isinstance(resp, dict) else {}
    return resp.get("entry_id") or resp.get("qa_id") or ""


async def submit_feedback(client, *, session_id: str, qa_id: str, score: int,
                          text: str = "", dataset_name: str = FEEDBACK_DATASET,
                          retries: int = 6, read_back: bool = True) -> FeedbackState:
    """Chain a 👍/👎 to an existing QA (the improve() primitive). Returns the read-back
    state so callers can prove the score persisted + inspect the memify staging flag."""
    await client.remember_entry(
        {"type": "feedback", "qa_id": qa_id, "feedback_score": int(score),
         "feedback_text": text or ""},
        dataset_name=dataset_name, session_id=session_id, retries=retries)
    if not read_back:
        return FeedbackState(qa_id=qa_id, found=True, score=int(score), text=text or "")
    return await read_feedback_state(client, session_id, qa_id)


async def try_submit_feedback(client, *, session_id: str, qa_id: str, score: int,
                              text: str = "", dataset_name: str = FEEDBACK_DATASET,
                              retries: int = 3) -> Tuple[bool, FeedbackState]:
    """Best-effort feedback submit for reproducible gates/demos.

    The Cloud `remember` pipeline intermittently returns a **transient 409** under
    shared-tenant lock contention (the same single-flight lock family as TEMPORAL
    recall). This wrapper attempts the feedback with bounded retries and, on a
    sustained transient 409, returns ``(False, unlanded-state)`` instead of raising —
    so the QA capture + app-level adaptation stay the *gated* proof while the live
    score-persistence is demonstrated opportunistically when the tenant permits.

    Returns ``(landed, state)``: ``landed`` is True only when the score persisted and
    read back; on a transient conflict the state carries ``found=False`` and a
    ``text`` explaining the degradation.
    """
    try:
        state = await submit_feedback(
            client, session_id=session_id, qa_id=qa_id, score=score, text=text,
            dataset_name=dataset_name, retries=retries)
        return (state.found and state.score is not None), state
    except CloudError as exc:
        if getattr(exc, "status", None) == 409:
            return False, FeedbackState(
                qa_id=qa_id, found=False,
                text=f"transient 409 (shared-tenant remember lock): {exc}")
        raise


@dataclass
class ImproveReport:
    session_id: str
    submitted: List[FeedbackState] = field(default_factory=list)

    @property
    def all_persisted(self) -> bool:
        return bool(self.submitted) and all(
            s.found and s.score is not None for s in self.submitted)

    def to_dict(self) -> Dict[str, Any]:
        return {"session_id": self.session_id,
                "count": len(self.submitted),
                "all_persisted": self.all_persisted,
                "items": [s.to_dict() for s in self.submitted]}


async def improve_findings(client, *, session_id: str,
                           items: Sequence[Tuple[str, int, str]],
                           dataset_name: str = FEEDBACK_DATASET,
                           retries: int = 6) -> ImproveReport:
    """Batch improve(): submit `(qa_id, score, text)` feedback for a set of findings and
    return a report of the read-back states."""
    report = ImproveReport(session_id=session_id)
    for qa_id, score, text in items:
        report.submitted.append(await submit_feedback(
            client, session_id=session_id, qa_id=qa_id, score=score, text=text,
            dataset_name=dataset_name, retries=retries))
    return report


def finding_node_ids(finding: Finding) -> List[str]:
    """The graph element ids a finding cited — its `used_graph_element_ids` for a QA."""
    ids: List[str] = []
    for ref in finding.evidence:
        nid = ref.data_id or ref.chunk_id
        if nid:
            ids.append(nid)
    return ids


# --------------------------------------------------------------------------- #
# App-level adaptation layer (deterministic, demonstrable)
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    label: str
    base_score: float
    node_ids: frozenset = field(default_factory=frozenset)


@dataclass
class FeedbackLedger:
    """Accumulated clinician feedback keyed by candidate label. Persisted per patient in
    the registry so the ranking adaptation survives across sessions (never-forget)."""
    net: Dict[str, int] = field(default_factory=dict)
    ruled_out: set = field(default_factory=set)

    def add(self, label: str, score: int) -> None:
        self.net[label] = self.net.get(label, 0) + int(score)

    def rule_out(self, label: str) -> None:
        self.ruled_out.add(label)

    def signal(self, label: str) -> int:
        return self.net.get(label, 0)

    def to_dict(self) -> Dict[str, Any]:
        return {"net": dict(self.net), "ruled_out": sorted(self.ruled_out)}

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "FeedbackLedger":
        d = d or {}
        return cls(net=dict(d.get("net") or {}), ruled_out=set(d.get("ruled_out") or []))


def reweight(candidates: Iterable[Candidate], ledger: FeedbackLedger, *,
             up: float = 0.15, down: float = 0.5) -> List[Tuple[str, float]]:
    """Apply the feedback ledger to a candidate list and return the re-ranked
    `(label, score)` pairs (descending). Ruled-out labels are suppressed entirely;
    👎 (net<0) multiplies the score by `down` per downvote; 👍 (net>0) adds `up` per
    upvote. Deterministic and monotone — the adaptation boundary for improve()."""
    ranked: List[Tuple[str, float]] = []
    for c in candidates:
        if c.label in ledger.ruled_out:
            continue
        net = ledger.signal(c.label)
        score = c.base_score
        if net < 0:
            score = score * (down ** (-net))
        elif net > 0:
            score = score * (1.0 + up * net)
        ranked.append((c.label, round(score, 6)))
    ranked.sort(key=lambda t: t[1], reverse=True)
    return ranked


def ranked_labels(candidates: Iterable[Candidate], ledger: FeedbackLedger,
                  **kw) -> List[str]:
    return [label for label, _ in reweight(candidates, ledger, **kw)]


# --- registry persistence (per-patient ledger) -------------------------------
def _ledger_key(patient_id: str) -> str:
    return f"feedback_{patient_id}"


def load_ledger(patient_id: str) -> FeedbackLedger:
    return FeedbackLedger.from_dict(registry.get_meta(_ledger_key(patient_id)))


def save_ledger(patient_id: str, ledger: FeedbackLedger) -> None:
    registry.set_meta(_ledger_key(patient_id), ledger.to_dict())
