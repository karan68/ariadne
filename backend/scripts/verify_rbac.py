"""Live RBAC verification — provisions the Cognee Cloud RBAC substrate for the hero
patient and prints a human-readable proof that the family/provider split is real.

Run:  python -m scripts.verify_rbac

Idempotent: reuses existing roles by name (roles cannot be deleted via the API).
"""

from __future__ import annotations

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

from app import principals as P
from app.principals import AppRole, BrainKind
from app.cloud_client import CloudCogneeClient
from app.config import get_settings

_PATIENT = "odyssey"


async def main() -> int:
    settings = get_settings()
    if not settings.is_cloud():
        print("VERIFY RBAC SKIP — COGNEE_BASE_URL not set (local mode).")
        return 0

    client = CloudCogneeClient(settings)
    await client.connect()
    ok = True
    try:
        tenant = await client.tenant_me()
        print(f"tenant        : {tenant}")

        report = await P.provision(client, patient_id=_PATIENT)
        print(f"user_id       : {report.user_id}")

        print("\n--- roles + dataset grants (Cloud RBAC substrate) ---")
        for rg in report.roles:
            print(f"  [{rg.role:8}] {rg.role_name}  id={rg.role_id}")
            for g in rg.grants:
                mark = "OK " if g["ok"] else "ERR"
                print(f"      {mark} {g['permission']:6} {g['brain']:11} -> {g['dataset_id']}")

        print("\n--- agent principals (Sessions attribution) ---")
        for a in report.agents:
            print(f"  {a.name:12} id={a.principal_id}  brains={a.brains}")

        # contract assertions
        fam = next(r for r in report.roles if r.role == AppRole.FAMILY)
        prov = next(r for r in report.roles if r.role == AppRole.PROVIDER)
        fam_brains = {g["brain"] for g in fam.grants}
        prov_brains = {g["brain"] for g in prov.grants}

        checks = {
            "family role NOT granted clinical": BrainKind.CLINICAL not in fam_brains,
            "family role granted reference brains":
                {BrainKind.LITERATURE, BrainKind.TRIALS}.issubset(fam_brains),
            "provider role granted clinical + reference":
                {BrainKind.CLINICAL, BrainKind.LITERATURE, BrainKind.TRIALS} == prov_brains,
            "all grants accepted (200)": report.all_grants_ok,
            "all six agents registered": report.all_agents_registered,
            "enforcement: family->clinical->[]":
                P.guarded_datasets(AppRole.FAMILY, _PATIENT, BrainKind.CLINICAL) == [],
            "enforcement: provider->clinical->dataset":
                len(P.guarded_datasets(AppRole.PROVIDER, _PATIENT, BrainKind.CLINICAL)) == 1,
        }

        print("\n--- contract ---")
        for name, passed in checks.items():
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
            ok = ok and passed
    finally:
        await client.disconnect()

    print(f"\nVERIFY RBAC {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
