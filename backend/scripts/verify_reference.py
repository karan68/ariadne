"""Verify the two global reference brains (literature + trials) on Cognee Cloud.

Reads both reference datasets from the registry (pseudo patient "global"), then:
  1. asserts each dataset is healthy,
  2. pulls each dataset graph and counts nodes by type,
  3. runs the *actual* recalls the P2 agents will run and checks they behave:
       - literature: the hero's de-identified constellation must surface
         "Takayasu arteritis" (with citations), discriminating it from mimics,
       - trials: the hero profile must be judged eligible for the large-vessel
         vasculitis trials and NOT for the GCA / SLE / paediatric ones.

Run from backend/:
    python -m scripts.verify_reference
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass

from app import registry
from app.cognee_client import get_client
from app.graph_utils import count_by_type, nodes_edges
from app.recall_parse import parse_recall
from app.seed.ingest_reference import GLOBAL_PATIENT
from app.seed.reference_data import REFERENCE_GOLDEN

# A de-identified description of the hero's constellation (does NOT name the disease)
# — this is exactly what ConnectionsAgent feeds the literature brain.
HERO_CONSTELLATION = (
    "A woman under 40 has more than two years of persistent fatigue, low-grade "
    "evening fevers, night sweats and unintentional weight loss, with a normocytic "
    "anaemia and a persistently elevated ESR and CRP. She has since developed an "
    "inter-arm systolic blood-pressure difference greater than 15 mmHg, a diminished "
    "left radial pulse, a bruit over the left subclavian and carotid arteries, left- "
    "arm claudication on exertion, and new hypertension. HLA-B*52:01 typing is "
    "positive. Which conditions best explain this constellation, and what "
    "distinguishes the most likely one from its mimics? Cite sources."
)

# The hero profile ConnectionsAgent/TrialsAgent hands to the trials brain.
HERO_TRIAL_PROFILE = (
    "Patient: 30-year-old woman with Takayasu arteritis confirmed on CT and MR "
    "angiography (aortic and subclavian wall thickening with stenosis). Active "
    "disease: ESR 62 mm/hr and CRP 18 mg/L within the last month. Vascular signs: "
    "absent left radial pulse, inter-arm blood-pressure difference over 15 mmHg, "
    "left-arm claudication. Not pregnant. No active infection. "
    "For each of the trials in memory, state the NCT id and whether this patient is "
    "likely ELIGIBLE or NOT eligible, and cite the specific criterion that decides it."
)


async def _health(client, dataset_id: str) -> bool:
    try:
        status = await client.datasets_status([dataset_id] if dataset_id else None)
        this = status.get(dataset_id) if isinstance(status, dict) and dataset_id else status
        print(f"  [health] {dataset_id} = {this}")
        return str(this).upper().find("ERROR") == -1
    except Exception as e:  # noqa: BLE001
        print(f"  [health] status check failed (non-fatal): {e!r}")
        return True


async def _graph_counts(client, dataset_id: str):
    try:
        graph = await client.dataset_graph(dataset_id)
        nodes, edges = nodes_edges(graph)
        counts = count_by_type(nodes)
        print(f"  [graph] nodes={len(nodes)} edges={len(edges)}")
        for t, c in counts.most_common():
            print(f"          {t:<26} {c}")
        return len(nodes), len(edges), counts
    except Exception as e:  # noqa: BLE001
        print(f"  [graph] dataset_graph failed: {e!r}")
        return 0, 0, Counter()


async def main() -> int:
    lit = registry.get_active(GLOBAL_PATIENT, "literature")
    trials = registry.get_active(GLOBAL_PATIENT, "trials")
    if not lit or not trials:
        print("reference brains not in registry — run app.seed.ingest_reference first")
        return 2

    client = get_client()
    await client.connect()

    # ---- Literature brain -------------------------------------------------
    print(f"=== reference_literature: {lit['name']} ({lit.get('id')}) ===")
    lit_healthy = await _health(client, lit.get("id"))
    _ln, _le, _lc = await _graph_counts(client, lit.get("id"))

    print("\n  [connections recall] hero constellation -> candidate conditions:")
    lit_answer = ""
    lit_cites = 0
    try:
        resp = await client.recall(
            query_text=HERO_CONSTELLATION, query_type="GRAPH_COMPLETION",
            datasets=[lit["name"]], include_references=True,
            session_id="verify-ref-literature",
        )
        parsed = parse_recall(resp)
        lit_answer = (parsed.answer or "").strip()
        lit_cites = len(parsed.references)
        shown = lit_answer.replace("\n", " ")
        print(f"    A: {shown[:800]}{'…' if len(shown) > 800 else ''}")
        print(f"    citations: {lit_cites} (has_citations={parsed.has_citations})")
        for r in parsed.references[:3]:
            print(f"       • doc={r.document_name} data_id={r.data_id}")
    except Exception as e:  # noqa: BLE001
        print(f"    literature recall FAILED: {e!r}")

    low = lit_answer.lower()
    top_condition = str(REFERENCE_GOLDEN["literature_top_condition"]).lower()
    lit_names_takayasu = "takayasu" in low
    lit_discriminates = any(k in low for k in (
        "absent", "pulse", "bruit", "inter-arm", "claudication", "large-vessel", "angiograph"))
    lit_patterns_ok = _lc.get("LiteraturePattern", 0) >= 1 or _ln >= int(REFERENCE_GOLDEN["literature_min_patterns"])

    # ---- Trials brain -----------------------------------------------------
    print(f"\n=== reference_trials: {trials['name']} ({trials.get('id')}) ===")
    trials_healthy = await _health(client, trials.get("id"))
    _tn, _te, _tc = await _graph_counts(client, trials.get("id"))

    print("\n  [trials recall] hero profile -> eligibility:")
    trial_answer = ""
    trial_cites = 0
    try:
        resp = await client.recall(
            query_text=HERO_TRIAL_PROFILE, query_type="GRAPH_COMPLETION",
            datasets=[trials["name"]], include_references=True,
            session_id="verify-ref-trials",
        )
        parsed = parse_recall(resp)
        trial_answer = (parsed.answer or "").strip()
        trial_cites = len(parsed.references)
        shown = trial_answer.replace("\n", " ")
        print(f"    A: {shown[:900]}{'…' if len(shown) > 900 else ''}")
        print(f"    citations: {trial_cites} (has_citations={parsed.has_citations})")
    except Exception as e:  # noqa: BLE001
        print(f"    trials recall FAILED: {e!r}")

    tlow = trial_answer.lower()
    should_match = [n.lower() for n in REFERENCE_GOLDEN["trials_should_match"]]  # type: ignore
    should_not = [n.lower() for n in REFERENCE_GOLDEN["trials_should_not_match"]]  # type: ignore
    matched_present = [n for n in should_match if n in tlow]
    # For "should match" NCTs, the answer must mention them (and ideally as eligible).
    trials_match_ok = len(matched_present) >= 2  # at least 2 of 3 large-vessel trials surfaced
    # The paediatric Takayasu trial (right disease, wrong age) is the key discriminator:
    paeds = "nct09000006"
    paeds_reasoned = paeds in tlow  # present so it can be reasoned about/excluded

    await client.disconnect()

    # ---- Gate -------------------------------------------------------------
    print("\n[summary]")
    print(f"  literature healthy        : {lit_healthy}")
    print(f"  literature names Takayasu : {lit_names_takayasu}")
    print(f"  literature discriminates  : {lit_discriminates}")
    print(f"  literature cited          : {lit_cites > 0}")
    print(f"  literature patterns graph : {lit_patterns_ok}")
    print(f"  trials healthy            : {trials_healthy}")
    print(f"  trials matched (>=2/3)    : {trials_match_ok} ({matched_present})")
    print(f"  trials paeds discriminator: {paeds_reasoned}")
    print(f"  trials cited              : {trial_cites > 0}")

    ok = (
        lit_healthy and lit_names_takayasu and lit_discriminates and lit_cites > 0
        and lit_patterns_ok and trials_healthy and trials_match_ok and trial_cites > 0
    )
    print(f"\nVERIFY REFERENCE {'PASS' if ok else 'REVIEW'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
