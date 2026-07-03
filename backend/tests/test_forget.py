"""Offline unit tests for app/forget.py — the forget()-with-proof lifecycle.

A recording fake cloud simulates the live before/after behaviour (graph shrinks when a
data item is forgotten; a recall of the forgotten fact flips Yes->No; unrelated memory
survives). The verdict/mentions heuristics are exercised against the exact live answer
phrasings so the surgical verdict is deterministic.
"""

from __future__ import annotations

import pytest

from app import forget as FGT


# --------------------------------------------------------------------------- #
# robust answer parsing
# --------------------------------------------------------------------------- #
def test_verdict_parses_live_phrasings():
    assert FGT.verdict("**Answer: Yes.** confirmed Type 1 diabetes mellitus.") == "yes"
    assert FGT.verdict("**Answer: No.**  Neither node mentions diabetes.") == "no"
    assert FGT.verdict("The patient takes aspirin for cardiovascular protection.") is None


def test_mentions_respects_negative_verdict():
    assert FGT.mentions("The patient takes aspirin for cardiovascular protection.", "aspirin")
    # present as a term but under a 'No' verdict -> not an affirmation
    assert not FGT.mentions("**Answer: No.** no aspirin information here.", "aspirin")
    assert not FGT.mentions("nothing relevant", "aspirin")


# --------------------------------------------------------------------------- #
# ForgetProof verdict logic
# --------------------------------------------------------------------------- #
def _proof(**over):
    base = dict(
        dataset="d", data_id="x", forget_status="success",
        nodes_before=15, nodes_after=6, edges_before=13, edges_after=5,
        probe_query=FGT.BAD_QUERY, probe_term=FGT.BAD_TERM,
        probe_before="**Answer: Yes.** confirmed Type 1 diabetes mellitus.",
        probe_after="**Answer: No.** graph only mentions cardiology and aspirin.",
        unrelated_query=FGT.KEEP_QUERY, unrelated_term=FGT.KEEP_TERM,
        unrelated_after="The patient takes aspirin for cardiovascular protection.")
    base.update(over)
    return FGT.ForgetProof(**base)


def test_surgical_forget_recognised():
    p = _proof()
    assert p.is_surgical
    assert p.nodes_removed == 9 and p.edges_removed == 8
    assert p.probe_present_before and p.probe_absent_after and p.unrelated_survives


def test_non_removing_forget_rejected():
    p = _proof(nodes_after=15, edges_after=13,
               probe_after="**Answer: Yes.** still confirmed diabetes.")
    assert not p.is_surgical
    assert not p.graph_shrank and not p.probe_absent_after


def test_collateral_damage_rejected():
    p = _proof(nodes_after=1, edges_after=0,
               unrelated_after="**Answer: No.** no aspirin information.")
    assert not p.is_surgical
    assert not p.unrelated_survives


def test_failed_deletion_rejected():
    p = _proof(forget_status="error")
    assert not p.deletion_succeeded and not p.is_surgical


# --------------------------------------------------------------------------- #
# fake cloud — end-to-end seed + prove
# --------------------------------------------------------------------------- #
class _FakeForgetCloud:
    def __init__(self):
        self.datasets = {}       # name -> {"id", "items": {data_id: {"text","nodes"}}}
        self.forgotten = set()
        self._n = 0

    async def remember(self, data, dataset_name, node_set=None, session_id=None,
                       graph_model=None, custom_prompt=None, self_improvement=None,
                       run_in_background=False):
        self._n += 1
        did = f"data-{self._n}"
        ds = self.datasets.setdefault(dataset_name, {"id": f"dsid-{dataset_name}", "items": {}})
        nodes = 9 if "diabetes" in data.lower() else 6      # mirror the live counts
        ds["items"][did] = {"text": data, "nodes": nodes}
        return {"status": "completed", "dataset_id": ds["id"], "items": [{"id": did}]}

    def _ds_by_id(self, dataset_id):
        return next((d for d in self.datasets.values() if d["id"] == dataset_id), None)

    async def dataset_graph(self, dataset_id):
        ds = self._ds_by_id(dataset_id)
        nodes, edges = [], []
        if ds:
            for did, item in ds["items"].items():
                if did in self.forgotten:
                    continue
                nodes += [f"{did}-n{i}" for i in range(item["nodes"])]
                edges += [f"{did}-e{i}" for i in range(max(0, item["nodes"] - 1))]
        return {"nodes": nodes, "edges": edges}

    async def recall(self, query_text, query_type=None, datasets=None,
                     include_references=True, **kw):
        ds = self.datasets.get(datasets[0] if datasets else None, {"items": {}})
        alive = {did: it for did, it in ds["items"].items() if did not in self.forgotten}
        has_diabetes = any("diabetes" in it["text"].lower() for it in alive.values())
        has_aspirin = any("aspirin" in it["text"].lower() for it in alive.values())
        q = query_text.lower()
        if "diabetes" in q:
            ans = ("**Answer: Yes.** confirmed Type 1 diabetes mellitus." if has_diabetes
                   else "**Answer: No.** the graph only mentions cardiology and aspirin.")
        elif "aspirin" in q:
            ans = ("The patient takes aspirin for cardiovascular protection." if has_aspirin
                   else "**Answer: No.** no aspirin information.")
        else:
            ans = "n/a"
        return [{"text": ans}]

    async def forget(self, data_id=None, dataset=None, everything=False, memory_only=False):
        ds = self.datasets.get(dataset)
        self.forgotten.add(data_id)
        return {"data_id": data_id, "dataset_id": ds["id"] if ds else None,
                "status": "success"}

    async def delete_dataset(self, dataset_id):
        return {"status": "deleted"}


async def test_seed_forget_fixture_returns_two_ids():
    c = _FakeForgetCloud()
    ds_id, keep_id, bad_id = await FGT.seed_forget_fixture(c, dataset="ds1")
    assert ds_id == "dsid-ds1"
    assert keep_id and bad_id and keep_id != bad_id


async def test_prove_forget_is_surgical_end_to_end():
    c = _FakeForgetCloud()
    ds_id, keep_id, bad_id = await FGT.seed_forget_fixture(c, dataset="ds1")
    proof = await FGT.prove_forget(c, dataset="ds1", dataset_id=ds_id,
                                   data_id=bad_id, settle=0.0, poll_interval=0.0)
    assert proof.is_surgical
    assert proof.nodes_before == 15 and proof.nodes_after == 6      # 9 diabetes nodes gone
    assert proof.probe_present_before and proof.probe_absent_after
    assert proof.unrelated_survives


async def test_forgetting_the_kept_record_is_not_surgical():
    # forgetting the aspirin (KEEP) record leaves the diabetes fact intact -> not surgical
    c = _FakeForgetCloud()
    ds_id, keep_id, bad_id = await FGT.seed_forget_fixture(c, dataset="ds1")
    proof = await FGT.prove_forget(c, dataset="ds1", dataset_id=ds_id,
                                   data_id=keep_id, settle=0.0, poll_attempts=2,
                                   poll_interval=0.0)
    assert not proof.is_surgical
    assert not proof.probe_absent_after        # diabetes still recallable
    assert not proof.unrelated_survives        # aspirin was the thing we deleted


async def test_forget_record_wrapper_returns_status():
    c = _FakeForgetCloud()
    ds_id, _keep, bad_id = await FGT.seed_forget_fixture(c, dataset="ds1")
    resp = await FGT.forget_record(c, dataset="ds1", data_id=bad_id)
    assert resp["status"] == "success" and resp["data_id"] == bad_id
