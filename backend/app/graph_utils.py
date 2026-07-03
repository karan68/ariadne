"""Helpers for the Cognee Cloud `dataset_graph` payload.

The Cloud returns `{"nodes":[{id,label,type,properties}], "edges":[{source,target,label}]}`.
These helpers normalize access so the eval, the agents, and the P4 red-thread
visualization all read the graph the same way.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Set, Tuple

# Clinical node types produced by the ClinicalKnowledgeGraph ontology (vs Cognee
# infrastructure nodes like DocumentChunk / TextSummary / NodeSet / TextDocument).
CLINICAL_NODE_TYPES: Set[str] = {
    "Patient", "Encounter", "Symptom", "Condition", "Medication", "LabResult",
    "ImagingStudy", "Procedure", "FamilyHistory", "GeneVariant", "Provider",
    "Trial", "EligibilityCriterion", "LiteraturePattern",
}

INFRA_NODE_TYPES: Set[str] = {
    "NodeSet", "TextSummary", "DocumentChunk", "TextDocument",
    "ClinicalKnowledgeGraph", "Entity", "EntityType",
}


def nodes_edges(graph: Any) -> Tuple[List[dict], List[dict]]:
    if isinstance(graph, dict):
        nodes = graph.get("nodes") or graph.get("vertices") or []
        edges = graph.get("edges") or graph.get("links") or graph.get("relationships") or []
        return list(nodes), list(edges)
    return [], []


def node_id(node: dict) -> str:
    for k in ("id", "node_id", "uuid", "_id"):
        if node.get(k):
            return str(node[k])
    props = node.get("properties") or {}
    return str(props.get("id", "")) if isinstance(props, dict) else ""


def node_type(node: dict) -> str:
    for k in ("type", "label_type", "node_type", "label"):
        v = node.get(k)
        if isinstance(v, list) and v:
            return str(v[0])
        if isinstance(v, str) and v:
            # `label` holds the display name, not the type — only use as last resort
            if k == "label":
                continue
            return v
    return "Unknown"


def node_label(node: dict) -> str:
    for k in ("label", "name", "display_name", "title", "text"):
        v = node.get(k)
        if isinstance(v, str) and v:
            return v
    props = node.get("properties") or {}
    if isinstance(props, dict):
        for k in ("name", "display_name", "title", "value", "text"):
            if props.get(k):
                return str(props[k])
    return ""


def node_props(node: dict) -> dict:
    p = node.get("properties")
    return p if isinstance(p, dict) else {}


# Some node types carry their human-readable mention in a property rather than the
# top-level `label` (e.g. LabResult.label is a UUID fallback; the analyte name is in
# properties.analyte). This maps a node to the best free-text mention for normalization.
_MENTION_PROPS = {
    "LabResult": ("analyte", "name", "value"),
    "Medication": ("name",),
    "Condition": ("name",),
    "Symptom": ("name",),
    "Provider": ("name",),
}


def clinical_mention(node: dict) -> str:
    t = node_type(node)
    props = node_props(node)
    for key in _MENTION_PROPS.get(t, ()):
        if props.get(key):
            return str(props[key])
    label = node_label(node)
    if label and not label.startswith(f"{t}_"):
        return label
    # label is a type-prefixed UUID fallback — dig for any human-ish property
    for key in ("analyte", "name", "value", "display_name", "title", "text"):
        if props.get(key):
            return str(props[key])
    return label


def mentions_by_type(nodes: List[dict]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for n in nodes:
        out.setdefault(node_type(n), []).append(clinical_mention(n))
    return out


def edge_endpoints(edge: dict) -> Tuple[str, str, str]:
    def pick(*keys):
        for k in keys:
            if edge.get(k):
                return str(edge[k])
        return ""
    source = pick("source", "from", "source_node", "start", "subject", "src")
    target = pick("target", "to", "target_node", "end", "object", "dst")
    label = pick("label", "relationship", "relation", "type", "predicate")
    return source, target, label


def count_by_type(nodes: List[dict]) -> Counter:
    c: Counter = Counter()
    for n in nodes:
        c[node_type(n)] += 1
    return c


def labels_by_type(nodes: List[dict]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {}
    for n in nodes:
        out.setdefault(node_type(n), []).append(node_label(n))
    return out


def incident_ids(edges: List[dict]) -> Set[str]:
    ids: Set[str] = set()
    for e in edges:
        s, t, _ = edge_endpoints(e)
        if s:
            ids.add(s)
        if t:
            ids.add(t)
    return ids


def orphan_clinical_nodes(nodes: List[dict], edges: List[dict]) -> List[dict]:
    """Clinical nodes with no incident edge (graph-integrity red flag)."""
    connected = incident_ids(edges)
    orphans = []
    for n in nodes:
        if node_type(n) in CLINICAL_NODE_TYPES and node_id(n) not in connected:
            orphans.append(n)
    return orphans
