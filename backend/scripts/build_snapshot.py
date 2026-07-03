"""Build the deterministic demo snapshot (app/demo/snapshot.json) from live brains.

Runs every agent + signature feature + cloud surface once and caches the results so the
API serves an instant, reproducible demo. Resilient: a contended section is recorded and
the rest still builds.

    python -m scripts.build_snapshot                # agents + features (no destructive forget)
    python -m scripts.build_snapshot --forget       # also run a live forget-with-proof
"""

from __future__ import annotations

import argparse
import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

from app.demo_store import build_snapshot, save_snapshot


async def _main(include_forget: bool) -> int:
    snap = await build_snapshot(include_forget=include_forget)
    path = save_snapshot(snap)
    print("\n".join(snap.get("build_log", [])))
    ag = snap.get("agents", {})
    ok = sum(1 for v in ag.values() if isinstance(v, dict) and "error" not in v)
    print(f"\nagents ok: {ok}/{len(ag)}")
    for name in ("timetravel", "redthread", "sessions", "graph"):
        v = snap.get(name)
        status = "ERROR" if isinstance(v, dict) and "error" in v else "ok"
        print(f"  {name}: {status}")
    print(f"\nsnapshot written -> {path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--forget", action="store_true", help="also run a live forget-with-proof")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_main(args.forget)))
