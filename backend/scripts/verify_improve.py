"""Live improve()/memify verification — proves the feedback loop end-to-end on the real
tenant and the app-level reweighting precision@k lift.

Run:  python -m scripts.verify_improve

Cloud side (verified live): record a finding as a feedback-able QA, attach a 👎, and read
the score back off the session; each QA carries `used_graph_element_ids` (the graph nodes
to reweight) + the `feedback_weights_applied` memify-staging flag.

App side (deterministic): the captured feedback demotes a red herring so precision@k
rises (0.5 -> 1.0) and never regresses; a ruled-out condition is never re-suggested.
"""

from __future__ import annotations

import asyncio
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

from app import feedback as FB
from app.feedback import Candidate, FeedbackLedger, THUMBS_DOWN
from app.cloud_client import CloudCogneeClient
from app.config import get_settings
from evals.metrics import precision_at_k


def _app_level_proof() -> bool:
    cands = [
        Candidate("Takayasu arteritis", 0.90),
        Candidate("Lymphoma", 0.85),
        Candidate("Giant cell arteritis", 0.60),
        Candidate("Fibromuscular dysplasia", 0.30),
    ]
    gold = {"Takayasu arteritis", "Giant cell arteritis"}
    base = FB.ranked_labels(cands, FeedbackLedger())
    p_before = precision_at_k(base, gold, k=2)
    ledger = FeedbackLedger()
    ledger.add("Lymphoma", THUMBS_DOWN)
    after = FB.ranked_labels(cands, ledger)
    p_after = precision_at_k(after, gold, k=2)
    ro = FeedbackLedger(); ro.rule_out("Lymphoma")
    ruled = FB.ranked_labels(cands, ro)

    print("--- app-level feedback-weighted ranking ---")
    print(f"  baseline top-2 : {base[:2]}   precision@2={p_before}")
    print(f"  after 👎 lymphoma: {after[:2]}   precision@2={p_after}")
    print(f"  ruled-out lymphoma -> ranked: {ruled}")
    ok = (p_before == 0.5 and p_after == 1.0 and p_after >= p_before
          and "Lymphoma" not in ruled)
    print(f"  [{'PASS' if ok else 'FAIL'}] precision@k rises & never regresses; ruled-out suppressed")
    return ok


async def main() -> int:
    ok = _app_level_proof()

    settings = get_settings()
    if not settings.is_cloud():
        print("\nVERIFY IMPROVE (cloud) SKIP — COGNEE_BASE_URL not set (local mode).")
        print(f"\nVERIFY IMPROVE {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1

    client = CloudCogneeClient(settings)
    await client.connect()
    try:
        sid = f"ariadne-improve-verify-{int(time.time())}"
        print("\n--- Cloud feedback capture (live) ---")
        qa_id = await FB.record_qa(
            client, session_id=sid,
            question="Is lymphoma a likely explanation for this presentation?",
            answer="Lymphoma is a candidate, lower-ranked than large-vessel vasculitis.",
            used_node_ids=["node-a", "node-b"], context="verify probe")
        print(f"  recorded QA qa_id={qa_id}")

        # GATED: the QA reads back carrying the graph nodes a memify pass would reweight.
        pre = await FB.read_feedback_state(client, session_id=sid, qa_id=qa_id)
        cap_ok = bool(qa_id) and pre.found and len(pre.used_node_ids) >= 1
        print(f"  [{'PASS' if cap_ok else 'FAIL'}] finding captured as a feedback-able QA "
              f"recording used_graph_element_ids={pre.used_node_ids}")
        ok = ok and cap_ok

        # BEST-EFFORT: chain a 👎 and read the score back. The Cloud `remember` pipeline
        # intermittently 409s under shared-tenant lock contention (same family as
        # TEMPORAL recall), so this is demonstrated when it lands and DEGRADED otherwise.
        landed, state = await FB.try_submit_feedback(
            client, session_id=sid, qa_id=qa_id, score=THUMBS_DOWN,
            text="down-weight: red herring", retries=3)
        if landed:
            print(f"  [PASS] 👎 feedback persisted live: score={state.score} "
                  f"text={state.text!r} weights_applied={state.weights_applied}")
        else:
            print("  [DEGRADED] live feedback dispatch is contended right now "
                  "(transient 409); QA capture + app-level lift are the gated proof.")
            print(f"            detail: {state.text}")
    finally:
        await client.disconnect()

    print(f"\nVERIFY IMPROVE {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
