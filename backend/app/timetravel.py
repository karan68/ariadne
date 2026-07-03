"""Time-travel — as-of-date memory reconstruction + the diagnostic-lead counterfactual.

This is Ariadne's signature "wow": *when would the connected memory have surfaced the
pattern?* It reconstructs what the record already supported at each past date and
re-runs the **same** deterministic ConnectionsAgent ranking, showing how many months
before the real diagnosis a large-vessel vasculitis flag was already justified.

Two grounded capabilities:

1. **as-of subgraph** (`as_of_subgraph`) — filter the *live* clinical graph to the
   nodes whose event date is on/before a cutoff. Encounter / Condition / LabResult /
   Medication / ImagingStudy / Procedure nodes carry reliable ISO dates (verified live);
   Symptom nodes do **not** (their `onset` is free-text and they carry no edge to a
   dated Encounter), so the phenotype timeline below dates symptoms by the note that
   documents them instead. This is the literal "filter nodes by event date" from the
   spec and it never includes a future-dated node.

2. **the counterfactual** (`build_trace` / `run_time_travel`) — each hero encounter is
   one ingested memory document with a date. For each cutoff we accumulate the phenotype
   knowable by then (scanned from the actual note texts with the same normalizer
   dictionary the agents use — never inventing a feature or a date), re-run the identical
   phenotype-overlap ranking against the curated literature brain, and find the earliest
   date the record already justified a flag.

Honesty rails (staff-engineer discipline — nothing hand-tuned to a target):
  * The phenotype-as-of-date is *scanned from the literal note text*; this module never
    invents a symptom or a date. The "months earlier" figure emerges from the ranking.
  * The headline flag requires a genuine **vascular discriminator** (claudication,
    absent pulse, bruit, inter-arm BP difference, cold extremities, renovascular
    hypertension) — not constitutional overlap alone — mirroring the literature red-flag
    ("young woman + >12 months of raised inflammatory markers + a new vascular sign →
    image the aorta"). The weaker constitutional lead is reported separately, labelled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from app.agents.connections import (
    VASCULAR_HPO,
    build_candidate_index,
    rank_candidates,
)
from app.graph_utils import node_props, node_type, nodes_edges
from app.normalize import Normalizer, hpo_display_map
from app.seed.odyssey_patient import ENCOUNTERS, HERO_PATIENT

#: the condition whose earlier flag is the story
TARGET_CONDITION = "Takayasu arteritis"

#: node types that carry a reliable ISO event date (verified live on the hero brain)
_DATED_TYPES = {
    "Encounter": "date",
    "Condition": "date",
    "LabResult": "date",
    "Medication": "start",
    "ImagingStudy": "date",
    "Procedure": "date",
}


# --------------------------------------------------------------------------- #
# date helpers (pure)
# --------------------------------------------------------------------------- #
def _iso(value: object) -> Optional[str]:
    if isinstance(value, str) and len(value) >= 10 and value[4] == "-" and value[7] == "-":
        head = value[:10]
        try:
            y, m, d = int(head[:4]), int(head[5:7]), int(head[8:10])
        except ValueError:
            return None
        if 1 <= m <= 12 and 1 <= d <= 31:
            return head
    return None


def node_event_date(node: dict) -> Optional[str]:
    """The ISO event date of a graph node, or None (Symptom/infra nodes → None)."""
    field_name = _DATED_TYPES.get(node_type(node))
    if not field_name:
        return None
    return _iso(node_props(node).get(field_name))


def months_between(earlier: str, later: str) -> int:
    """Completed calendar months between two ISO dates (earlier ≤ later).

    2022-08-05 → 2024-03-01 = 18 (not yet at the 19-month mark on the 1st).
    """
    ey, em, ed = int(earlier[:4]), int(earlier[5:7]), int(earlier[8:10])
    ly, lm, ld = int(later[:4]), int(later[5:7]), int(later[8:10])
    months = (ly - ey) * 12 + (lm - em)
    if ld < ed:
        months -= 1
    return max(0, months)


# --------------------------------------------------------------------------- #
# as-of subgraph (graph-grounded)
# --------------------------------------------------------------------------- #
def as_of_subgraph(nodes: List[dict], as_of: str) -> Tuple[List[dict], List[dict]]:
    """Split dated clinical nodes into (kept ≤ as_of, excluded > as_of).

    Undated nodes (Symptom, infrastructure) are ignored here — they are not part of the
    date-filtered clinical axis. Guarantees no kept node is future-dated.
    """
    kept: List[dict] = []
    excluded: List[dict] = []
    for n in nodes:
        d = node_event_date(n)
        if d is None:
            continue
        (kept if d <= as_of else excluded).append(n)
    return kept, excluded


# --------------------------------------------------------------------------- #
# counterfactual (phenotype-over-time → ranking)
# --------------------------------------------------------------------------- #
@dataclass
class AsOfRanking:
    date: str
    new_features: List[str]          # human-readable features introduced at this date
    phenotype_hpo: List[str]         # accumulated HPO ids ≤ this date
    vascular_hpo: List[str]          # subset that are vascular discriminators
    top_condition: Optional[str]
    top_score: int
    runner_up: Optional[str]
    runner_up_score: int
    ranking: List[dict] = field(default_factory=list)

    @property
    def top_is_target(self) -> bool:
        return self.top_condition == TARGET_CONDITION

    @property
    def top_is_clear(self) -> bool:
        """Target ranks #1 and strictly ahead of the runner-up."""
        return self.top_is_target and self.top_score > self.runner_up_score

    @property
    def has_vascular(self) -> bool:
        return bool(self.vascular_hpo)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "new_features": self.new_features,
            "phenotype_count": len(self.phenotype_hpo),
            "vascular_features": self.vascular_hpo,
            "top_condition": self.top_condition,
            "top_score": self.top_score,
            "top_is_clear": self.top_is_clear,
            "has_vascular": self.has_vascular,
            "ranking": self.ranking,
        }


