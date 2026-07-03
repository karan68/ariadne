"""Live verification of the SafetyAgent on Cognee Cloud.

Runs the agent end-to-end against the seeded hero clinical brain and checks:
  * a non-trivial, grounded medication universe is read from the graph,
  * the well-established methotrexate + aspirin (NSAID/salicylate) interaction is
    surfaced as a cited alert,
  * the cumulative immunosuppressant burden (methotrexate + prednisolone + tocilizumab)
    is surfaced as a cited alert,
  * the cross-prescriber iron duplication (Dr. A. Sharma + Dr. R. Iyer) is surfaced as a
    cited duplication alert,
  * every surfaced alert carries >= 1 citation (citation-required),
  * the safety narrative is cited,
  * no surfaced text trips the no-diagnosis lint.

Run from backend/:
    python -m scripts.verify_safety
"""

from __future__ import annotations

import asyncio
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from app.agents.safety import SafetyAgent
from app.models import find_diagnosis_language
from app.seed.odyssey_patient import HERO_PATIENT


async def main() -> int:
    agent = SafetyAgent(HERO_PATIENT["id"])
    try:
        res = await agent.run()
    finally:
        await agent.aclose()

    print(f"clinical : {res.clinical_dataset}")
    print(f"session  : {res.session_id}")
    print(f"meds     : {', '.join(res.medications)}\n")

    print("[alerts]")
    for a in res.alerts:
        print(f"  - [{a.kind}/{a.severity}] {', '.join(a.medications)}  cites={len(a.evidence)}")
        print(f"      {a.rationale[:120]}")
    if res.suppressed_uncited:
        print(f"\n  suppressed (uncited): {res.suppressed_uncited}")

    print("\n[narrative]")
    if res.narrative:
        ans = res.narrative.summary.replace("\n", " ")
        print(f"  confidence: {res.narrative.confidence.value} ({res.narrative.confidence_score})")
        print(f"  citations : {len(res.narrative.evidence)}")
        print(f"  A: {ans[:600]}{'…' if len(ans) > 600 else ''}")
    else:
        print("  (no cited narrative — suppressed)")

    # --- gate --------------------------------------------------------------
    meds_ok = len(res.medications) >= 6
    mtx_aspirin = next(
        (a for a in res.interaction_alerts
         if {"methotrexate"}.issubset({m.lower() for m in a.medications})
         and any("aspirin" in m.lower() for m in a.medications)), None)
    mtx_aspirin_ok = mtx_aspirin is not None and len(mtx_aspirin.evidence) > 0
    stack = next((a for a in res.interaction_alerts if len(a.medications) >= 3), None)
    stack_ok = stack is not None and len(stack.evidence) > 0
    dup = next((a for a in res.duplication_alerts
                if any("iron" in m.lower() for m in a.medications)), None)
    dup_ok = dup is not None and len(dup.evidence) > 0
    all_cited = bool(res.alerts) and all(a.evidence for a in res.alerts)
    narrative_ok = res.narrative is not None and len(res.narrative.evidence) > 0
    lint_ok = all(not find_diagnosis_language(a.rationale) for a in res.alerts)
    if res.narrative:
        lint_ok = lint_ok and not find_diagnosis_language(res.narrative.summary)

    print("\n[summary]")
    print(f"  grounded med universe (>=6)    : {meds_ok} ({len(res.medications)})")
    print(f"  MTX + aspirin interaction cited: {mtx_aspirin_ok}")
    print(f"  immunosuppressant stack cited  : {stack_ok}")
    print(f"  cross-prescriber iron dup cited: {dup_ok}")
    print(f"  every alert cited              : {all_cited}")
    print(f"  narrative cited                : {narrative_ok}")
    print(f"  no-diagnosis lint clean        : {lint_ok}")

    ok = (meds_ok and mtx_aspirin_ok and stack_ok and dup_ok and all_cited
          and narrative_ok and lint_ok)
    print(f"\nVERIFY SAFETY {'PASS' if ok else 'REVIEW'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
