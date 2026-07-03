"""Live Sessions verification — prints the per-agent attribution scorecard and one
session's Q&A audit trail, proving Cognee Cloud's observability plane records every
agent recall under its `{agent}-{patient}-{unix}-run-{key}` session id.

Run:  python -m scripts.verify_sessions
"""

from __future__ import annotations

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

from app import sessions as SESS
from app.cloud_client import CloudCogneeClient
from app.config import get_settings


async def main() -> int:
    settings = get_settings()
    if not settings.is_cloud():
        print("VERIFY SESSIONS SKIP — COGNEE_BASE_URL not set (local mode).")
        return 0

    client = CloudCogneeClient(settings)
    await client.connect()
    ok = True
    try:
        report = await SESS.observe(client, range="all", limit=200)
        print(f"range          : {report.range}")
        print(f"total sessions : {report.total_sessions}")
        print(f"tokens total   : {report.tokens_total}")
        print(f"agent time (s) : {report.stats.get('agent_time_s')}")

        print("\n--- per-agent attribution ---")
        for agent in SESS.AGENT_NAMES:
            att = report.by_agent.get(agent)
            if att:
                print(f"  {agent:12} sessions={att.session_count:3}  runs={att.run_count:2}  "
                      f"patients={sorted(att.patients)}  last={att.last_activity}")
            else:
                print(f"  {agent:12} (no sessions attributed)")

        print("\n--- cost by model ---")
        for m in report.cost_by_model:
            print(f"  {m.get('model')}: sessions={m.get('session_count')} "
                  f"tokens_in={m.get('tokens_in')} tokens_out={m.get('tokens_out')} "
                  f"cost_usd={m.get('cost_usd')}")

        # one session's audit trail
        raw = await client.sessions(range="all", limit=1)
        first = (raw.get("sessions") if isinstance(raw, dict) else raw) or []
        if first:
            sid = first[0].get("session_id")
            audit = await SESS.audit_trail(client, sid)
            print(f"\n--- audit trail: {sid} (agent={audit.agent}) ---")
            print(f"  label: {audit.label}")
            for t in audit.turns[:1]:
                print(f"  Q: {t.question[:160]}")
                print(f"  A: {t.answer[:160]}")

        checks = {
            "sessions attributed to agents": report.total_sessions > 0 and len(report.by_agent) > 0,
            "all six swarm agents attributed": report.all_agents_attributed,
            "aggregate token accounting populated": report.tokens_total > 0,
            "cost-by-model present": len(report.cost_by_model) > 0,
            "a session exposes a Q&A audit trail":
                bool(first) and (await SESS.audit_trail(client, first[0]["session_id"])).turn_count >= 1,
        }
        print("\n--- contract ---")
        for name, passed in checks.items():
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
            ok = ok and passed
    finally:
        await client.disconnect()

    print(f"\nVERIFY SESSIONS {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