def build_trace(
    encounters: List[dict],
    index: Dict[str, dict],
    normalizer: Normalizer,
    hpo_display: Optional[Dict[str, str]] = None,
) -> List[AsOfRanking]:
    """Accumulate the phenotype over dated encounters and rank at each step.

    Pure + deterministic given the encounters and the candidate index (which is built
    from the literature brain). Scans each note's text for symptom mentions — the same
    normalizer dictionary the ConnectionsAgent uses — so a feature is dated by the note
    that documents it, never invented.
    """
    disp = hpo_display or hpo_display_map()
    acc: List[str] = []
    trace: List[AsOfRanking] = []
    # encounters in date order (defensive: sort by date)
    for enc in sorted(encounters, key=lambda e: e["date"]):
        codes = normalizer.scan(enc.get("text", ""), "symptom")
        new_disp: List[str] = []
        for c in codes:
            if c.code not in acc:
                acc.append(c.code)
                new_disp.append(disp.get(c.code, c.display))
        ranked = rank_candidates(set(acc), index, disp)
        top = ranked[0] if ranked else None
        second = ranked[1] if len(ranked) > 1 else None
        vasc = sorted(set(acc) & VASCULAR_HPO)
        trace.append(AsOfRanking(
            date=enc["date"],
            new_features=new_disp,
            phenotype_hpo=list(acc),
            vascular_hpo=[disp.get(h, h) for h in vasc],
            top_condition=top.condition if top else None,
            top_score=top.score if top else 0,
            runner_up=second.condition if second else None,
            runner_up_score=second.score if second else 0,
            ranking=[c.to_dict() for c in ranked[:5]],
        ))
    return trace


def constitutional_lead_date(trace: List[AsOfRanking]) -> Optional[str]:
    """Earliest date the target becomes the single leading explanation (any features)."""
    for step in trace:
        if step.top_is_clear:
            return step.date
    return None


def first_vascular_flag_date(trace: List[AsOfRanking]) -> Optional[str]:
    """Earliest date the target leads AND a genuine vascular discriminator is present.

    This is the honest headline flag: chronic inflammation plus a new large-vessel sign
    is what should trigger "consider large-vessel vasculitis / image the aorta".
    """
    for step in trace:
        if step.top_is_clear and step.has_vascular:
            return step.date
    return None


