"""Live Cognee Cloud smoke test (P1 pivot).

Exercises the real hosted tenant end-to-end with the CloudCogneeClient:
  connect -> health -> remember (with clinical graph_model) -> recall (GRAPH +
  TEMPORAL, with references) -> sessions/stats -> forget -> disconnect.

It PRINTS the actual response shapes for remember/recall so we can wire the P2
agents against reality. Run from backend/:

    python -m scripts.smoke_cloud
"""

from __future__ import annotations

import asyncio
import json
import time

from app.cognee_client import QueryType, get_client
from app.config import get_settings
from app.ontology import CUSTOM_EXTRACTION_PROMPT, clinical_graph_model_json

SMOKE_NOTE = (
    "Clinic note 2022-03-14. 26-year-old woman. Six months of low-grade fevers, "
    "malaise, and left arm claudication. Exam: unequal blood pressure between arms "
    "(right 128/82, left 96/60), a bruit over the left subclavian artery. "
    "Labs: ESR 74 mm/hr (high), CRP 41 mg/L (high). Prior clinicians attributed "
    "symptoms to anxiety. Assessment: large-vessel inflammation not yet explained."
)


def _dump(label: str, obj, limit: int = 4000) -> None:
    print(f"\n----- {label} -----")
    try:
        s = json.dumps(obj, indent=2, default=str)
    except Exception:
        s = str(obj)
    print(s[:limit] + ("\n... [truncated]" if len(s) > limit else ""))


def _find_dataset_id(datasets, name):
    items = datasets.get("datasets") if isinstance(datasets, dict) else datasets
    for d in items or []:
        if isinstance(d, dict) and d.get("name") == name:
            return d.get("id")
    return None


async def _ingest_and_recall(client, dataset, note, gm, tag):
    """Remember a note (permanent mode) then GRAPH+TEMPORAL recall. Returns (ok, ds_id)."""
    print(f"\n=== [{tag}] remembering into '{dataset}' "
          f"(graph_model={'yes' if gm else 'no'}) ===")
    t0 = time.time()
    try:
        remember_resp = await client.remember(
            data=note,
            dataset_name=dataset,
            node_set=["encounter:2022-03-14", "specialty:rheumatology"],
            graph_model=gm,
            custom_prompt=CUSTOM_EXTRACTION_PROMPT if gm else None,
            run_in_background=False,
        )
    except Exception as e:
        print(f"[{tag}] REMEMBER FAILED after {time.time()-t0:.1f}s: {e!r}")
        return False, None
    print(f"[{tag}] remember took {time.time()-t0:.1f}s")
    _dump(f"[{tag}] remember response", remember_resp)

    ds_id = _find_dataset_id(await client.list_datasets(), dataset)
    print(f"[{tag}] resolved dataset id: {ds_id}")

    session_id = f"smoke-{tag}-{int(time.time())}"
    try:
        t1 = time.time()
        graph_resp = await client.recall(
            query_text="What symptoms, exam findings, and labs are documented for this patient?",
            query_type=QueryType.GRAPH_COMPLETION, datasets=[dataset],
            session_id=session_id, include_references=True, top_k=10,
        )
        print(f"[{tag}] graph recall took {time.time()-t1:.1f}s")
        _dump(f"[{tag}] recall GRAPH_COMPLETION (with references)", graph_resp)
    except Exception as e:
        print(f"[{tag}] GRAPH recall FAILED: {e!r}")

    try:
        t2 = time.time()
        temporal_resp = await client.recall(
            query_text="Reconstruct the timeline of this patient's events in order.",
            query_type=QueryType.TEMPORAL, datasets=[dataset],
            session_id=session_id, include_references=True, top_k=10,
        )
        print(f"[{tag}] temporal recall took {time.time()-t2:.1f}s")
        _dump(f"[{tag}] recall TEMPORAL", temporal_resp)
    except Exception as e:
        print(f"[{tag}] TEMPORAL recall FAILED: {e!r}")

    return True, ds_id


async def main() -> int:
    settings = get_settings()
    print(f"mode={settings.mode} base_url_set={bool(settings.cognee_base_url)} "
          f"tenant_set={bool(settings.cognee_tenant_id)}")
    if not settings.is_cloud():
        print("NOT in cloud mode (COGNEE_BASE_URL missing) - aborting")
        return 2

    run = int(time.time())
    ds_with = f"ariadne_smoke_gm_{run}"      # with custom clinical graph_model
    ds_plain = f"ariadne_smoke_plain_{run}"  # control: default extraction
    created_ids = []

    client = get_client()  # -> CloudCogneeClient
    print("client:", type(client).__name__)
    await client.connect()
    try:
        _dump("health", await client.health())
        _dump("datasets (before)", await client.list_datasets(), limit=1500)

        gm = clinical_graph_model_json()
        ok_a, id_a = await _ingest_and_recall(client, ds_with, SMOKE_NOTE, gm, "graph_model")
        if id_a:
            created_ids.append(id_a)
        ok_b, id_b = await _ingest_and_recall(client, ds_plain, SMOKE_NOTE, None, "control")
        if id_b:
            created_ids.append(id_b)

        try:
            _dump("sessions", await client.sessions(range="24h", limit=5), limit=2500)
            _dump("session_stats", await client.session_stats(range="24h"), limit=1500)
        except Exception as e:
            print("sessions/stats fetch failed (non-fatal):", repr(e))

        print(f"\nRESULT: graph_model_ok={ok_a} control_ok={ok_b}")

    finally:
        for dsid in created_ids:
            try:
                print(f"deleting dataset {dsid} ...")
                await client.delete_dataset(dsid)
            except Exception as e:
                print(f"delete {dsid} failed:", repr(e))
        await client.disconnect()

    print("\nSMOKE CLOUD: done")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
