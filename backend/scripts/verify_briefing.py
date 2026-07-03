"""Live verification of the BriefingAgent on Cognee Cloud.

Runs the agent end-to-end against the seeded hero clinical brain and checks that the
one-page pre-visit brief is *entirely derived from cited memory*:
  * a cited summary (active problems / meds / status),
  * deterministic timeline highlights that include the confirmed-diagnosis milestone,
  * a non-trivial, cited list of open questions / pending follow-ups,
  * every backing Finding carries >= 1 citation (citation-required).

Run from backend/:
    python -m scripts.verify_briefing
"""

from __future__ import annotations

import asyncio
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from app.agents.briefing import BriefingAgent
from app.models import find_diagnosis_language
from app.seed.odyssey_patient import GOLDEN, HERO_PATIENT

DX_DATE = str(GOLDEN.get("true_diagnosis_date", "2024-03-01"))


async def main() -> int:
    agent = BriefingAgent(HERO_PATIENT["id"])
    try:
        res = await agent.run()
    finally:
        await agent.aclose()

    brief = res.brief
    print(f"clinical : {res.clinical_dataset}")
    print(f"session  : {res.session_id}")
    print(f"events   : {res.event_count}")
    if res.suppressed:
        print(f"suppressed: {res.suppressed}")

    print("\n[summary]")
    print(f"  {brief.summary[:700].strip()}{'…' if len(brief.summary) > 700 else ''}")

    print("\n[timeline highlights]")
    for e in brief.timeline_highlights:
        print(f"  {e.date}  {e.type:<12} {e.description[:80]}")

    print("\n[open questions]")
    for q in brief.open_questions:
        print(f"  - {q[:110]}")

    print("\n[findings]")
    for f in brief.findings:
        print(f"  {f.kind.value:<10} conf={f.confidence.value:<8} cites={len(f.evidence)}")

    # --- gate --------------------------------------------------------------
    summary_ok = bool(brief.summary.strip()) and res.summary_finding is not None \
        and len(res.summary_finding.evidence) > 0
    dx_highlight_ok = any(
        e.date == DX_DATE and "takayasu" in e.description.lower()
        for e in brief.timeline_highlights)
    highlights_ok = len(brief.timeline_highlights) >= 3
    open_q_ok = len(brief.open_questions) >= 3 and res.open_questions_finding is not None \
        and len(res.open_questions_finding.evidence) > 0
    all_cited = bool(brief.findings) and all(f.evidence for f in brief.findings)
    derived_from_cited = res.suppressed == []  # nothing surfaced uncited

    print("\n[summary check]")
    print(f"  summary present + cited        : {summary_ok}")
    print(f"  >=3 timeline highlights        : {highlights_ok} ({len(brief.timeline_highlights)})")
    print(f"  confirmed-dx milestone present : {dx_highlight_ok} (@{DX_DATE})")
    print(f"  >=3 cited open questions       : {open_q_ok} ({len(brief.open_questions)})")
    print(f"  every finding cited            : {all_cited}")
    print(f"  brief derived only from cited  : {derived_from_cited}")

    ok = (summary_ok and highlights_ok and dx_highlight_ok and open_q_ok
          and all_cited and derived_from_cited)
    print(f"\nVERIFY BRIEFING {'PASS' if ok else 'REVIEW'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
