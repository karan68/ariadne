"""Ingest the hero Takayasu "odyssey" patient into a per-patient clinical brain on
Cognee Cloud.

Each encounter is remembered as its own document (permanent mode: add()+cognify)
into a freshly-named clinical dataset, tagged with a per-encounter `node_set`
(patient / date / specialty / doc-type) so later recalls can be scoped and the
time-travel beat can filter by encounter date.

Why a *versioned* dataset name: on this Cloud tenant, deleting a dataset is async
and reusing the same (deterministic) name/id immediately after wedges it
(DATASET_PROCESSING_ERRORED -> subsequent writes raise ProgrammingError). Net-new
unique names always work. So each full seed creates `patient_<id>_clinical__<ts>`
and records it in the registry as the patient's active clinical brain; the previous
(healthy) dataset is best-effort deleted afterward.

Run from backend/:
    python -m app.seed.ingest            # full seed -> sets the active clinical brain
    python -m app.seed.ingest --limit 2  # quick throwaway test (auto-deleted)
    python -m app.seed.ingest --limit 2 --keep   # keep the throwaway dataset
"""

from __future__ import annotations

import argparse
import asyncio
import re
import time
from datetime import datetime
from typing import List, Optional

from app import registry
from app.cognee_client import get_client
from app.config import get_settings
from app.ontology import CUSTOM_EXTRACTION_PROMPT, clinical_graph_model_json
from app.seed.odyssey_patient import HERO_PATIENT, iter_documents


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _node_set(meta: dict) -> List[str]:
    tags = [f"patient:{HERO_PATIENT['id']}"]
    if meta.get("date"):
        tags.append(f"encounter:{meta['date']}")
    if meta.get("specialty"):
        tags.append(f"specialty:{_slug(meta['specialty'])}")
    if meta.get("doc_type"):
        tags.append(f"doc:{_slug(meta['doc_type'])}")
    return tags


async def _find_dataset_id(client, name: str) -> Optional[str]:
    datasets = await client.list_datasets()
    items = datasets.get("datasets") if isinstance(datasets, dict) else datasets
    for d in items or []:
        if isinstance(d, dict) and d.get("name") == name:
            return d.get("id")
    return None


async def _best_effort_delete(client, dataset_id: Optional[str], why: str) -> None:
    if not dataset_id:
        return
    try:
        print(f"{why}: deleting dataset {dataset_id}")
        await client.delete_dataset(dataset_id)
    except Exception as e:
        print(f"  (warning) delete failed (non-fatal): {e!r}")


async def ingest_hero(limit: Optional[int] = None, set_active: Optional[bool] = None,
                      keep: bool = False, background: bool = False) -> dict:
    settings = get_settings()
    if not settings.is_cloud():
        raise RuntimeError("ingest_hero requires cloud mode (set COGNEE_BASE_URL)")

    patient_id = HERO_PATIENT["id"]
    # A full run (no limit) is a real seed that updates the active brain pointer;
    # a limited run is a throwaway test unless explicitly asked to set_active.
    is_full = limit is None
    if set_active is None:
        set_active = is_full

    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    base = settings.dataset_clinical(patient_id)          # patient_odyssey_clinical
    dataset = f"{base}__{ts}"                             # versioned, always net-new
    prev = registry.get_active(patient_id, "clinical") if set_active else None

    client = get_client()
    await client.connect()
    graph_model = clinical_graph_model_json()
    data_ids: List[str] = []
    dataset_id: Optional[str] = None
    try:
        docs = list(iter_documents())
        if limit is not None:
            docs = docs[:limit]
        print(f"ingesting {len(docs)} encounter(s) into '{dataset}' "
              f"(graph_model title={graph_model.get('title')}, background={background})")

        for i, (doc_id, text, meta) in enumerate(docs, start=1):
            tags = _node_set(meta)
            t0 = time.time()
            resp = await client.remember(
                data=text,
                dataset_name=dataset,
                node_set=tags,
                graph_model=graph_model,
                custom_prompt=CUSTOM_EXTRACTION_PROMPT,
                run_in_background=background,
            )
            dt = time.time() - t0
            status = resp.get("status") if isinstance(resp, dict) else None
            if isinstance(resp, dict):
                dataset_id = resp.get("dataset_id") or dataset_id
                ids = [it.get("id") for it in (resp.get("items") or []) if isinstance(it, dict)]
                data_ids.extend([x for x in ids if x])
            print(f"  [{i}/{len(docs)}] {doc_id:<22} {status or '?':<11} {dt:5.1f}s tags={tags}")
            if status not in ("completed", "running", "session_stored", None):
                raise RuntimeError(f"unexpected remember status for {doc_id}: {status!r} :: {resp}")

    except Exception:
        # New dataset may now be half-written; delete it so it doesn't linger errored.
        bad = dataset_id or await _find_dataset_id(client, dataset)
        await _best_effort_delete(client, bad, "failure cleanup")
        await client.disconnect()
        raise

    if dataset_id is None:
        dataset_id = await _find_dataset_id(client, dataset)

    unique_ids = list(dict.fromkeys(data_ids))  # remember items are cumulative -> dedupe

    if set_active:
        registry.set_active(patient_id, "clinical", dataset, dataset_id)
        print(f"registry: active clinical brain for '{patient_id}' -> {dataset} ({dataset_id})")
        if prev and prev.get("id") and prev.get("id") != dataset_id:
            await _best_effort_delete(client, prev.get("id"), "superseded previous brain")
    elif not keep:
        # Throwaway test dataset: clean it up (healthy datasets delete fine).
        await _best_effort_delete(client, dataset_id, "throwaway test cleanup")

    await client.disconnect()

    summary = {
        "dataset": dataset,
        "dataset_id": dataset_id,
        "documents": len(unique_ids),
        "data_ids": unique_ids,
        "active": bool(set_active),
    }
    print(f"\nINGEST DONE: dataset={dataset} id={dataset_id} documents={len(unique_ids)} "
          f"active={bool(set_active)}")
    return summary


def _parse_args():
    p = argparse.ArgumentParser(description="Ingest the hero Takayasu patient into Cognee Cloud")
    p.add_argument("--limit", type=int, default=None, help="only ingest the first N encounters (throwaway test)")
    p.add_argument("--set-active", action="store_true", help="force updating the active brain pointer")
    p.add_argument("--keep", action="store_true", help="keep a throwaway (--limit) dataset instead of deleting it")
    p.add_argument("--background", action="store_true", help="run cognify in background (non-blocking)")
    return p.parse_args()


async def _main() -> int:
    args = _parse_args()
    set_active = True if args.set_active else None
    await ingest_hero(limit=args.limit, set_active=set_active, keep=args.keep, background=args.background)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
