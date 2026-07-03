"""Live verification of the ConnectionsAgent on Cognee Cloud.

Runs the agent end-to-end against the seeded hero clinical brain + the global
`reference_literature` brain and checks:
  * a non-trivial phenotype (HPO set) is derived from the patient graph,
  * the deterministic ranking puts Takayasu arteritis first (discriminating it from
    the constitutional-symptom mimics via the large-vessel signs),
  * each surfaced candidate is a cited connection Finding with an evidence path whose
    every hop carries >=1 citation (citation-required),
  * the discrimination narrative comes back cited,
  * no surfaced summary trips the no-diagnosis lint.

Run from backend/:
    python -m scripts.verify_connections
"""

from __future__ import annotations

import asyncio
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from app.agents.connections import ConnectionsAgent
from app.models import find_diagnosis_language
from app.seed.odyssey_patient import HERO_PATIENT
from app.seed.reference_data import REFERENCE_GOLDEN


async def main() -> int:
    agent = ConnectionsAgent(HERO_PATIENT["id"])
    try:
        res = await agent.run(top_k=3)
    finally:
        await agent.aclose()

    print(f"clinical  : {res.clinical_dataset}")
    print(f"literature: {res.literature_dataset}")
    print(f"session   : {res.session_id}")
    print(f"phenotype (HPO {len(res.patient_hpo)}): {res.patient_hpo}")
    print(f"constellation: {', '.join(res.constellation)}\n")

    print("[ranking] (deterministic phenotype overlap)")
    for r in res.ranking:
        print(f"  score={r['score']:>2}  overlap={r['overlap_count']:>2}  "
              f"vasc={r['vascular_features']}  {r['condition']}")

    print("\n[candidates] (cited connection findings)")
    for c in res.candidates:
        cond = c.summary.split(":")[0].replace("Consider ", "").strip()
        hops = len(c.path.hops) if c.path else 0
        print(f"  - {cond:<28} conf={c.confidence.value:<8} "
              f"cites={len(c.evidence)} hops={hops}")
        print(f"      {c.summary[:150]}")

    print("\n[narrative]")
    if res.narrative:
        ans = res.narrative.summary.replace("\n", " ")
        print(f"  confidence: {res.narrative.confidence.value} ({res.narrative.confidence_score})")
        print(f"  citations : {len(res.narrative.evidence)}")
        print(f"  A: {ans[:700]}{'…' if len(ans) > 700 else ''}")
    else:
        print("  (no cited narrative — suppressed)")

    # --- gate --------------------------------------------------------------
    expected_top = str(REFERENCE_GOLDEN["literature_top_condition"])
    top_ok = res.top_condition == expected_top
    phen_ok = len(res.patient_hpo) >= 8
    vascular_ok = bool(res.ranking) and bool(res.ranking[0]["vascular_features"])
    candidates_ok = bool(res.candidates)
    cited_ok = all(bool(c.evidence) for c in res.candidates)
    hops_cited_ok = all(
        (c.path is None) or all(h.evidence for h in c.path.hops) for c in res.candidates)
    top_is_takayasu_finding = any(
        "takayasu" in c.summary.lower() for c in res.candidates)
    narrative_ok = res.narrative is not None and len(res.narrative.evidence) > 0
    lint_ok = all(not find_diagnosis_language(c.summary) for c in res.candidates)

    print("\n[summary]")
    print(f"  phenotype non-trivial (>=8)    : {phen_ok} ({len(res.patient_hpo)})")
    print(f"  top-ranked == Takayasu         : {top_ok} ({res.top_condition})")
    print(f"  top rank has vascular signal   : {vascular_ok}")
    print(f"  candidates surfaced            : {candidates_ok} ({len(res.candidates)})")
    print(f"  Takayasu is a cited candidate  : {top_is_takayasu_finding}")
    print(f"  every candidate cited          : {cited_ok}")
    print(f"  every evidence-hop cited       : {hops_cited_ok}")
    print(f"  narrative cited                : {narrative_ok}")
    print(f"  no-diagnosis lint clean        : {lint_ok}")

    ok = (phen_ok and top_ok and vascular_ok and candidates_ok and cited_ok
          and hops_cited_ok and top_is_takayasu_finding and narrative_ok and lint_ok)
    print(f"\nVERIFY CONNECTIONS {'PASS' if ok else 'REVIEW'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
