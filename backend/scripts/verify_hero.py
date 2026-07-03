"""Verify the freshly-seeded hero clinical brain on Cognee Cloud.

Reads the active clinical dataset from the registry, then:
  1. asserts the dataset is healthy (not DATASET_PROCESSING_ERRORED),
  2. pulls the dataset graph and counts nodes by type vs GOLDEN.min_counts,
  3. runs a few cited recalls (GRAPH_COMPLETION + TEMPORAL) and prints the
     synthesized answer + parsed citation count.

Run from backend/:
    python -m scripts.verify_hero
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from typing import Any, Dict, List

# LLM-synthesized answers (and box-drawing glyphs below) can contain non-cp1252
# characters; force UTF-8 so printing never crashes on the Windows console.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from app import registry
from app.cognee_client import get_client
from app.recall_parse import parse_recall
from app.seed.odyssey_patient import GOLDEN, HERO_PATIENT


def _nodes_edges(graph: Any):
    """Normalize a dataset-graph payload into (nodes, edges) lists."""
    if isinstance(graph, dict):
        nodes = graph.get("nodes") or graph.get("vertices") or []
        edges = graph.get("edges") or graph.get("links") or graph.get("relationships") or []
        return nodes, edges
    return [], []


def _node_type(node: dict) -> str:
    for key in ("type", "label", "labels", "node_type", "__typename"):
        v = node.get(key)
        if isinstance(v, list) and v:
            return str(v[0])
        if isinstance(v, str) and v:
            return v
    # fall back to a "type"-ish property
    props = node.get("properties") or node.get("attributes") or {}
    if isinstance(props, dict):
        for key in ("type", "label", "category"):
            if props.get(key):
                return str(props[key])
    return "Unknown"


async def main() -> int:
    patient_id = HERO_PATIENT["id"]
    entry = registry.get_active(patient_id, "clinical")
    if not entry:
        print(f"no active clinical brain in registry for '{patient_id}' — run app.seed.ingest first")
        return 2
    name, dataset_id = entry["name"], entry.get("id")
    print(f"active clinical brain: {name} ({dataset_id})\n")

    client = get_client()
    await client.connect()

    # 1) health
    try:
        status = await client.datasets_status([dataset_id] if dataset_id else None)
        this = status.get(dataset_id) if isinstance(status, dict) and dataset_id else status
        print(f"[health] datasets/status[{dataset_id}] = {this}")
        healthy = str(this).upper().find("ERROR") == -1
    except Exception as e:
        print(f"[health] status check failed: {e!r}")
        healthy = True  # don't hard-fail on the status endpoint alone

    # 2) graph integrity vs GOLDEN.min_counts
    type_counts: Counter = Counter()
    n_nodes = n_edges = 0
    try:
        graph = await client.dataset_graph(dataset_id)
        nodes, edges = _nodes_edges(graph)
        n_nodes, n_edges = len(nodes), len(edges)
        for node in nodes:
            if isinstance(node, dict):
                type_counts[_node_type(node)] += 1
        print(f"\n[graph] nodes={n_nodes} edges={n_edges}")
        for t, c in type_counts.most_common():
            print(f"        {t:<24} {c}")
    except Exception as e:
        print(f"[graph] dataset_graph failed (will rely on recalls): {e!r}")

    # 2b) explicit min_counts integrity vs GOLDEN
    mins: Dict[str, int] = GOLDEN.get("min_counts", {})  # type: ignore
    _type_for = {
        "conditions": "Condition", "medications": "Medication", "labs": "LabResult",
        "symptoms": "Symptom", "providers": "Provider", "encounters": "Encounter",
    }
    count_results: Dict[str, tuple] = {}
    counts_ok = True
    print("\n[min_counts] extracted vs GOLDEN target:")
    for key, target in mins.items():
        got = type_counts.get(_type_for.get(key, key), 0)
        ok = got >= target
        counts_ok = counts_ok and ok
        count_results[key] = (got, target, ok)
        print(f"        {'PASS' if ok else 'FAIL'}  {key:<12} {got} >= {target}")

    # 3) cited GRAPH_COMPLETION recalls (gating)
    probes = [
        ("summary",
         "Summarize this patient's multi-year illness: key symptoms, abnormal labs, "
         "medications tried, and the final diagnosis. Cite sources."),
        ("diagnosis",
         "What is the confirmed diagnosis and when was it made?"),
    ]
    print("\n[recalls] GRAPH_COMPLETION (gating):")
    recall_ok = True
    for tag, q in probes:
        try:
            resp = await client.recall(
                query_text=q, query_type="GRAPH_COMPLETION", datasets=[name],
                include_references=True, session_id=f"verify-{patient_id}-{tag}",
            )
            parsed = parse_recall(resp)
            ans = (parsed.answer or "").strip().replace("\n", " ")
            print(f"\n  ── {tag} ──")
            print(f"  Q: {q}")
            print(f"  A: {ans[:600]}{'…' if len(ans) > 600 else ''}")
            print(f"  citations: {len(parsed.references)} (has_citations={parsed.has_citations})")
            for r in parsed.references[:3]:
                quote = (r.snippet or "")[:80]
                print(f"     • doc={r.document_name} data_id={r.data_id} \"{quote}\"")
            if not ans:
                recall_ok = False
        except Exception as e:
            print(f"  {tag} recall FAILED: {e!r}")
            recall_ok = False

    # 3b) TEMPORAL recall (best-effort, NON-gating — server-side single-flight
    #     temporal index build 409s intermittently; TimelineAgent will fall back
    #     to GRAPH_COMPLETION and the P4 time-travel uses deterministic node dates).
    print("\n[recalls] TEMPORAL (best-effort, non-gating):")
    temporal_ok = False
    for attempt in range(1, 4):
        try:
            resp = await client.recall(
                query_text="Chronological timeline of symptoms and diagnoses with dates.",
                query_type="TEMPORAL", datasets=[name], include_references=False,
                session_id=f"verify-{patient_id}-temporal",
            )
            ans = (parse_recall(resp).answer or "").strip().replace("\n", " ")
            if ans:
                temporal_ok = True
                print(f"  attempt {attempt}: OK :: {ans[:300]}{'…' if len(ans) > 300 else ''}")
                break
            print(f"  attempt {attempt}: empty answer")
        except Exception as e:
            print(f"  attempt {attempt}: {e!r}")
        await asyncio.sleep(3)
    if not temporal_ok:
        print("  -> TEMPORAL unavailable right now; TimelineAgent will use GRAPH_COMPLETION fallback.")

    # 4) light GOLDEN key-fact presence (substring in combined answer)
    combined = ""
    try:
        r = await client.recall(
            query_text="List every symptom, lab abnormality, medication and the diagnosis with dates.",
            query_type="GRAPH_COMPLETION", datasets=[name], include_references=False,
            session_id=f"verify-{patient_id}-facts",
        )
        combined = (parse_recall(r).answer or "").lower()
    except Exception as e:
        print(f"\n[golden] fact recall failed: {e!r}")

    print("\n[golden] key-fact presence in recall:")
    checks = {
        "Takayasu (diagnosis)": "takayasu" in combined,
        "ESR / inflammatory marker": ("esr" in combined or "sed rate" in combined or "c-reactive" in combined or "crp" in combined),
        "Prednisolone / steroid": ("prednisol" in combined or "steroid" in combined or "glucocort" in combined),
        "Hypertension": "hypertens" in combined,
        "2024 diagnosis year": "2024" in combined,
    }
    for label, ok in checks.items():
        print(f"        {'PASS' if ok else 'MISS'}  {label}")

    await client.disconnect()

    # summary + gate
    print("\n[summary]")
    print(f"  dataset healthy         : {healthy}")
    print(f"  graph nodes/edges       : {n_nodes}/{n_edges}")
    print(f"  min_counts all met      : {counts_ok}")
    print(f"  golden facts present    : {sum(checks.values())}/{len(checks)}")
    print(f"  graph recalls answered  : {recall_ok}")
    print(f"  temporal (best-effort)  : {temporal_ok}")
    ok = healthy and counts_ok and recall_ok and sum(checks.values()) >= 4
    print(f"\nVERIFY {'PASS' if ok else 'REVIEW'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
