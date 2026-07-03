"""Unit tests for the eval harness: pure scoring metrics, case loading, and the
deterministic offline swarm scores (no cloud)."""

from __future__ import annotations

import json

from evals import metrics as M
from evals.swarm_eval import CASES_PATH, load_cases, run_swarm, _offline_scores
from evals.p1_eval import EvalResult
from app.seed.odyssey_patient import GOLDEN
from app.seed.reference_data import REFERENCE_GOLDEN


# --- pure metrics ------------------------------------------------------------

def test_set_precision_recall_and_f1():
    assert M.set_precision(["a", "b"], ["a", "b", "c"]) == 1.0
    assert M.set_recall(["a", "b"], ["a", "b", "c"]) == round(2 / 3, 4)
    assert M.set_precision([], ["a"]) == 1.0          # nothing wrong surfaced
    assert M.set_recall(["a"], []) == 1.0             # nothing required
    assert M.f1(1.0, round(2 / 3, 4)) == round(2 * 1.0 * (2 / 3) / (1.0 + 2 / 3), 4)
    assert M.f1(0.0, 0.0) == 0.0


def test_precision_at_k():
    ranked = ["Takayasu arteritis", "Giant cell arteritis", "Lymphoma"]
    assert M.precision_at_k(ranked, {"Takayasu arteritis"}, 1) == 1.0
    assert M.precision_at_k(ranked, {"Lymphoma"}, 1) == 0.0
    assert M.precision_at_k(ranked, {"Takayasu arteritis", "Lymphoma"}, 2) == 0.5
    assert M.precision_at_k([], {"x"}, 1) == 0.0      # empty ranking
    assert M.precision_at_k(ranked, {"x"}, 0) == 0.0  # k <= 0


def test_temporal_ordering_accuracy():
    assert M.temporal_ordering_accuracy(["2021-01-01", "2022-01-01", "2023-01-01"]) == 1.0
    assert M.temporal_ordering_accuracy(["2023-01-01", "2021-01-01"]) == 0.0
    assert M.temporal_ordering_accuracy(["2021-01-01", "2020-01-01", "2022-01-01"]) == 0.5
    assert M.temporal_ordering_accuracy(["2021-01-01"]) == 1.0   # trivially ordered
    assert M.temporal_ordering_accuracy([]) == 1.0


class _Item:
    def __init__(self, evidence):
        self.evidence = evidence


def test_citation_coverage():
    assert M.citation_coverage([_Item(["ref"]), _Item(["ref"])]) == 1.0
    assert M.citation_coverage([_Item(["ref"]), _Item([])]) == 0.5
    assert M.citation_coverage([]) == 1.0  # nothing uncited was surfaced


def test_lint_violation_count():
    def finder(text):
        return ["hit"] if "diagnosis is" in text else []
    texts = ["consider investigating", "the diagnosis is lupus", ""]
    assert M.lint_violation_count(texts, finder) == 1


def test_coverage_fraction():
    assert M.coverage_fraction(4, 4) == 1.0
    assert M.coverage_fraction(2, 4) == 0.5
    assert M.coverage_fraction(5, 4) == 1.0   # capped
    assert M.coverage_fraction(1, 0) == 1.0   # nothing expected


# --- case file ---------------------------------------------------------------

def test_cases_file_parses_and_is_indexed():
    doc = load_cases()
    assert doc["cases"], "cases list must be non-empty"
    agents = set(doc["by_agent"])
    assert {"timeline", "connections", "trials", "safety",
            "briefing", "justify", "swarm"}.issubset(agents)
    # every metric declares a numeric threshold
    for case in doc["cases"]:
        for name, cfg in case["metrics"].items():
            assert isinstance(cfg["threshold"], (int, float)), (case["id"], name)
            assert cfg.get("compare", "ge") in ("ge", "le")


def test_cases_gold_matches_authoritative_constants():
    """The labeled gold in swarm.json must not drift from the seed constants."""
    doc = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    by = {c["agent"]: c for c in doc["cases"]}
    assert by["connections"]["gold"]["top_condition"] == \
        REFERENCE_GOLDEN["literature_top_condition"]
    assert [str(x) for x in by["trials"]["gold"]["should_match"]] == \
        [str(x) for x in REFERENCE_GOLDEN["trials_should_match"]]
    assert by["timeline"]["gold"]["dx_date"] == \
        str(GOLDEN.get("true_diagnosis_date", "2024-03-01"))


# --- offline deterministic scores --------------------------------------------

def test_offline_scores_are_perfect_on_fixtures():
    s = _offline_scores()
    assert s["timeline"]["temporal_ordering_accuracy"] == 1.0
    assert s["timeline"]["dx_milestone_recall"] == 1.0
    assert s["connections"]["precision_at_1"] == 1.0
    assert s["trials"]["eligibility_precision"] == 1.0
    assert s["trials"]["eligibility_recall"] == 1.0
    assert s["safety"]["signal_detection_recall"] == 1.0
    assert s["briefing"]["dx_milestone_recall"] == 1.0
    assert s["justify"]["grounding_correctness"] == 1.0


async def test_run_swarm_offline_passes():
    res: EvalResult = await run_swarm(offline=True)
    assert res.phase == "swarm"
    assert res.live is False
    # offline path only scores the deterministic (non live_only) metrics + meta checks
    assert res.checks, "should have produced checks"
    assert res.passed, f"offline swarm gate failed: {res.gating_failures}"
    # a live_only metric must not be scored offline
    assert "connections.citation_coverage" not in res.metrics
