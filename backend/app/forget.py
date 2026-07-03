"""forget() — surgical deletion with proof.

The Cloud `forget` verb is `POST /api/v1/forget` with a `ForgetPayloadDTO`
(grounded against the live OpenAPI spec):

    {dataId?, dataset? | datasetId?, everything=False, memoryOnly=False}

  * `dataId` + `dataset`/`datasetId` → remove a **single** data item (its graph
    nodes/edges + vector embeddings). This is Ariadne's "correct a mislabeled
    record" / "patient exercises the right to be forgotten" surface.
  * `memoryOnly=True` with a dataset → clear the graph + vectors but keep the raw
    files (re-cognifiable).
  * `everything=True` → DANGER, deletes ALL of the user's data (never used here).

Verified live (before/after on a disposable dataset): forgetting the data_id of a
deliberately mislabeled record drops the graph node/edge count, flips a recall of
that fact from "Yes" to "No", and leaves unrelated concepts intact — the response is
`{"data_id", "dataset_id", "status": "success"}`.

Two layers, same shape as the rest of P3:
  1. **Cloud verb (live):** `forget_record` (thin app-facing wrapper) + `prove_forget`
     (before/after graph-count + recall proof). The real forget() hitting the real Cloud.
  2. **App-level (deterministic):** `ForgetProof` computes a *surgical* verdict from the
     captured before/after signals — robust to LLM phrasing (yes/no verdict parsing),
     so the gate is reproducible.

Safety: proofs run against a **disposable, versioned** dataset (seeded by
`seed_forget_fixture`) so a forget can never wedge or mutate the precious hero brain
(a failed forget/cognify can errored-wedge a dataset — see the P1 build log).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

# --------------------------------------------------------------------------- #
# fixture constants (shared by the eval, the verify script, and the unit tests)
# --------------------------------------------------------------------------- #
#: a genuine record that must SURVIVE the forget
KEEP_DOC = ("Encounter 2024-02-10 cardiology. The patient takes aspirin 75 mg once "
            "daily for cardiovascular protection.")
KEEP_TERM = "aspirin"
KEEP_QUERY = "What does the patient take aspirin for?"

#: a deliberately MISLABELED record that must be forgotten (the patient never had it)
BAD_DOC = ("Encounter 2024-02-11 endocrinology. The patient has a confirmed diagnosis "
           "of Type 1 diabetes mellitus; HbA1c 11.2 percent; started insulin glargine.")
BAD_TERM = "diabetes"
BAD_QUERY = "Does the patient have diabetes? Answer yes or no with evidence."


# --------------------------------------------------------------------------- #
# robust answer parsing (deterministic — no dependency on exact LLM phrasing)
# --------------------------------------------------------------------------- #
_VERDICT_RE = re.compile(r"answer[:\*\s]*\b(yes|no)\b", re.IGNORECASE)


def verdict(answer: str) -> Optional[str]:
    """Parse a forced yes/no recall answer into 'yes' | 'no' | None."""
    m = _VERDICT_RE.search(answer or "")
    return m.group(1).lower() if m else None


def mentions(answer: str, term: str) -> bool:
    """True when `term` is affirmatively present (not under a 'No' verdict)."""
    a = (answer or "").lower()
    return term.lower() in a and verdict(answer) != "no"


# --------------------------------------------------------------------------- #
# proof
# --------------------------------------------------------------------------- #
@dataclass
class ForgetProof:
    dataset: str
    data_id: str
    forget_status: str
    nodes_before: int
    nodes_after: int
    edges_before: int
    edges_after: int
    probe_query: str
    probe_term: str
    probe_before: str
    probe_after: str
    unrelated_query: str
    unrelated_term: str
    unrelated_after: str

    @property
    def nodes_removed(self) -> int:
        return self.nodes_before - self.nodes_after

    @property
    def edges_removed(self) -> int:
        return self.edges_before - self.edges_after

    @property
    def deletion_succeeded(self) -> bool:
        return self.forget_status == "success"

    @property
    def graph_shrank(self) -> bool:
        return self.nodes_after < self.nodes_before

    @property
    def probe_present_before(self) -> bool:
        return verdict(self.probe_before) == "yes"

    @property
    def probe_absent_after(self) -> bool:
        return verdict(self.probe_after) == "no"

    @property
    def unrelated_survives(self) -> bool:
        return mentions(self.unrelated_after, self.unrelated_term)

    @property
    def is_surgical(self) -> bool:
        """The full forget-with-proof contract: the delete succeeded, the graph
        shrank, the forgotten fact is no longer recallable, and unrelated memory
        survived."""
        return (self.deletion_succeeded and self.graph_shrank
                and self.probe_present_before and self.probe_absent_after
                and self.unrelated_survives)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset": self.dataset, "data_id": self.data_id,
            "forget_status": self.forget_status,
            "nodes_before": self.nodes_before, "nodes_after": self.nodes_after,
            "edges_before": self.edges_before, "edges_after": self.edges_after,
            "nodes_removed": self.nodes_removed,
            "probe_present_before": self.probe_present_before,
            "probe_absent_after": self.probe_absent_after,
            "unrelated_survives": self.unrelated_survives,
            "is_surgical": self.is_surgical,
        }


# --------------------------------------------------------------------------- #
# cloud helpers
# --------------------------------------------------------------------------- #
async def _graph_counts(client, dataset_id: str) -> Tuple[int, int]:
    g = await client.dataset_graph(dataset_id)
    g = g if isinstance(g, dict) else {}
    return len(g.get("nodes") or []), len(g.get("edges") or [])


async def _recall_text(client, query: str, dataset: str) -> str:
    res = await client.recall(query_text=query, query_type="GRAPH_COMPLETION",
                              datasets=[dataset], include_references=True)
    if isinstance(res, list) and res:
        return str(res[0].get("text") or "")
    return str(res or "")


async def _recall_until_absent(client, query: str, dataset: str, *,
                               attempts: int, interval: float) -> str:
    """Poll the probe recall until its verdict flips to 'no' (the deletion has
    propagated to the retrieval layer) or the attempts are exhausted.

    The graph delete is immediate, but the vector/summary retrieval index is
    **eventually-consistent** on this tenant — a single-shot after-recall can still
    surface a residual chunk for a few seconds. A bounded poll makes the proof
    reproducible without masking a genuine failure (an item that is truly still present
    keeps answering 'yes' through every attempt and the proof correctly fails)."""
    answer = ""
    for i in range(max(1, attempts)):
        answer = await _recall_text(client, query, dataset)
        if verdict(answer) == "no":
            return answer
        if i < attempts - 1 and interval:
            await asyncio.sleep(interval)
    return answer


async def forget_record(client, *, dataset: str, data_id: str,
                        dataset_id: Optional[str] = None) -> Dict[str, Any]:
    """App-facing surgical forget of one record (patient's right to be forgotten /
    correcting a mislabeled item). Returns the Cloud response
    `{data_id, dataset_id, status}`."""
    resp = await client.forget(data_id=data_id, dataset=dataset)
    return resp if isinstance(resp, dict) else {"status": str(resp)}


async def seed_forget_fixture(client, *, dataset: str, keep_doc: str = KEEP_DOC,
                              bad_doc: str = BAD_DOC, graph_model: Optional[str] = None,
                              custom_prompt: Optional[str] = None,
                              retries: int = 4, backoff: float = 6.0
                              ) -> Tuple[Optional[str], str, str]:
    """Seed a disposable dataset with a KEEP doc + a mislabeled BAD doc (two separate
    data items). Returns `(dataset_id, keep_data_id, bad_data_id)`. Retries the
    permanent-remember on the transient 409 the shared tenant can raise under load."""
    from app.cloud_client import CloudError

    async def _remember(text: str) -> Dict[str, Any]:
        last: Optional[Exception] = None
        for attempt in range(retries):
            try:
                return await client.remember(
                    data=text, dataset_name=dataset, graph_model=graph_model,
                    custom_prompt=custom_prompt, run_in_background=False)
            except CloudError as exc:
                if getattr(exc, "status", None) == 409:
                    last = exc
                    await asyncio.sleep(backoff * (attempt + 1))
                    continue
                raise
        raise last or CloudError(409, "seed_forget_fixture exhausted retries")

    def _first_id(resp: Dict[str, Any]) -> str:
        items = resp.get("items") if isinstance(resp, dict) else None
        for it in items or []:
            if isinstance(it, dict) and it.get("id"):
                return it["id"]
        return ""

    r_keep = await _remember(keep_doc)
    r_bad = await _remember(bad_doc)
    dataset_id = (r_bad.get("dataset_id") if isinstance(r_bad, dict) else None) \
        or (r_keep.get("dataset_id") if isinstance(r_keep, dict) else None)
    return dataset_id, _first_id(r_keep), _first_id(r_bad)


async def prove_forget(client, *, dataset: str, dataset_id: str, data_id: str,
                       probe_query: str = BAD_QUERY, probe_term: str = BAD_TERM,
                       unrelated_query: str = KEEP_QUERY, unrelated_term: str = KEEP_TERM,
                       settle: float = 4.0, poll_attempts: int = 6,
                       poll_interval: float = 5.0) -> ForgetProof:
    """Capture the before/after graph counts + recalls around a surgical forget of
    `data_id`, returning a `ForgetProof` whose `.is_surgical` is the gate.

    The after-recall of the forgotten fact is a **bounded poll** (`poll_attempts` x
    `poll_interval`) so eventual-consistency of the retrieval index doesn't flake the
    gate; a fact that is genuinely still present answers 'yes' through every attempt."""
    nodes_before, edges_before = await _graph_counts(client, dataset_id)
    probe_before = await _recall_text(client, probe_query, dataset)

    resp = await forget_record(client, dataset=dataset, data_id=data_id,
                               dataset_id=dataset_id)
    status = str(resp.get("status") or "")
    if settle:
        await asyncio.sleep(settle)

    nodes_after, edges_after = await _graph_counts(client, dataset_id)
    probe_after = await _recall_until_absent(client, probe_query, dataset,
                                             attempts=poll_attempts, interval=poll_interval)
    unrelated_after = await _recall_text(client, unrelated_query, dataset)

    return ForgetProof(
        dataset=dataset, data_id=data_id, forget_status=status,
        nodes_before=nodes_before, nodes_after=nodes_after,
        edges_before=edges_before, edges_after=edges_after,
        probe_query=probe_query, probe_term=probe_term,
        probe_before=probe_before, probe_after=probe_after,
        unrelated_query=unrelated_query, unrelated_term=unrelated_term,
        unrelated_after=unrelated_after)
