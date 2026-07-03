r"""P0b live smoke test: prove the Cognee round-trip works with Ariadne's config.

Run from backend/:  .\.venv\Scripts\python.exe -m scripts.smoke_p0
(or)                .\.venv\Scripts\python.exe scripts\smoke_p0.py

Exercises the real memory lifecycle against local OSS Cognee (or Cognee Cloud if
COGNEE_BASE_URL is set) using Ariadne's own CogneeClient wrapper:
  remember(clinical note) -> recall(question) -> forget(dataset).
Uses the same Azure LLM + Ollama embedding env as hindsight-os.
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# Allow running as a loose script (python scripts\smoke_p0.py).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.cognee_client import CogneeClient, QueryType  # noqa: E402

DATASET = "ariadne_smoke"

NOTE = (
    "2021-03-14 Encounter (rheumatology): 34-year-old with a 6-month history of "
    "recurrent fevers, an evanescent salmon-colored rash, and arthralgia. "
    "Ferritin markedly elevated at 1,240 ng/mL. Started on naproxen. "
    "Working impression: systemic inflammatory process, further workup planned."
)


async def main() -> int:
    s = get_settings()
    print(f"[config]   mode={s.mode}  llm={s.llm_model}  embed={s.embedding_model}")

    client = CogneeClient(s)
    await client.connect()

    t0 = time.time()
    try:
        await client.forget(data_id=None, dataset=DATASET)  # best-effort clean slate
    except Exception as e:
        print(f"[reset]    skipped ({type(e).__name__})")
    print(f"[reset]    {time.time() - t0:5.1f}s")

    t1 = time.time()
    await client.remember(NOTE, dataset_name=DATASET, self_improvement=False)
    print(f"[remember] {time.time() - t1:5.1f}s")

    t2 = time.time()
    res = await client.recall(
        query_text="When did the fevers and elevated ferritin begin?",
        query_type=QueryType.CHUNKS,
        datasets=[DATASET],
        top_k=5,
    )
    print(f"[recall]   {time.time() - t2:5.1f}s")

    items = res if isinstance(res, list) else [res]
    print(f"[recall]   {len(items)} item(s) returned")
    for r in items[:3]:
        text = getattr(r, "text", r)
        print(f"  - {str(text)[:300]}")

    await client.disconnect()
    print(f"[total]    {time.time() - t0:5.1f}s")

    ok = len(items) > 0
    print("[result]   PASS" if ok else "[result]   FAIL (no items recalled)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
