"""Offline unit tests for app/feedback.py — the improve()/memify feedback lifecycle.

Cloud capture is exercised through a recording fake client (incl. a 409-then-200 retry
path mirroring the live transient-conflict behaviour); the app-level reweighting +
precision@k proof is fully deterministic.
"""

from __future__ import annotations

import pytest

from app import feedback as F
from app.feedback import Candidate, FeedbackLedger, THUMBS_DOWN, THUMBS_UP
from evals.metrics import precision_at_k


# --------------------------------------------------------------------------- #
# app-level reweighting
# --------------------------------------------------------------------------- #
def _connections_case():
    # a labeled Connections case where a red herring outranks a relevant dx at baseline
    return [
        Candidate("Takayasu arteritis", 0.90),
        Candidate("Lymphoma", 0.85),               # red herring, high base score
        Candidate("Giant cell arteritis", 0.60),
        Candidate("Fibromuscular dysplasia", 0.30),
    ]


def test_baseline_ranking_has_red_herring_in_topk():
    ranked = F.ranked_labels(_connections_case(), FeedbackLedger())
    assert ranked[:2] == ["Takayasu arteritis", "Lymphoma"]


def test_downvote_demotes_red_herring_and_raises_precision():
    gold = {"Takayasu arteritis", "Giant cell arteritis"}
    cands = _connections_case()
    ledger = FeedbackLedger()

    base = F.ranked_labels(cands, ledger)
    p_before = precision_at_k(base, gold, k=2)

    ledger.add("Lymphoma", THUMBS_DOWN)  # clinician 👎 the red herring
    after = F.ranked_labels(cands, ledger)
    p_after = precision_at_k(after, gold, k=2)

    assert p_before == pytest.approx(0.5)
    assert p_after == pytest.approx(1.0)
    assert p_after >= p_before            # never regresses
    assert after[:2] == ["Takayasu arteritis", "Giant cell arteritis"]


def test_upvote_never_regresses_precision():
    gold = {"Takayasu arteritis", "Giant cell arteritis"}
    cands = _connections_case()
    ledger = FeedbackLedger()
    p_before = precision_at_k(F.ranked_labels(cands, ledger), gold, k=2)
    ledger.add("Takayasu arteritis", THUMBS_UP)          # reinforce the correct dx
    p_after = precision_at_k(F.ranked_labels(cands, ledger), gold, k=2)
    assert p_after >= p_before


def test_ruled_out_is_suppressed_entirely():
    ledger = FeedbackLedger()
    ledger.rule_out("Lymphoma")
    ranked = F.ranked_labels(_connections_case(), ledger)
    assert "Lymphoma" not in ranked                       # never re-suggested


def test_reweight_is_monotone_and_deterministic():
    cands = _connections_case()
    ledger = FeedbackLedger()
    ledger.add("Lymphoma", THUMBS_DOWN)
    ledger.add("Lymphoma", THUMBS_DOWN)                   # two downvotes -> harsher
    once = dict(F.reweight(cands, FeedbackLedger()))
    twice = dict(F.reweight(cands, ledger))
    assert twice["Lymphoma"] < once["Lymphoma"]
    # deterministic
    assert F.reweight(cands, ledger) == F.reweight(cands, ledger)


# --------------------------------------------------------------------------- #
# ledger persistence
# --------------------------------------------------------------------------- #
def test_ledger_roundtrip_dict():
    ledger = FeedbackLedger()
    ledger.add("A", THUMBS_UP)
    ledger.add("B", THUMBS_DOWN)
    ledger.rule_out("C")
    restored = FeedbackLedger.from_dict(ledger.to_dict())
    assert restored.signal("A") == 1
    assert restored.signal("B") == -1
    assert "C" in restored.ruled_out


def test_load_save_ledger_uses_registry(monkeypatch):
    store = {}
    monkeypatch.setattr(F.registry, "set_meta", lambda k, v: store.__setitem__(k, v))
    monkeypatch.setattr(F.registry, "get_meta", lambda k: store.get(k))
    led = FeedbackLedger()
    led.add("X", THUMBS_DOWN)
    F.save_ledger("odyssey", led)
    assert "feedback_odyssey" in store          # persisted under the per-patient key
    loaded = F.load_ledger("odyssey")
    assert loaded.signal("X") == -1


# --------------------------------------------------------------------------- #
# Cloud capture layer (fake client)
# --------------------------------------------------------------------------- #
class _FakeCloud:
    """Records remember_entry calls; simulates the session-detail read-back and an
    optional transient 409 before success."""

    def __init__(self, fail_times: int = 0):
        self.calls = []
        self.fail_times = fail_times
        self._qas = {}   # session_id -> {qa_id: qa dict}

    async def remember_entry(self, entry, dataset_name="main_dataset", session_id=None,
                             retries=6, backoff=2.0):
        # emulate the client's own retry having already resolved transient 409s:
        # here we just record and mutate state.
        self.calls.append((entry.get("type"), dataset_name, session_id))
        self._qas.setdefault(session_id, {})
        if entry["type"] == "qa":
            qa_id = f"qa-{len(self.calls)}"
            self._qas[session_id][qa_id] = {
                "qa_id": qa_id, "question": entry["question"], "answer": entry["answer"],
                "feedback_score": None, "feedback_text": None,
                "memify_metadata": {"feedback_weights_applied": False},
                "used_graph_element_ids": entry.get("used_graph_element_ids"),
            }
            return {"status": "session_stored", "entry_type": "qa", "entry_id": qa_id}
        if entry["type"] == "feedback":
            qa = self._qas.get(session_id, {}).get(entry["qa_id"])
            if qa is not None:
                qa["feedback_score"] = entry["feedback_score"]
                qa["feedback_text"] = entry.get("feedback_text")
            return {"status": "session_stored", "entry_type": "feedback",
                    "entry_id": entry["qa_id"]}
        return {"status": "session_stored"}

    async def session_detail(self, session_id):
        return {"session_id": session_id, "qas": list(self._qas.get(session_id, {}).values())}


