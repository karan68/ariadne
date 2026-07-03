"""Ingest the two global read-only reference brains into Cognee Cloud:

  * reference_literature — condition -> phenotype-constellation patterns
    (Takayasu + differentials) for the ConnectionsAgent's cited literature support.
  * reference_trials     — trials + eligibility criteria for the TrialsAgent.

These are *global* (shared, read-only) brains, not per-patient, so we register them
in the registry under the pseudo patient id "global" (kinds "literature"/"trials").

Uses the same robust pattern as the hero seed: always ingest into a fresh,
uniquely-versioned dataset name (Cloud delete is async → reusing a name wedges the
dataset), record it in the registry, and best-effort delete the previous healthy
version afterward.

Run from backend/:
    python -m app.seed.ingest_reference                 # both brains
    python -m app.seed.ingest_reference --only literature
    python -m app.seed.ingest_reference --only trials
    python -m app.seed.ingest_reference --limit 2        # quick throwaway test
"""

from __future__ import annotations

import argparse
import asyncio
import re
import time
from datetime import datetime
from typing import Callable, Iterator, List, Optional, Tuple

from app import registry
from app.cognee_client import get_client
from app.config import get_settings
from app.ontology import (
    LITERATURE_EXTRACTION_PROMPT,
    TRIALS_EXTRACTION_PROMPT,
    reference_literature_graph_model_json,
    reference_trials_graph_model_json,
)
from app.seed.reference_data import iter_literature, iter_trials

GLOBAL_PATIENT = "global"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _lit_node_set(meta: dict) -> List[str]:
    tags = ["ref:literature"]
    if meta.get("condition"):
        tags.append(f"condition:{_slug(meta['condition'])}")
    return tags


def _trial_node_set(meta: dict) -> List[str]:
    tags = ["ref:trials"]
    if meta.get("nct_id"):
        tags.append(f"nct:{meta['nct_id']}")
    if meta.get("condition"):
        tags.append(f"condition:{_slug(meta['condition'])}")
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
    except Exception as e:  # noqa: BLE001
        print(f"  (warning) delete failed (non-fatal): {e!r}")


async def _ingest_brain(
    client,
    *,
    kind: str,
    base_name: str,
    docs: List[Tuple[str, str, dict]],
    graph_model: dict,
    custom_prompt: str,
    node_set_fn: Callable[[dict], List[str]],
    background: bool,
    keep: bool,
    set_active: bool,
) -> dict:
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    dataset = f"{base_name}__{ts}"  # versioned, always net-new
    prev = registry.get_active(GLOBAL_PATIENT, kind) if set_active else None

    data_ids: List[str] = []
    dataset_id: Optional[str] = None
    print(f"\n[{kind}] ingesting {len(docs)} doc(s) into '{dataset}' "
          f"(graph_model title={graph_model.get('title')}, background={background})")
    try:
        for i, (doc_id, text, meta) in enumerate(docs, start=1):
            tags = node_set_fn(meta)
            t0 = time.time()
            resp = await client.remember(
                data=text,
                dataset_name=dataset,
                node_set=tags,
                graph_model=graph_model,
                custom_prompt=custom_prompt,
                run_in_background=background,
            )
            dt = time.time() - t0
            status = resp.get("status") if isinstance(resp, dict) else None
            if isinstance(resp, dict):
                dataset_id = resp.get("dataset_id") or dataset_id
                ids = [it.get("id") for it in (resp.get("items") or []) if isinstance(it, dict)]
                data_ids.extend([x for x in ids if x])
            print(f"  [{i}/{len(docs)}] {doc_id:<24} {status or '?':<11} {dt:5.1f}s tags={tags}")
            if status not in ("completed", "running", "session_stored", None):
                raise RuntimeError(f"unexpected remember status for {doc_id}: {status!r} :: {resp}")
    except Exception:
        bad = dataset_id or await _find_dataset_id(client, dataset)
        await _best_effort_delete(client, bad, f"[{kind}] failure cleanup")
        raise

    if dataset_id is None:
        dataset_id = await _find_dataset_id(client, dataset)

    unique_ids = list(dict.fromkeys(data_ids))  # remember items are cumulative -> dedupe

    if set_active:
        registry.set_active(GLOBAL_PATIENT, kind, dataset, dataset_id)
        print(f"[{kind}] registry: active brain -> {dataset} ({dataset_id})")
        if prev and prev.get("id") and prev.get("id") != dataset_id:
            await _best_effort_delete(client, prev.get("id"), f"[{kind}] superseded previous brain")
    elif not keep:
        await _best_effort_delete(client, dataset_id, f"[{kind}] throwaway test cleanup")

    return {
        "kind": kind,
        "dataset": dataset,
        "dataset_id": dataset_id,
        "documents": len(unique_ids),
        "data_ids": unique_ids,
        "active": bool(set_active),
    }


async def ingest_reference(
    only: Optional[str] = None,
    limit: Optional[int] = None,
    set_active: Optional[bool] = None,
    keep: bool = False,
    background: bool = False,
) -> dict:
    settings = get_settings()
    if not settings.is_cloud():
        raise RuntimeError("ingest_reference requires cloud mode (set COGNEE_BASE_URL)")

    is_full = limit is None
    if set_active is None:
        set_active = is_full

    client = get_client()
    await client.connect()
    results: dict = {}
    try:
        if only in (None, "literature"):
            lit_docs = list(iter_literature())
            if limit is not None:
                lit_docs = lit_docs[:limit]
            results["literature"] = await _ingest_brain(
                client,
                kind="literature",
                base_name=settings.reference_literature,
                docs=lit_docs,
                graph_model=reference_literature_graph_model_json(),
                custom_prompt=LITERATURE_EXTRACTION_PROMPT,
                node_set_fn=_lit_node_set,
                background=background,
                keep=keep,
                set_active=set_active,
            )
        if only in (None, "trials"):
            trial_docs = list(iter_trials())
            if limit is not None:
                trial_docs = trial_docs[:limit]
            results["trials"] = await _ingest_brain(
                client,
                kind="trials",
                base_name=settings.reference_trials,
                docs=trial_docs,
                graph_model=reference_trials_graph_model_json(),
                custom_prompt=TRIALS_EXTRACTION_PROMPT,
                node_set_fn=_trial_node_set,
                background=background,
                keep=keep,
                set_active=set_active,
            )
    finally:
        await client.disconnect()

    print("\nREFERENCE INGEST DONE:")
    for kind, r in results.items():
        print(f"  {kind:<11} dataset={r['dataset']} id={r['dataset_id']} "
              f"documents={r['documents']} active={r['active']}")
    return results


def _parse_args():
    p = argparse.ArgumentParser(description="Ingest the global reference brains into Cognee Cloud")
    p.add_argument("--only", choices=["literature", "trials"], default=None,
                   help="ingest only one reference brain")
    p.add_argument("--limit", type=int, default=None,
                   help="only ingest the first N docs per brain (throwaway test)")
    p.add_argument("--set-active", action="store_true", help="force updating the active brain pointer")
    p.add_argument("--keep", action="store_true", help="keep a throwaway (--limit) dataset instead of deleting it")
    p.add_argument("--background", action="store_true", help="run cognify in background (non-blocking)")
    return p.parse_args()


async def _main() -> int:
    args = _parse_args()
    set_active = True if args.set_active else None
    await ingest_reference(only=args.only, limit=args.limit, set_active=set_active,
                           keep=args.keep, background=args.background)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
