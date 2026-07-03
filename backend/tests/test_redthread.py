"""Offline unit tests for app/redthread.py — the graph-backed cited provenance path.

Pure functions only (no cloud): a fixture graph mirroring the live topology
(container --rel--> entity; DocumentChunk --contains--> container; DocumentChunk
--is_part_of--> TextDocument). Verifies the provenance walk uses only real edges,
terminates at the source document with the verbatim quote, never fabricates a citation
for an un-sourced anchor, and that the bundle-level edge validation is exact.
"""

from __future__ import annotations

from app import redthread as RT
from app.graph_utils import clinical_mention
from app.normalize import Normalizer


def _graph():
    nodes = [
        {"id": "sym1", "type": "Symptom", "properties": {"name": "Intermittent claudication"}},
        {"id": "sym2", "type": "Symptom", "properties": {"name": "Fatigue"}},
        {"id": "cond", "type": "Condition",
         "properties": {"name": "Takayasu arteritis", "status": "confirmed"}},
        {"id": "cg", "type": "ClinicalKnowledgeGraph", "properties": {}},
        {"id": "chunk", "type": "DocumentChunk",
         "properties": {"text": "left-arm claudication on exertion; absent radial pulse"}},
        {"id": "doc", "type": "TextDocument", "label": "doc_0", "properties": {}},
        # a second container with no chunk -> its entity cannot be sourced
        {"id": "cg2", "type": "ClinicalKnowledgeGraph", "properties": {}},
        {"id": "orphan", "type": "Symptom", "properties": {"name": "Cold extremities"}},
    ]
    edges = [
        {"source": "cg", "target": "sym1", "label": "symptoms"},
        {"source": "cg", "target": "sym2", "label": "symptoms"},
        {"source": "cg", "target": "cond", "label": "conditions"},
        {"source": "chunk", "target": "cg", "label": "contains"},
        {"source": "chunk", "target": "doc", "label": "is_part_of"},
        {"source": "cg2", "target": "orphan", "label": "symptoms"},
    ]
    return nodes, edges


# --------------------------------------------------------------------------- #
# provenance walk
# --------------------------------------------------------------------------- #
def test_trace_resolves_to_source_document_over_real_edges():
    nodes, edges = _graph()
    t = RT.trace_provenance("sym1", nodes, edges)
    assert t.resolved
    assert t.chunk_id == "chunk" and t.document_id == "doc"
    assert t.document_label == "doc_0"
    assert "claudication" in (t.quote or "")
    # exactly three hops: container->entity, chunk->container, chunk->document
    assert [h.relation for h in t.hops] == ["symptoms", "contains", "is_part_of"]


def test_every_hop_is_a_real_edge():
    nodes, edges = _graph()
    t = RT.trace_provenance("cond", nodes, edges)
    triples = RT.edge_triples(edges)
    assert t.resolved
    assert all(h.triple in triples for h in t.hops)


def test_unsourced_anchor_is_not_fabricated():
    nodes, edges = _graph()
    t = RT.trace_provenance("orphan", nodes, edges)
    # container found, but no chunk -> unresolved, no document, no quote
    assert not t.resolved
    assert t.document_id is None and t.chunk_id is None
    assert len(t.hops) == 1   # only container->entity


def test_missing_anchor_returns_empty_thread():
    nodes, edges = _graph()
    t = RT.trace_provenance("does-not-exist", nodes, edges)
    assert not t.resolved and not t.hops


# --------------------------------------------------------------------------- #
# anchor selection
# --------------------------------------------------------------------------- #
def test_find_phenotype_anchors_selects_vascular_only():
    nodes, edges = _graph()
    anchors = RT.find_phenotype_anchors(nodes, {"HP:0004417"}, Normalizer())  # claudication
    labels = {clinical_mention(a).lower() for a in anchors}
    assert any("claudication" in l for l in labels)
    assert not any(l == "fatigue" for l in labels)


def test_find_condition_anchor_prefers_confirmed():
    nodes, edges = _graph()
    a = RT.find_condition_anchor(nodes, "Takayasu arteritis")
    assert a is not None and a["id"] == "cond"


def test_find_pattern_anchor_matches_condition_property():
    lit_nodes = [
        {"id": "p1", "type": "LiteraturePattern", "properties": {"condition": "Takayasu arteritis"}},
        {"id": "p2", "type": "LiteraturePattern", "properties": {"condition": "Lymphoma"}},
    ]
    a = RT.find_pattern_anchor(lit_nodes, "Takayasu arteritis")
    assert a is not None and a["id"] == "p1"


# --------------------------------------------------------------------------- #
# bundle validation
# --------------------------------------------------------------------------- #
def test_bundle_validate_true_on_real_edges():
    nodes, edges = _graph()
    bundle = RT.RedThreadBundle(condition="Takayasu arteritis")
    bundle.patient_threads.append(RT.trace_provenance("sym1", nodes, edges))
    bundle.patient_threads.append(RT.trace_provenance("cond", nodes, edges))
    assert RT.validate(bundle, edges) is True
    assert bundle.all_edges_exist


def test_bundle_validate_false_on_a_tampered_hop():
    nodes, edges = _graph()
    bundle = RT.RedThreadBundle(condition="Takayasu arteritis")
    t = RT.trace_provenance("sym1", nodes, edges)
    # tamper: point a hop at a target that has no such edge
    t.hops[0].target_id = "ghost"
    bundle.patient_threads.append(t)
    assert RT.validate(bundle, edges) is False
    assert not bundle.all_edges_exist


def test_validate_over_ignores_unresolved_threads():
    nodes, edges = _graph()
    unresolved = RT.trace_provenance("orphan", nodes, edges)
    # an unresolved thread contributes no hops to validate -> vacuously ok
    assert RT.validate_over([unresolved], edges) is True
