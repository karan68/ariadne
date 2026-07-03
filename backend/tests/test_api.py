"""P5 API contract tests — FastAPI TestClient over the snapshot-backed API.

These are deterministic (no live cloud): they assert every front-door endpoint's
shape + the load-bearing contracts the demo depends on — the RBAC family->[] /
provider->dataset boundary, the improve() reweight (red herring demoted, top
unchanged), and the signature-feature anchors (time-travel 18-months, red-thread
edges-all-exist). If the demo snapshot has not been built, the whole module skips
cleanly (the snapshot is a live-captured artifact, not fabricated in tests).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import demo_store
from app.main import app

pytestmark = pytest.mark.skipif(
    demo_store.load_snapshot() is None,
    reason="demo snapshot not built (run `python -m scripts.build_snapshot`)",
)

client = TestClient(app)


# --------------------------------------------------------------------------- #
# health / config / snapshot envelope
# --------------------------------------------------------------------------- #
def test_health_reports_snapshot_present():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "ariadne"
    assert body["snapshot"] is True
    assert body["snapshot_generated_at"]


def test_config_shape():
    r = client.get("/config")
    assert r.status_code == 200
    body = r.json()
    for key in ("cognee_mode", "llm_model", "embedding_model", "min_confidence"):
        assert key in body


def test_snapshot_has_all_sections():
    r = client.get("/api/snapshot")
    assert r.status_code == 200
    snap = r.json()
    for key in (
        "generated_at", "patient_id", "condition", "datasets", "hero",
        "agents", "timetravel", "redthread", "sessions", "graph",
        "rbac", "improve_demo", "forget_demo",
    ):
        assert key in snap, f"snapshot missing {key}"
    for agent in ("timeline", "connections", "trials", "safety", "briefing", "justify"):
        assert agent in snap["agents"], f"snapshot.agents missing {agent}"


def test_meta_endpoint():
    r = client.get("/api/meta")
    assert r.status_code == 200
    body = r.json()
    assert body["patient_id"]
    assert body["condition"]
    assert isinstance(body["build_log"], list)


# --------------------------------------------------------------------------- #
# per-agent read surfaces
# --------------------------------------------------------------------------- #
def test_timeline_endpoint_is_ordered_and_dated():
    r = client.get("/api/patient/odyssey/timeline")
    assert r.status_code == 200
    events = r.json()["events"]
    assert len(events) >= 10
    dates = [e["date"] for e in events]
    assert dates == sorted(dates), "timeline events must be date-ordered"


def test_connections_ranks_takayasu_first():
    r = client.get("/api/patient/odyssey/connections")
    assert r.status_code == 200
    body = r.json()
    assert body["top_condition"] == "Takayasu arteritis"
    labels = [row["condition"] for row in body["ranking"]]
    assert "Takayasu arteritis" == labels[0]
    assert body["ranking"][0]["score"] >= body["ranking"][1]["score"]
    # every surfaced candidate carries at least one citation
    for cand in body["candidates"]:
        assert cand["evidence"], "connections candidate must be cited"


def test_trials_eligibility_partition():
    r = client.get("/api/patient/odyssey/trials")
    assert r.status_code == 200
    body = r.json()
    assert set(body["eligible_ids"]) == {"NCT09000001", "NCT09000002", "NCT09000003"}
    # the paediatric "right disease, wrong age" trap is excluded
    assert "NCT09000006" in body["ineligible_ids"]
    for m in body["matches"]:
        assert m["evidence"], f"trial {m['nct_id']} must be cited"


def test_safety_alerts_cited():
    r = client.get("/api/patient/odyssey/safety")
    assert r.status_code == 200
    body = r.json()
    assert len(body["alerts"]) >= 3
    kinds = {a["kind"] for a in body["alerts"]}
    assert "interaction" in kinds
    assert "duplication" in kinds
    for a in body["alerts"]:
        assert a["evidence"], "safety alert must cite the co-prescription"


def test_briefing_has_confirmed_dx_milestone():
    r = client.get("/api/patient/odyssey/briefing")
    assert r.status_code == 200
    brief = r.json()["brief"]
    assert brief["summary"]
    assert brief["open_questions"]
    descs = " ".join(h["description"].lower() for h in brief["timeline_highlights"])
    assert "takayasu" in descs


def test_justify_packet_complete():
    r = client.get("/api/patient/odyssey/justify")
    assert r.status_code == 200
    body = r.json()
    assert body["requested_drug"] == "tocilizumab"
    assert "Takayasu" in (body["indication"] or "")
    assert body["complete"] is True
    assert len(body["elements"]) == 4
    for el in body["elements"]:
        assert el["satisfied"], f"element {el['key']} must be satisfied+cited"


def test_timetravel_flags_18_months_earlier():
    r = client.get("/api/patient/odyssey/timetravel")
    assert r.status_code == 200
    tt = r.json()
    assert tt["first_flag_date"] == "2022-08-05"
    assert tt["true_diagnosis_date"] == "2024-03-01"
    assert tt["months_earlier"] == 18
    assert len(tt["trace"]) >= 5


def test_redthread_every_hop_is_a_real_edge():
    r = client.get("/api/patient/odyssey/redthread")
    assert r.status_code == 200
    bundle = r.json()
    assert bundle["all_edges_exist"] is True
    assert bundle["n_patient_threads"] >= 2
    assert bundle["unresolved_anchors"] == []
    # every resolved thread terminates at a source document with a quote
    for t in bundle["patient_threads"]:
        if t["resolved"]:
            assert t["document_label"]
            assert t["quote"]


def test_graph_endpoint():
    r = client.get("/api/patient/odyssey/graph")
    assert r.status_code == 200
    g = r.json()
    assert g["n_nodes"] > 0
    assert g["counts_by_type"]


# --------------------------------------------------------------------------- #
# RBAC — the enforcement boundary (the headline contract)
# --------------------------------------------------------------------------- #
def test_rbac_matrix_denies_family_clinical():
    r = client.get("/api/rbac")
    assert r.status_code == 200
    matrix = r.json()["matrix"]
    assert matrix["family"]["clinical"] is False
    assert matrix["provider"]["clinical"] is True
    assert matrix["owner"]["clinical"] is True


def test_rbac_check_family_clinical_returns_empty():
    r = client.post("/api/rbac/check",
                    json={"role": "family", "brain": "clinical", "patient_id": "odyssey"})
    assert r.status_code == 200
    body = r.json()
    assert body["authorized"] is False
    assert body["denied"] is True
    assert body["datasets"] == []


def test_rbac_check_provider_clinical_grants_dataset():
    r = client.post("/api/rbac/check",
                    json={"role": "provider", "brain": "clinical", "patient_id": "odyssey"})
    assert r.status_code == 200
    body = r.json()
    assert body["authorized"] is True
    assert body["datasets"]
    assert body["datasets"][0].startswith("patient_odyssey_clinical")


def test_rbac_check_family_reference_allowed():
    # family CAN read the global reference brains
    r = client.post("/api/rbac/check",
                    json={"role": "family", "brain": "literature", "patient_id": "odyssey"})
    assert r.status_code == 200
    assert r.json()["authorized"] is True


# --------------------------------------------------------------------------- #
# improve() reweight — red herring demoted, top unchanged
# --------------------------------------------------------------------------- #
def test_improve_get_returns_demo():
    r = client.get("/api/improve")
    assert r.status_code == 200
    body = r.json()
    assert body.get("baseline")
    assert body.get("after_feedback")


def test_improve_downvote_demotes_red_herring_keeps_top():
    r = client.post("/api/improve", json={"downvote": "Lymphoma"})
    assert r.status_code == 200
    body = r.json()
    assert body["downvoted"] == "Lymphoma"
    base = {row["label"]: row["score"] for row in body["baseline"]}
    after = {row["label"]: row["score"] for row in body["after_feedback"]}
    # top candidate unaffected
    assert body["baseline"][0]["label"] == "Takayasu arteritis"
    assert body["after_feedback"][0]["label"] == "Takayasu arteritis"
    # red herring demoted
    assert after["Lymphoma"] < base["Lymphoma"]


# --------------------------------------------------------------------------- #
# sessions + forget (captured proofs)
# --------------------------------------------------------------------------- #
def test_sessions_attributes_all_agents():
    r = client.get("/api/sessions")
    assert r.status_code == 200
    body = r.json()
    assert body["total_sessions"] > 0
    assert body["all_agents_attributed"] is True
    for agent in ("timeline", "connections", "trials", "briefing", "safety", "justify"):
        assert agent in body["by_agent"]


def test_forget_proof_is_surgical():
    r = client.get("/api/forget")
    assert r.status_code == 200
    proof = r.json()
    assert proof["is_surgical"] is True
    assert proof["nodes_after"] < proof["nodes_before"]
    assert proof["probe_present_before"] is True
    assert proof["probe_absent_after"] is True
    assert proof["unrelated_survives"] is True
