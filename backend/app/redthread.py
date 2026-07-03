"""Red-thread — the cited, fully **graph-backed** provenance path (plan §7.4, demo beat 3).

Every Connections/Trials finding must render as a multi-hop path over the graph where
**each hop is a real edge that exists in the graph** and the thread terminates at the
source document chunk that backs the claim (its `chunk_id` / document = the `data_id`).

Grounded live before design (no assumptions). The clinical brain's real topology is:

    ClinicalKnowledgeGraph --{symptoms|labs|conditions|medications|...}--> <ClinicalEntity>
    DocumentChunk          --contains--------------------------------------> ClinicalKnowledgeGraph
    DocumentChunk          --is_part_of------------------------------------> TextDocument   (doc_N)

So there is a genuine, walkable provenance chain from any clinical entity back to the
exact encounter document it was extracted from:

    <entity>  <--symptoms--  ClinicalKnowledgeGraph  <--contains--  DocumentChunk  --is_part_of-->  TextDocument
                                                                      (carries the verbatim `text` quote)

The literature brain mirrors it (`ReferenceLiteratureGraph --patterns--> LiteraturePattern`,
`DocumentChunk --contains--> ReferenceLiteratureGraph`, `DocumentChunk --is_part_of--> TextDocument`),
so a candidate condition's *literature* support is provenance-traceable the same way.

The "red-thread" for a candidate condition is therefore the bundle of these provenance
paths: the patient's discriminating phenotype findings (e.g. the large-vessel signs) each
traced to their source encounter note, plus the confirmed condition traced to its
diagnosis note, plus the literature pattern traced to its source abstract. Because every
hop is an actual graph edge, the gate assertion "red-thread path edges all exist in graph"
is literally checkable — `validate` re-confirms every hop against the edge set.

Honesty rails: a node whose provenance cannot be walked to a source document is **not**
fabricated a citation — it is reported as unresolved (`unresolved_anchors`) and excluded
from the thread bundle. Nothing here invents an edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from app.graph_utils import (
    clinical_mention,
    edge_endpoints,
    node_id,
    node_label,
    node_props,
    node_type,
    nodes_edges,
)
from app.normalize import Normalizer

# container node types that group extracted entities (one per ingested document)
_CONTAINER_TYPES = {"ClinicalKnowledgeGraph", "ReferenceLiteratureGraph", "ReferenceTrialsGraph"}
_CONTAINS = "contains"
_IS_PART_OF = "is_part_of"


# --------------------------------------------------------------------------- #
# graph indexing (pure)
# --------------------------------------------------------------------------- #
def edge_triples(edges: List[dict]) -> Set[Tuple[str, str, str]]:
    """The set of (source, target, label) triples — for O(1) edge-existence checks."""
    out: Set[Tuple[str, str, str]] = set()
    for e in edges:
        s, t, l = edge_endpoints(e)
        if s and t:
            out.add((s, t, l))
    return out


def _incoming(edges: List[dict]) -> Dict[str, List[Tuple[str, str]]]:
    """target_id -> [(label, source_id)]."""
    inc: Dict[str, List[Tuple[str, str]]] = {}
    for e in edges:
        s, t, l = edge_endpoints(e)
        if s and t:
            inc.setdefault(t, []).append((l, s))
    return inc


def _outgoing(edges: List[dict]) -> Dict[str, List[Tuple[str, str]]]:
    """source_id -> [(label, target_id)]."""
    out: Dict[str, List[Tuple[str, str]]] = {}
    for e in edges:
        s, t, l = edge_endpoints(e)
        if s and t:
            out.setdefault(s, []).append((l, t))
    return out


# --------------------------------------------------------------------------- #
# data model
# --------------------------------------------------------------------------- #
@dataclass
class ThreadHop:
    """One real, directed graph edge (source --relation--> target)."""

    source_id: str
    target_id: str
    relation: str
    source_type: str = ""
    target_type: str = ""
    source_label: str = ""
    target_label: str = ""

    @property
    def triple(self) -> Tuple[str, str, str]:
        return (self.source_id, self.target_id, self.relation)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id, "target_id": self.target_id,
            "relation": self.relation,
            "source_type": self.source_type, "target_type": self.target_type,
            "source_label": self.source_label, "target_label": self.target_label,
        }


@dataclass
class RedThread:
    """An anchor clinical/literature node traced over real edges to its source document."""

    anchor_id: str
    anchor_type: str
    anchor_label: str
    hops: List[ThreadHop] = field(default_factory=list)
    chunk_id: Optional[str] = None
    document_id: Optional[str] = None
    document_label: Optional[str] = None
    quote: Optional[str] = None

    @property
    def resolved(self) -> bool:
        """Traced all the way to a source document chunk (a real citation)."""
        return bool(self.hops and self.document_id and self.chunk_id)

    def to_dict(self) -> dict:
        return {
            "anchor": {"id": self.anchor_id, "type": self.anchor_type, "label": self.anchor_label},
            "resolved": self.resolved,
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "document_label": self.document_label,
            "quote": (self.quote or "")[:160],
            "hops": [h.to_dict() for h in self.hops],
        }


@dataclass
class RedThreadBundle:
    condition: str
    patient_threads: List[RedThread] = field(default_factory=list)
    literature_threads: List[RedThread] = field(default_factory=list)
    unresolved_anchors: List[str] = field(default_factory=list)
    clinical_dataset: Optional[str] = None
    literature_dataset: Optional[str] = None
    all_edges_exist: bool = True

    @property
    def threads(self) -> List[RedThread]:
        return [t for t in (self.patient_threads + self.literature_threads) if t.resolved]

    def to_dict(self) -> dict:
        return {
            "condition": self.condition,
            "clinical_dataset": self.clinical_dataset,
            "literature_dataset": self.literature_dataset,
            "all_edges_exist": self.all_edges_exist,
            "n_patient_threads": len([t for t in self.patient_threads if t.resolved]),
            "n_literature_threads": len([t for t in self.literature_threads if t.resolved]),
            "unresolved_anchors": self.unresolved_anchors,
            "patient_threads": [t.to_dict() for t in self.patient_threads],
            "literature_threads": [t.to_dict() for t in self.literature_threads],
        }


# --------------------------------------------------------------------------- #
# provenance walk (pure) — the heart of the red-thread
# --------------------------------------------------------------------------- #
def trace_provenance(anchor_id: str, nodes: List[dict], edges: List[dict]) -> RedThread:
    """Walk an entity/pattern node back to its source document over **real edges**.

        <container> --<rel>--> anchor              (container groups the entity)
        <chunk>     --contains--> <container>       (the chunk that produced it)
        <chunk>     --is_part_of--> <document>      (the source document)

    Every hop stored is an edge that exists in `edges`. If any link is missing the thread
    is returned partially resolved (never fabricated).
    """
    by_id = {node_id(n): n for n in nodes}
    inc = _incoming(edges)
    out = _outgoing(edges)
    anchor = by_id.get(anchor_id, {})
    thread = RedThread(
        anchor_id=anchor_id,
        anchor_type=node_type(anchor),
        anchor_label=clinical_mention(anchor) or node_label(anchor),
    )

    # 1) container --rel--> anchor  (find the grouping container that points to the anchor)
    container_id = None
    container_rel = None
    for rel, src in inc.get(anchor_id, []):
        if node_type(by_id.get(src, {})) in _CONTAINER_TYPES:
            container_id, container_rel = src, rel
            break
    if not container_id:
        return thread
    thread.hops.append(_hop(container_id, anchor_id, container_rel, by_id))

    # 2) chunk --contains--> container
    chunk_id = None
    for rel, src in inc.get(container_id, []):
        if rel == _CONTAINS and node_type(by_id.get(src, {})) == "DocumentChunk":
            chunk_id = src
            break
    if not chunk_id:
        return thread
    thread.hops.append(_hop(chunk_id, container_id, _CONTAINS, by_id))
    thread.chunk_id = chunk_id
    thread.quote = str(node_props(by_id.get(chunk_id, {})).get("text", "")) or None

    # 3) chunk --is_part_of--> document
    for rel, tgt in out.get(chunk_id, []):
        if rel == _IS_PART_OF and node_type(by_id.get(tgt, {})) == "TextDocument":
            thread.hops.append(_hop(chunk_id, tgt, _IS_PART_OF, by_id))
            thread.document_id = tgt
            thread.document_label = node_label(by_id.get(tgt, {})) or None
            break
    return thread


def _hop(src: str, tgt: str, rel: str, by_id: Dict[str, dict]) -> ThreadHop:
    s, t = by_id.get(src, {}), by_id.get(tgt, {})
    return ThreadHop(
        source_id=src, target_id=tgt, relation=rel,
        source_type=node_type(s), target_type=node_type(t),
        source_label=(clinical_mention(s) or node_label(s)),
        target_label=(clinical_mention(t) or node_label(t)),
    )


def validate(bundle: RedThreadBundle, edges: List[dict]) -> bool:
    """Confirm **every hop of every resolved thread is a real edge** in the graph.

    Use when all threads share one edge set (e.g. an offline single-graph fixture). For
    the live two-graph case use `validate_over` per graph (see `run_redthread`).
    """
    ok = (validate_over(bundle.patient_threads, edges)
          and validate_over(bundle.literature_threads, edges))
    bundle.all_edges_exist = ok
    return ok


# --------------------------------------------------------------------------- #
# anchor selection (pure)
# --------------------------------------------------------------------------- #
def find_phenotype_anchors(
    nodes: List[dict], hpo_terms: Set[str], normalizer: Normalizer
) -> List[dict]:
    """Clinical entity nodes whose mention normalizes to one of the target HPO terms
    (e.g. the large-vessel discriminators). Deterministic; one node per distinct HPO."""
    seen: Set[str] = set()
    anchors: List[dict] = []
    for n in nodes:
        if node_type(n) not in ("Symptom", "LabResult", "Condition"):
            continue
        code = normalizer.normalize(clinical_mention(n), "symptom")
        if code and code.code in hpo_terms and code.code not in seen:
            seen.add(code.code)
            anchors.append(n)
    return anchors


def find_condition_anchor(nodes: List[dict], condition_display: str) -> Optional[dict]:
    """The confirmed Condition node whose label matches the target condition."""
    want = condition_display.lower()
    best = None
    for n in nodes:
        if node_type(n) != "Condition":
            continue
        label = (clinical_mention(n) or node_label(n)).lower()
        status = str(node_props(n).get("status", "")).lower()
        if want in label or label in want:
            if "confirm" in status:
                return n
            best = best or n
    return best


def find_pattern_anchor(lit_nodes: List[dict], condition: str) -> Optional[dict]:
    want = condition.lower()
    for n in lit_nodes:
        if node_type(n) != "LiteraturePattern":
            continue
        if str(node_props(n).get("condition", "")).lower() == want:
            return n
    return None


# --------------------------------------------------------------------------- #
# live orchestration
# --------------------------------------------------------------------------- #
async def run_redthread(
    client, patient_id: str = "odyssey", condition: str = "Takayasu arteritis"
) -> RedThreadBundle:
    """Build + validate the graph-backed red-thread for a candidate condition, live."""
    from app import registry
    from app.agents.connections import VASCULAR_HPO

    clinical = registry.get_active(patient_id, "clinical")
    if not clinical or not clinical.get("id"):
        raise RuntimeError("no active clinical brain — seed the hero patient first")
    lit = registry.get_active("global", "literature")

    norm = Normalizer()
    cg = await client.dataset_graph(clinical["id"])
    cnodes, cedges = nodes_edges(cg)

    bundle = RedThreadBundle(
        condition=condition,
        clinical_dataset=clinical.get("name"),
        literature_dataset=lit.get("name") if lit else None,
    )

    # patient side: the large-vessel discriminators + the confirmed condition
    anchors = find_phenotype_anchors(cnodes, VASCULAR_HPO, norm)
    cond = find_condition_anchor(cnodes, condition)
    if cond:
        anchors.append(cond)
    for a in anchors:
        thread = trace_provenance(node_id(a), cnodes, cedges)
        if thread.resolved:
            bundle.patient_threads.append(thread)
        else:
            bundle.unresolved_anchors.append(thread.anchor_label or node_id(a))

    # literature side: the pattern for the candidate condition
    lit_edges: List[dict] = []
    if lit and lit.get("id"):
        lg = await client.dataset_graph(lit["id"])
        lnodes, lit_edges = nodes_edges(lg)
        pat = find_pattern_anchor(lnodes, condition)
        if pat:
            t = trace_provenance(node_id(pat), lnodes, lit_edges)
            if t.resolved:
                bundle.literature_threads.append(t)
            else:
                bundle.unresolved_anchors.append(t.anchor_label or node_id(pat))

    # the gate: every hop of every resolved thread must be a real edge in its graph
    ok_clin = validate_over(bundle.patient_threads, cedges)
    ok_lit = validate_over(bundle.literature_threads, lit_edges)
    bundle.all_edges_exist = ok_clin and ok_lit
    return bundle


def validate_over(threads: List[RedThread], edges: List[dict]) -> bool:
    triples = edge_triples(edges)
    ok = True
    for thread in threads:
        if not thread.resolved:
            continue
        for hop in thread.hops:
            if hop.triple not in triples:
                ok = False
    return ok
