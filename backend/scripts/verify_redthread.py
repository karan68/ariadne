"""Live verification of the P4 red-thread (graph-backed cited provenance path).

Builds the red-thread for Takayasu arteritis over the live clinical + literature brains
and confirms every hop is a real edge in the graph, terminating at the source document.

Run:  python -m scripts.verify_redthread
"""

from __future__ import annotations

import asyncio
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.cognee_client import get_client
from app.redthread import run_redthread


def _print_thread(t) -> None:
    parts = [f"[{t.anchor_type}] {t.anchor_label}"]
    for hop in t.hops:
        parts.append(f"  <{hop.relation}>  [{hop.source_type}] {hop.source_label[:40]}")
    print("   " + "\n   ".join(parts))
    print(f"      => document {t.document_label} (chunk {str(t.chunk_id)[:8]}…)")
    if t.quote:
        print(f"      quote: {t.quote[:110].strip()}…")


async def main() -> int:
    client = get_client()
    await client.connect()
    try:
        bundle = await run_redthread(client, "odyssey", "Takayasu arteritis")
    finally:
        await client.disconnect()

    print(f"condition        : {bundle.condition}")
    print(f"clinical brain   : {bundle.clinical_dataset}")
    print(f"literature brain : {bundle.literature_dataset}\n")

    print(f"--- patient provenance threads ({len(bundle.patient_threads)}) ---")
    for t in bundle.patient_threads:
        _print_thread(t)
    print(f"\n--- literature provenance threads ({len(bundle.literature_threads)}) ---")
    for t in bundle.literature_threads:
        _print_thread(t)
    if bundle.unresolved_anchors:
        print(f"\nunresolved (no citation, excluded): {bundle.unresolved_anchors}")

    resolved_patient = [t for t in bundle.patient_threads if t.resolved]
    resolved_lit = [t for t in bundle.literature_threads if t.resolved]
    checks = [
        ("every hop of every thread is a real graph edge", bundle.all_edges_exist),
        ("at least 2 patient discriminator threads resolved", len(resolved_patient) >= 2),
        ("the literature pattern is provenance-traced", len(resolved_lit) >= 1),
        ("every resolved thread terminates at a source document",
         all(t.document_id and t.chunk_id for t in bundle.threads)),
        ("every resolved thread carries a verbatim quote",
         all(bool(t.quote) for t in bundle.threads)),
        ("the confirmed condition is one of the anchors",
         any("takayasu" in (t.anchor_label or "").lower() for t in bundle.threads)),
    ]
    print("\n--- contract ---")
    ok = True
    for label, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}")
        ok = ok and passed

    print("\n" + ("VERIFY REDTHREAD PASS" if ok else "VERIFY REDTHREAD FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
