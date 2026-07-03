"""Live verification of the P4 time-travel counterfactual.

Builds the candidate index from the live literature brain, reconstructs the phenotype
over the hero's dated encounters, and prints when the connected memory would first have
justified flagging Takayasu arteritis — and how many months before the real diagnosis.

Run:  python -m scripts.verify_timetravel
"""

from __future__ import annotations

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.cognee_client import get_client
from app.seed.odyssey_patient import HERO_PATIENT
from app.timetravel import run_time_travel


async def main() -> int:
    client = get_client()
    await client.connect()
    try:
        res = await run_time_travel(client, "odyssey")
    finally:
        await client.disconnect()

    print(f"literature brain : {res.literature_dataset}")
    print(f"clinical brain   : {res.clinical_dataset}")
    print(f"candidates       : {res.candidates}")
    print(f"true diagnosis   : {res.true_diagnosis} on {res.true_diagnosis_date}\n")

    print("--- phenotype-over-time (top candidate at each dated encounter) ---")
    for s in res.trace:
        marks = []
        if s.top_is_clear:
            marks.append("TARGET #1")
        if s.has_vascular:
            marks.append("vascular")
        flag = "  <== FIRST FLAG" if s.date == res.first_flag_date else ""
        new = (", ".join(s.new_features) or "-")
        print(f"  {s.date}: top={s.top_condition}({s.top_score}) "
              f"[{', '.join(marks) or '—'}]  new: {new}{flag}")

    print("\n--- counterfactual ---")
    print(f"  constitutional lead first appears : {res.constitutional_lead_date}")
    print(f"  first VASCULAR-supported flag      : {res.first_flag_date}")
    print(f"  real diagnosis                     : {res.true_diagnosis_date}")
    print(f"  => flaggable {res.months_earlier} months earlier")

    # contract
    expect_flag = str(HERO_PATIENT["earliest_flaggable_date"])
    expect_months = int(HERO_PATIENT["months_earlier"])
    checks = [
        ("target reachable in candidate universe", res.true_diagnosis in res.candidates),
        ("a vascular-supported flag exists", res.first_flag_date is not None),
        ("flag is before the real diagnosis",
         bool(res.first_flag_date) and res.first_flag_date < res.true_diagnosis_date),
        (f"flag date == hero anchor ({expect_flag})", res.first_flag_date == expect_flag),
        (f"months_earlier == {expect_months}", res.months_earlier == expect_months),
        ("flag step has the target #1 and a vascular sign",
         bool(res.flag_step) and res.flag_step.top_is_clear and res.flag_step.has_vascular),
        ("lead is a meaningful >=12-month head start", res.months_earlier >= 12),
    ]
    print("\n--- contract ---")
    ok = True
    for label, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed

    print("\n" + ("VERIFY TIMETRAVEL PASS" if ok else "VERIFY TIMETRAVEL FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