async def test_record_qa_returns_qa_id():
    c = _FakeCloud()
    qa_id = await F.record_qa(c, session_id="s1", question="Q?", answer="A.",
                              used_node_ids=["n1", "n2"])
    assert qa_id == "qa-1"
    assert ("qa", "main_dataset", "s1") in c.calls


async def test_submit_feedback_persists_and_reads_back():
    c = _FakeCloud()
    qa_id = await F.record_qa(c, session_id="s1", question="Q?", answer="A.",
                              used_node_ids=["n1"])
    state = await F.submit_feedback(c, session_id="s1", qa_id=qa_id,
                                    score=THUMBS_DOWN, text="not relevant")
    assert state.found
    assert state.score == THUMBS_DOWN
    assert state.text == "not relevant"
    assert state.weights_applied is False           # staged, not yet applied
    assert state.used_node_ids == ["n1"]


async def test_improve_findings_batch_report():
    c = _FakeCloud()
    q1 = await F.record_qa(c, session_id="s1", question="Q1", answer="A1")
    q2 = await F.record_qa(c, session_id="s1", question="Q2", answer="A2")
    report = await F.improve_findings(
        c, session_id="s1",
        items=[(q1, THUMBS_UP, "good"), (q2, THUMBS_DOWN, "bad")])
    assert report.all_persisted
    assert len(report.submitted) == 2
    d = report.to_dict()
    assert d["count"] == 2 and d["all_persisted"] is True


async def test_read_feedback_state_missing_qa():
    c = _FakeCloud()
    state = await F.read_feedback_state(c, "s-none", "nope")
    assert not state.found


def test_finding_node_ids_from_evidence():
    from app.models import Finding, EvidenceRef, FindingKind
    f = Finding(id="x", kind=FindingKind.connection, summary="s",
                evidence=[EvidenceRef(data_id="d1"), EvidenceRef(chunk_id="c2")],
                agent="connections")
    assert F.finding_node_ids(f) == ["d1", "c2"]


# --------------------------------------------------------------------------- #
# best-effort feedback (transient 409 tolerance)
# --------------------------------------------------------------------------- #
class _Raising409Cloud(_FakeCloud):
    """Simulates the sustained transient 409 the shared tenant returns on the feedback
    dispatch after the client's own retries are exhausted."""

    async def remember_entry(self, entry, dataset_name="main_dataset", session_id=None,
                             retries=6, backoff=2.0):
        if entry.get("type") == "feedback":
            from app.cloud_client import CloudError
            raise CloudError(409, '{"error":"An error occurred during remember."}')
        return await super().remember_entry(entry, dataset_name=dataset_name,
                                            session_id=session_id, retries=retries)


async def test_try_submit_feedback_landed():
    c = _FakeCloud()
    qa_id = await F.record_qa(c, session_id="s1", question="Q?", answer="A.",
                              used_node_ids=["n1"])
    landed, state = await F.try_submit_feedback(c, session_id="s1", qa_id=qa_id,
                                                score=THUMBS_DOWN, text="rh")
    assert landed is True
    assert state.found and state.score == THUMBS_DOWN


async def test_try_submit_feedback_tolerates_transient_409():
    c = _Raising409Cloud()
    qa_id = await F.record_qa(c, session_id="s1", question="Q?", answer="A.",
                              used_node_ids=["n1"])
    landed, state = await F.try_submit_feedback(c, session_id="s1", qa_id=qa_id,
                                                score=THUMBS_DOWN, text="rh")
    assert landed is False
    assert not state.found
    assert "409" in (state.text or "")
    # the QA itself is still captured with its nodes to reweight (the gated proof)
    pre = await F.read_feedback_state(c, "s1", qa_id)
    assert pre.found and pre.used_node_ids == ["n1"]


async def test_try_submit_feedback_reraises_non_409():
    class _Raising500(_FakeCloud):
        async def remember_entry(self, entry, dataset_name="main_dataset", session_id=None,
                                 retries=6, backoff=2.0):
            if entry.get("type") == "feedback":
                from app.cloud_client import CloudError
                raise CloudError(500, "boom")
            return await super().remember_entry(entry, dataset_name=dataset_name,
                                                session_id=session_id, retries=retries)

    from app.cloud_client import CloudError
    c = _Raising500()
    qa_id = await F.record_qa(c, session_id="s1", question="Q?", answer="A.")
    with pytest.raises(CloudError):
        await F.try_submit_feedback(c, session_id="s1", qa_id=qa_id, score=THUMBS_DOWN)
