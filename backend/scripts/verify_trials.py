"""Live verification of the TrialsAgent on Cognee Cloud.

Runs the agent end-to-end against the seeded hero clinical brain + the global
`reference_trials` brain and checks:
  * all six seeded trials are read from the grounded trial universe,
  * deterministic eligibility reproduces the golden match/no-match set exactly,
  * the paediatric "right disease, wrong age" trap (NCT09000006) is NOT eligible
    and age is the deciding axis,
  * every surfaced TrialMatch is cited (citation-required),
  * the narrative comes back cited,
  * no surfaced summary trips the no-diagnosis lint.

Run from backend/:
    python -m scripts.verify_trials
"""

from __future__ import annotations

import asyncio
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from app.agents.trials import TrialsAgent
from app.models import find_diagnosis_language
from app.seed.odyssey_patient import HERO_PATIENT
from app.seed.reference_data import REFERENCE_GOLDEN

PAEDIATRIC_NCT = "NCT09000006"


async def main() -> int:
    yob = int(HERO_PATIENT["year_of_birth"])
    agent = TrialsAgent(HERO_PATIENT["id"], year_of_birth=yob)
    try:
        res = await agent.run()
    finally:
        await agent.aclose()

    print(f"clinical : {res.clinical_dataset}")
    print(f"trials   : {res.trials_dataset}")
    print(f"session  : {res.session_id}")
    print(f"hero age : {res.hero_age}")
    print(f"hero dx  : {', '.join(res.hero_conditions)}\n")

    print("[matches] (deterministic eligibility + cited rationale)")
    for m in sorted(res.matches, key=lambda x: x.nct_id):
        verdict = "ELIGIBLE" if m.eligible else "NOT eligible"
        print(f"  - {m.nct_id}  {verdict:<12} conf={m.confidence.value:<8} "
              f"cites={len(m.evidence)}")
        print(f"      deciding: {m.deciding_criterion[:110]}")
    if res.suppressed_uncited:
        print(f"\n  suppressed (uncited): {res.suppressed_uncited}")

    print("\n[narrative]")
    if res.narrative:
        ans = res.narrative.summary.replace("\n", " ")
        print(f"  confidence: {res.narrative.confidence.value} ({res.narrative.confidence_score})")
        print(f"  citations : {len(res.narrative.evidence)}")
        print(f"  A: {ans[:700]}{'…' if len(ans) > 700 else ''}")
    else:
        print("  (no cited narrative — suppressed)")

    # --- gate --------------------------------------------------------------
    should_match = {str(x) for x in REFERENCE_GOLDEN["trials_should_match"]}
    should_not = {str(x) for x in REFERENCE_GOLDEN["trials_should_not_match"]}
    all_ncts = should_match | should_not

    by_id = {m.nct_id: m for m in res.matches}
    universe_ok = all_ncts.issubset(set(by_id))

    got_eligible = {nct for nct, m in by_id.items() if m.eligible}
    got_ineligible = {nct for nct, m in by_id.items() if m.eligible is False}
    eligibility_ok = (got_eligible == should_match) and (got_ineligible == should_not)

    paeds = by_id.get(PAEDIATRIC_NCT)
    paeds_ok = bool(paeds) and paeds.eligible is False and (
        "18 years or older" in paeds.deciding_criterion
        or "5 to 17" in paeds.deciding_criterion
        or "age" in paeds.deciding_criterion.lower())

    cited_ok = bool(res.matches) and all(bool(m.evidence) for m in res.matches)
    narrative_ok = res.narrative is not None and len(res.narrative.evidence) > 0
    lint_ok = all(not find_diagnosis_language(m.deciding_criterion) for m in res.matches)
    if res.narrative:
        lint_ok = lint_ok and not find_diagnosis_language(res.narrative.summary)

    print("\n[summary]")
    print(f"  all 6 trials in universe       : {universe_ok}")
    print(f"  eligibility reproduces golden  : {eligibility_ok}")
    print(f"    match     -> {sorted(got_eligible)}")
    print(f"    no-match  -> {sorted(got_ineligible)}")
    print(f"  paediatric trap not eligible   : {paeds_ok}")
    print(f"  every match cited              : {cited_ok}")
    print(f"  narrative cited                : {narrative_ok}")
    print(f"  no-diagnosis lint clean        : {lint_ok}")

    ok = (universe_ok and eligibility_ok and paeds_ok and cited_ok
          and narrative_ok and lint_ok)
    print(f"\nVERIFY TRIALS {'PASS' if ok else 'REVIEW'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
