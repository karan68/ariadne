"""Live forget()-with-proof verification.

Run:  python -m scripts.verify_forget

Seeds a **disposable, versioned** dataset (never the hero brain) with a genuine record
(aspirin) + a deliberately mislabeled record (Type 1 diabetes the patient never had),
then forgets the mislabeled data_id and proves it is *surgical*:

  * the graph node/edge count drops,
  * a recall of the mislabeled fact flips from "Yes" to "No",
  * the unrelated fact is still recallable.

The dataset is deleted afterward.
"""

from __future__ import annotations

import asyncio
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

from app import forget as FGT
from app.cloud_client import CloudCogneeClient
from app.config import get_settings
from app.ontology import CUSTOM_EXTRACTION_PROMPT, clinical_graph_model_json


async def main() -> int:
    settings = get_settings()
    if not settings.is_cloud():
        print("VERIFY FORGET SKIP — COGNEE_BASE_URL not set (local mode).")
        return 0

    client = CloudCogneeClient(settings)
    await client.connect()
    dataset = f"ariadne_forget_verify__{time.strftime('%Y%m%d%H%M%S')}"
    dataset_id = ""
    try:
        print(f"--- seeding disposable dataset {dataset} ---")
        dataset_id, keep_id, bad_id = await FGT.seed_forget_fixture(
            client, dataset=dataset, graph_model=clinical_graph_model_json(),
            custom_prompt=CUSTOM_EXTRACTION_PROMPT)
        print(f"  dataset_id={dataset_id}")
        print(f"  keep data_id={keep_id}")
        print(f"  mislabeled data_id={bad_id}")
        if not (dataset_id and bad_id):
            print("\nVERIFY FORGET FAIL — could not seed the fixture")
            return 1

        print("\n--- forget-with-proof ---")
        proof = FGT.prove_forget  # for readability
        p = await proof(client, dataset=dataset, dataset_id=dataset_id, data_id=bad_id)

        print(f"  graph nodes {p.nodes_before} -> {p.nodes_after} (removed {p.nodes_removed})")
        print(f"  graph edges {p.edges_before} -> {p.edges_after}")
        print(f"  forget status: {p.forget_status}")
        print(f"  recall '{p.probe_query}'")
        print(f"    before: {p.probe_before[:100]!r}  (verdict={FGT.verdict(p.probe_before)})")
        print(f"    after : {p.probe_after[:100]!r}  (verdict={FGT.verdict(p.probe_after)})")
        print(f"  recall '{p.unrelated_query}'")
        print(f"    after : {p.unrelated_after[:100]!r}  (survives={p.unrelated_survives})")

        checks = {
            "mislabeled fact recallable before": p.probe_present_before,
            "deletion reported success": p.deletion_succeeded,
            "graph shrank (nodes removed)": p.graph_shrank,
            "mislabeled fact no longer recallable (Yes->No)": p.probe_absent_after,
            "unrelated memory survives": p.unrelated_survives,
            "delete is surgical": p.is_surgical,
        }
        print("\n--- contract ---")
        ok = True
        for name, passed in checks.items():
            print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
            ok = ok and passed
    finally:
        if dataset_id:
            try:
                print(f"\ncleanup: delete dataset {dataset_id}")
                await client.delete_dataset(dataset_id)
            except Exception as exc:
                print(f"  (warning) cleanup delete failed (non-fatal): {exc!r}")
        await client.disconnect()

    print(f"\nVERIFY FORGET {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
