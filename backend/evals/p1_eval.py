"""P1 eval gate — ingestion + normalization + graph integrity.

Two layers:
  * OFFLINE (no cloud): the normalization dictionary is complete over the hero
    GOLDEN sets (coverage == 1.0), entity resolution collapses variants, and the
    confirmed diagnosis resolves to SNOMED + Orphanet + OMIM.
  * LIVE (reads the active clinical brain from the registry): the dataset is
    healthy, extracted entity counts meet GOLDEN.min_counts, there are no orphan
    clinical nodes, live normalization coverage clears thresholds, and the
    confirmed-diagnosis node is present.

Metrics feed the JSON report; gating checks decide pass/fail.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from app.graph_utils import (
    count_by_type,
    labels_by_type,
    mentions_by_type,
    node_label,
    nodes_edges,
    orphan_clinical_nodes,
)
from app.normalize import Normalizer, coverage
from app.seed.odyssey_patient import GOLDEN
from app.seed.reference_data import REFERENCE_GOLDEN

# Live normalization-coverage thresholds (extraction labels are messy and
# non-deterministic; calibrated below what the seeded hero graph achieves — symptom
# ~0.92, medication 1.0, lab 1.0 — with margin for re-seed variability). Negative
# findings ("No chest pain") and vague mentions ("Left arm symptoms") are expected
# to stay unmatched, so symptom is not held to 1.0.
LIVE_COVERAGE_THRESH = {"symptom": 0.70, "medication": 0.85, "lab": 0.80}

_TYPE_FOR = {
    "conditions": "Condition", "medications": "Medication", "labs": "LabResult",
    "symptoms": "Symptom", "providers": "Provider", "encounters": "Encounter",
}


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    gating: bool = True


@dataclass
class EvalResult:
    phase: str
    checks: List[Check] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    live: bool = False

    @property
    def gating_failures(self) -> List[Check]:
        return [c for c in self.checks if c.gating and not c.passed]

    @property
    def passed(self) -> bool:
        return not self.gating_failures

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "live": self.live,
            "passed": self.passed,
            "metrics": self.metrics,
            "checks": [asdict(c) for c in self.checks],
        }


def _offline_checks(res: EvalResult) -> None:
    norm = Normalizer()
    for cat, golden_key in (("symptom", "symptoms_hpo"),
                            ("medication", "medications_rxnorm"),
                            ("lab", "labs_loinc")):
        mentions = list(GOLDEN[golden_key].keys())  # type: ignore[index]
        rep = coverage(mentions, cat, norm)
        res.metrics[f"golden_{cat}_coverage"] = round(rep.coverage, 3)
        res.checks.append(Check(
            f"golden {cat} dictionary coverage == 1.0",
            rep.coverage == 1.0,
            f"{rep.normalized}/{rep.total} unmatched={rep.unmatched}",
        ))

    dedupe_ok = norm.canonical("MTX", "medication") == norm.canonical("Methotrexate", "medication")
    res.checks.append(Check("entity resolution: MTX == Methotrexate", dedupe_ok))

    dx = norm.normalize("Takayasu arteritis", "condition")
    extra = norm.extra_codes("condition", dx.code) if dx else {}
    dx_ok = bool(dx) and extra.get("Orphanet") == "ORPHA:3287" and extra.get("OMIM") == "207600"
    res.checks.append(Check(
        "confirmed diagnosis -> SNOMED + Orphanet + OMIM",
        dx_ok, f"{dx.code if dx else None} extra={extra}",
    ))


async def _live_checks(res: EvalResult) -> None:
    from app import registry
    from app.cognee_client import get_client

    entry = registry.get_active("odyssey", "clinical")
    if not entry or not entry.get("id"):
        res.checks.append(Check(
            "live clinical brain available", False,
            "no active clinical brain in registry — run `python -m app.seed.ingest`",
            gating=False,
        ))
        return

    res.metrics["dataset_name"] = entry["name"]
    res.metrics["dataset_id"] = entry["id"]
    client = get_client()
    await client.connect()
    try:
        try:
            status = await client.datasets_status([entry["id"]])
            this = status.get(entry["id"]) if isinstance(status, dict) else status
            healthy = "ERROR" not in str(this).upper()
            res.metrics["dataset_status"] = str(this)
            res.checks.append(Check("dataset healthy (not ERRORED)", healthy, str(this)))
        except Exception as e:
            res.checks.append(Check("dataset status readable", False, repr(e), gating=False))

        graph = await client.dataset_graph(entry["id"])
    finally:
        await client.disconnect()

    res.live = True
    nodes, edges = nodes_edges(graph)
    counts = count_by_type(nodes)
    res.metrics["graph_nodes"] = len(nodes)
    res.metrics["graph_edges"] = len(edges)
    res.metrics["node_type_counts"] = dict(counts)

    for key, target in GOLDEN["min_counts"].items():  # type: ignore[index]
        got = counts.get(_TYPE_FOR.get(key, key), 0)
        res.checks.append(Check(f"min_counts {key} >= {target}", got >= target, f"got {got}"))

    orphans = orphan_clinical_nodes(nodes, edges)
    res.metrics["orphan_clinical_nodes"] = len(orphans)
    res.checks.append(Check(
        "no orphan clinical nodes", len(orphans) == 0,
        f"{len(orphans)} orphan(s): {[node_label(o) for o in orphans[:6]]}",
    ))

    labels = labels_by_type(nodes)
    mentions = mentions_by_type(nodes)
    norm = Normalizer()
    for cat, type_ in (("symptom", "Symptom"), ("medication", "Medication"), ("lab", "LabResult")):
        rep = coverage(mentions.get(type_, []), cat, norm)
        res.metrics[f"live_{cat}_coverage"] = round(rep.coverage, 3)
        res.metrics[f"live_{cat}_unmatched"] = rep.unmatched
        thr = LIVE_COVERAGE_THRESH[cat]
        res.checks.append(Check(
            f"live {cat} normalization coverage >= {thr}",
            rep.coverage >= thr,
            f"{rep.normalized}/{rep.total} ({rep.coverage:.0%}) unmatched={rep.unmatched[:6]}",
        ))

    cond_labels = [l.lower() for l in labels.get("Condition", [])]
    dx_present = any("takayasu" in l for l in cond_labels)
    res.checks.append(Check("confirmed diagnosis (Takayasu) node present", dx_present,
                            f"conditions sample={labels.get('Condition', [])[:6]}"))


async def _reference_checks(res: EvalResult) -> None:
    """Structural integrity of the two global reference brains (literature + trials).

    Deterministic graph-shape checks only; the LLM recall-quality assertions
    (does the literature surface Takayasu, does the trials brain reason eligibility)
    live in scripts/verify_reference.py so the gate stays reproducible.
    """
    from app import registry
    from app.cognee_client import get_client
    from app.seed.ingest_reference import GLOBAL_PATIENT

    lit = registry.get_active(GLOBAL_PATIENT, "literature")
    trials = registry.get_active(GLOBAL_PATIENT, "trials")
    if not lit or not lit.get("id") or not trials or not trials.get("id"):
        res.checks.append(Check(
            "reference brains available", False,
            "reference brains not in registry — run `python -m app.seed.ingest_reference`",
            gating=False,
        ))
        return

    res.metrics["reference_literature_dataset"] = lit["name"]
    res.metrics["reference_trials_dataset"] = trials["name"]

    client = get_client()
    await client.connect()
    try:
        for kind, entry in (("literature", lit), ("trials", trials)):
            try:
                status = await client.datasets_status([entry["id"]])
                this = status.get(entry["id"]) if isinstance(status, dict) else status
                healthy = "ERROR" not in str(this).upper()
                res.checks.append(Check(f"reference {kind} healthy (not ERRORED)", healthy, str(this)))
            except Exception as e:  # noqa: BLE001
                res.checks.append(Check(f"reference {kind} status readable", False, repr(e), gating=False))

        lit_counts = count_by_type(nodes_edges(await client.dataset_graph(lit["id"]))[0])
        trial_counts = count_by_type(nodes_edges(await client.dataset_graph(trials["id"]))[0])
    finally:
        await client.disconnect()

    res.metrics["reference_literature_counts"] = dict(lit_counts)
    res.metrics["reference_trials_counts"] = dict(trial_counts)

    lit_patterns = lit_counts.get("LiteraturePattern", 0)
    min_patterns = int(REFERENCE_GOLDEN["literature_min_patterns"])
    res.checks.append(Check(
        f"literature patterns >= {min_patterns}", lit_patterns >= min_patterns,
        f"LiteraturePattern={lit_patterns}"))

    trial_nodes = trial_counts.get("Trial", 0)
    min_trials = int(REFERENCE_GOLDEN["trials_min"])
    res.checks.append(Check(
        f"trials >= {min_trials}", trial_nodes >= min_trials, f"Trial={trial_nodes}"))

    crit_nodes = trial_counts.get("EligibilityCriterion", 0)
    res.checks.append(Check(
        "eligibility criteria extracted (>= trials)", crit_nodes >= min_trials,
        f"EligibilityCriterion={crit_nodes}"))


async def run_p1(offline: bool = False) -> EvalResult:
    res = EvalResult(phase="p1")
    _offline_checks(res)
    if not offline:
        await _live_checks(res)
        await _reference_checks(res)
    return res
