"""Deterministic (offline) tests for the global reference brains — graph_model
serialization + curated corpus integrity. No cloud / LLM needed."""

import json

from app import ontology
from app.seed import reference_data


# --- reference graph_models --------------------------------------------------

def test_reference_graph_models_have_identifier_titles():
    for fn in (ontology.reference_literature_graph_model_json,
               ontology.reference_trials_graph_model_json):
        schema = fn()
        title = schema.get("title")
        # Server reconstructs the model from this title -> must be a valid identifier.
        assert isinstance(title, str) and title.isidentifier(), title
        assert "properties" in schema


def test_reference_graph_models_are_json_serializable():
    # No enums / non-primitive property types (the pitfall that wedged a dataset).
    json.dumps(ontology.reference_literature_graph_model_json())
    json.dumps(ontology.reference_trials_graph_model_json())


def test_reference_prompts_are_scoped():
    assert "LiteraturePattern" in ontology.LITERATURE_EXTRACTION_PROMPT
    assert "EligibilityCriterion" in ontology.TRIALS_EXTRACTION_PROMPT


# --- literature corpus -------------------------------------------------------

def test_literature_includes_takayasu_and_key_differentials():
    conditions = {c.lower() for c in reference_data.literature_conditions()}
    assert "takayasu arteritis" in conditions
    # ConnectionsAgent must discriminate Takayasu from these mimics.
    for diff in ("giant cell arteritis", "adult-onset still's disease",
                 "systemic lupus erythematosus", "fibromuscular dysplasia"):
        assert diff in conditions, diff


def test_literature_min_patterns_met():
    docs = list(reference_data.iter_literature())
    assert len(docs) >= int(reference_data.REFERENCE_GOLDEN["literature_min_patterns"])
    for doc_id, text, meta in docs:
        assert doc_id and text and meta.get("kind") == "literature"


def test_takayasu_abstract_carries_vascular_discriminators():
    tak = next(p for p in reference_data.LITERATURE_PATTERNS
               if p["condition"] == "Takayasu arteritis")
    low = tak["text"].lower()
    for feature in ("inter-arm", "bruit", "claudication", "angiography", "hla-b*52:01"):
        assert feature in low, feature


# --- trials corpus -----------------------------------------------------------

def test_trials_min_count_and_unique_nct_ids():
    ncts = reference_data.trial_nct_ids()
    assert len(ncts) >= int(reference_data.REFERENCE_GOLDEN["trials_min"])
    assert len(ncts) == len(set(ncts))  # unique


def test_trial_match_expectations_are_disjoint_and_present():
    should = set(reference_data.REFERENCE_GOLDEN["trials_should_match"])
    should_not = set(reference_data.REFERENCE_GOLDEN["trials_should_not_match"])
    assert should.isdisjoint(should_not)
    known = set(reference_data.trial_nct_ids())
    assert should <= known
    assert should_not <= known


def test_iter_trials_metadata_shape():
    for doc_id, text, meta in reference_data.iter_trials():
        assert doc_id and text
        assert meta.get("kind") == "trial"
        assert meta.get("nct_id", "").startswith("NCT")
        assert meta.get("match_expectation") in {"match", "no-match"}
