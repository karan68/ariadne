"""Live verification of the JustifyAgent on Cognee Cloud.

Runs the agent end-to-end against the seeded hero clinical + reference-trials brains and
checks that a prior-authorisation evidence packet for the biologic (tocilizumab) is
assembled entirely from cited memory:
  * the requested drug is grounded (the biologic in the patient's own med set),
  * the indication is the patient's confirmed condition,
  * all four required elements (diagnosis, active disease, prior conventional therapy,
    supporting evidence) are present and each carries >= 1 citation,
  * the supporting-evidence element is sourced from the reference brain,
  * the packet is complete (nothing missing / suppressed),
  * the medical-necessity narrative is cited.

Run from backend/:
    python -m scripts.verify_justify
"""

from __future__ import annotations

import asyncio
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from app.agents.justify import JustifyAgent
from app.seed.odyssey_patient import HERO_PATIENT


async def main() -> int:
    agent = JustifyAgent(HERO_PATIENT["id"])
    try:
        res = await agent.run()
    finally:
        await agent.aclose()

    p = res.packet
    print(f"clinical : {res.clinical_dataset}")
    print(f"reference: {res.reference_dataset}")
    print(f"session  : {res.session_id}")
    print(f"requested: {p.requested_drug}   indication: {p.indication}\n")

    print("[elements]")
    for e in p.elements:
        mark = "OK " if e.satisfied else "MISS"
        print(f"  [{mark}] {e.key:<20} src={e.source:<9} cites={len(e.evidence)}")
        if e.content:
            print(f"        {e.content[:110].replace(chr(10), ' ')}")
    if res.suppressed_uncited:
        print(f"\n  suppressed (uncited): {res.suppressed_uncited}")

    print("\n[narrative]")
    if res.narrative:
        ans = res.narrative.summary.replace("\n", " ")
        print(f"  confidence: {res.narrative.confidence.value} ({res.narrative.confidence_score})")
        print(f"  citations : {len(res.narrative.evidence)}")
        print(f"  {ans[:400]}{'…' if len(ans) > 400 else ''}")
    else:
        print("  (no cited narrative — suppressed)")

    # --- gate --------------------------------------------------------------
    drug_ok = p.requested_drug.lower().startswith("tocilizumab")
    indication_ok = "takayasu" in (p.indication or "").lower()
    keys = [e.key for e in p.elements]
    keys_ok = keys == ["diagnosis", "active_disease", "prior_therapy", "supporting_evidence"]
    all_cited = bool(p.elements) and all(e.satisfied and e.evidence for e in p.elements)
    se = next((e for e in p.elements if e.key == "supporting_evidence"), None)
    se_ok = se is not None and se.source == "reference" and len(se.evidence) > 0
    complete_ok = p.complete and not p.missing_elements
    narrative_ok = res.narrative is not None and len(res.narrative.evidence) > 0

    print("\n[summary]")
    print(f"  requested drug is the biologic (tocilizumab): {drug_ok} ({p.requested_drug})")
    print(f"  indication is the confirmed condition        : {indication_ok} ({p.indication})")
    print(f"  all four required elements, in order         : {keys_ok}")
    print(f"  every element cited                          : {all_cited}")
    print(f"  supporting evidence from reference brain     : {se_ok}")
    print(f"  packet complete (nothing missing)            : {complete_ok}")
    print(f"  medical-necessity narrative cited            : {narrative_ok}")

    ok = (drug_ok and indication_ok and keys_ok and all_cited and se_ok
          and complete_ok and narrative_ok)
    print(f"\nVERIFY JUSTIFY {'PASS' if ok else 'REVIEW'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
