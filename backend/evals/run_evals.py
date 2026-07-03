"""Ariadne eval runner.

    python -m evals.run_evals            # run current-phase gate (P1), live
    python -m evals.run_evals --offline  # deterministic only (no cloud), CI-safe
    python -m evals.run_evals --phase p1

Prints a report table, writes evals/reports/<phase>_<ts>.json, and exits non-zero
if any gating check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from evals.p1_eval import EvalResult, run_p1
from evals.p2_eval import run_p2
from evals.p3_eval import run_p3
from evals.p4_eval import run_p4
from evals.swarm_eval import run_swarm

_REPORTS = Path(__file__).resolve().parent / "reports"

_RUNNERS = {"p1": run_p1, "p2": run_p2, "p3": run_p3, "p4": run_p4, "swarm": run_swarm}


def _print_report(res: EvalResult) -> None:
    print(f"\n=== Ariadne eval: {res.phase.upper()} ({'live' if res.live else 'offline'}) ===\n")
    width = max((len(c.name) for c in res.checks), default=10)
    for c in res.checks:
        mark = "PASS" if c.passed else ("FAIL" if c.gating else "warn")
        line = f"  [{mark:4}] {c.name.ljust(width)}"
        if c.detail and (not c.passed or mark == "warn"):
            line += f"   :: {c.detail}"
        print(line)

    print("\n  metrics:")
    for k, v in res.metrics.items():
        if k in ("node_type_counts",) or (isinstance(v, list) and len(v) > 8):
            v = json.dumps(v)[:120] + ("…" if len(json.dumps(v)) > 120 else "")
        print(f"    {k:<28} {v}")

    fails = res.gating_failures
    gating_total = len([c for c in res.checks if c.gating])
    print(f"\n  gating checks: {gating_total - len(fails)}/{gating_total} passed")
    print(f"\n{res.phase.upper()} EVAL {'PASS' if res.passed else 'FAIL'}")


def _write_report(res: EvalResult) -> Path:
    _REPORTS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    path = _REPORTS / f"{res.phase}_{'offline' if not res.live else 'live'}_{ts}.json"
    path.write_text(json.dumps(res.to_dict(), indent=2), encoding="utf-8")
    return path


async def _main() -> int:
    ap = argparse.ArgumentParser(description="Run Ariadne phase eval gates")
    ap.add_argument("--phase", default="p1", choices=list(_RUNNERS))
    ap.add_argument("--offline", action="store_true", help="skip live cloud checks (CI-safe)")
    args = ap.parse_args()

    res = await _RUNNERS[args.phase](offline=args.offline)
    _print_report(res)
    report_path = _write_report(res)
    print(f"report: {report_path}")
    return 0 if res.passed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