# --------------------------------------------------------------------------- #
# result container
# --------------------------------------------------------------------------- #
@dataclass
class TimeTravelResult:
    patient_id: str
    true_diagnosis: str
    true_diagnosis_date: str
    constitutional_lead_date: Optional[str]
    first_flag_date: Optional[str]
    months_earlier: int
    trace: List[AsOfRanking] = field(default_factory=list)
    candidates: List[str] = field(default_factory=list)
    literature_dataset: Optional[str] = None
    clinical_dataset: Optional[str] = None

    @property
    def flag_step(self) -> Optional[AsOfRanking]:
        if not self.first_flag_date:
            return None
        return next((s for s in self.trace if s.date == self.first_flag_date), None)

    def to_dict(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "true_diagnosis": self.true_diagnosis,
            "true_diagnosis_date": self.true_diagnosis_date,
            "constitutional_lead_date": self.constitutional_lead_date,
            "first_flag_date": self.first_flag_date,
            "months_earlier": self.months_earlier,
            "candidates": self.candidates,
            "literature_dataset": self.literature_dataset,
            "clinical_dataset": self.clinical_dataset,
            "trace": [s.to_dict() for s in self.trace],
        }


def summarize(
    encounters: List[dict],
    index: Dict[str, dict],
    normalizer: Optional[Normalizer] = None,
    *,
    patient_id: str = "odyssey",
    true_diagnosis: str = TARGET_CONDITION,
    true_diagnosis_date: Optional[str] = None,
) -> TimeTravelResult:
    """Pure end-to-end summary given the encounters + a candidate index."""
    norm = normalizer or Normalizer()
    dx_date = true_diagnosis_date or str(HERO_PATIENT["true_diagnosis_date"])
    trace = build_trace(encounters, index, norm)
    const = constitutional_lead_date(trace)
    flag = first_vascular_flag_date(trace)
    months = months_between(flag, dx_date) if flag else 0
    return TimeTravelResult(
        patient_id=patient_id,
        true_diagnosis=true_diagnosis,
        true_diagnosis_date=dx_date,
        constitutional_lead_date=const,
        first_flag_date=flag,
        months_earlier=months,
        trace=trace,
        candidates=sorted(index.keys()),
    )


# --------------------------------------------------------------------------- #
# live orchestration
# --------------------------------------------------------------------------- #
async def run_time_travel(client, patient_id: str = "odyssey") -> TimeTravelResult:
    """Live: build the candidate index from the literature brain, then compute the
    counterfactual over the hero's dated encounters. Also records the clinical dataset
    so callers can reconstruct the growing as-of subgraph for the frontend."""
    from app import registry

    lit = registry.get_active("global", "literature")
    if not lit or not lit.get("id"):
        raise RuntimeError(
            "no active reference_literature brain — run "
            "`python -m app.seed.ingest_reference --only literature`")
    clinical = registry.get_active(patient_id, "clinical")

    norm = Normalizer()
    lit_graph = await client.dataset_graph(lit["id"])
    lnodes, _ = nodes_edges(lit_graph)
    index = build_candidate_index(lnodes, norm)

    result = summarize(ENCOUNTERS, index, norm, patient_id=patient_id)
    result.literature_dataset = lit.get("name")
    result.clinical_dataset = clinical.get("name") if clinical else None
    return result


async def as_of_counts(client, dataset_id: str, dates: List[str]) -> List[Tuple[str, int, int]]:
    """For the frontend slider: (date, kept_node_count, excluded_node_count) at each
    cutoff over the live clinical graph — the graph visibly grows over time."""
    graph = await client.dataset_graph(dataset_id)
    nodes, _ = nodes_edges(graph)
    out: List[Tuple[str, int, int]] = []
    for d in dates:
        kept, excluded = as_of_subgraph(nodes, d)
        out.append((d, len(kept), len(excluded)))
    return out
