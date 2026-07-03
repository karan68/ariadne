"""Live verification of the TimelineAgent against the seeded hero brain.

Runs the agent end-to-end on Cognee Cloud and checks:
  * a non-trivial, correctly date-ordered structured timeline is produced,
  * it spans the multi-year odyssey and includes the confirmed-diagnosis event,
  * the narrative arc comes back cited (citation-required Finding),
  * a `since` slice ("what changed lately?") narrows the axis.

Run from backend/:
    python -m scripts.verify_timeline
"""

from __future__ import annotations

import asyncio
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from app.agents.timeline import TimelineAgent
from app.seed.odyssey_patient import GOLDEN, HERO_PATIENT


async def main() -> int:
    patient_id = HERO_PATIENT["id"]
    agent = TimelineAgent(patient_id)
    try:
        res = await agent.run()
    finally:
        await agent.aclose()

    print(f"dataset: {res.dataset_name}")
    print(f"session: {res.session_id}")
    print(f"narrative via: {res.used_search_type}")
    print(f"events: {len(res.events)}  span: {res.span}\n")

    for e in res.events:
        print(f"  {e.date}  {e.type:<12} {e.description[:90]}")

    ordered = [e.date for e in res.events] == sorted(e.date for e in res.events)
    dates = [e.date for e in res.events]
    dx_date = str(GOLDEN.get("true_diagnosis_date", "2024-03-01"))
    has_dx_event = any(
        e.date == dx_date and "takayasu" in e.description.lower() for e in res.events)
    multi_year = bool(dates) and dates[0][:4] != dates[-1][:4]

    print("\n[narrative]")
    if res.narrative:
        ans = res.narrative.summary.replace("\n", " ")
        print(f"  confidence: {res.narrative.confidence} ({res.narrative.confidence_score})")
        print(f"  citations : {len(res.narrative.evidence)}")
        print(f"  A: {ans[:600]}{'…' if len(ans) > 600 else ''}")
    else:
        print("  (no cited narrative — suppressed)")

    # 'What changed since last year?' slice
    since = "2024-01-01"
    try:
        recent = await agent.run_and_close(since=since)
        print(f"\n[since {since}] events: {len(recent.events)} "
              f"(all >= {since}: {all(e.date >= since for e in recent.events)})")
    except Exception as e:  # noqa: BLE001
        recent = None
        print(f"\n[since] slice failed: {e!r}")

    narrative_ok = res.narrative is not None and len(res.narrative.evidence) > 0
    since_ok = recent is not None and bool(recent.events) and all(
        e.date >= since for e in recent.events)

    print("\n[summary]")
    print(f"  events present            : {len(res.events) >= 10}")
    print(f"  date-ordered              : {ordered}")
    print(f"  spans multiple years      : {multi_year}")
    print(f"  confirmed-dx event present: {has_dx_event}")
    print(f"  narrative cited           : {narrative_ok}")
    print(f"  since-slice works         : {since_ok}")

    ok = (len(res.events) >= 10 and ordered and multi_year and has_dx_event
          and narrative_ok and since_ok)
    print(f"\nVERIFY TIMELINE {'PASS' if ok else 'REVIEW'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
