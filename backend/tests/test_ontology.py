from app import ontology


def test_graph_model_json_has_title():
    schema = ontology.clinical_graph_model_json()
    title = schema.get("title")
    # Server reconstructs the model via this title, so it must be a valid,
    # non-empty Python identifier. (Cognee's serializer uses the class name.)
    assert isinstance(title, str) and title.isidentifier()
    assert "properties" in schema


def test_required_node_types_present():
    for required in ["Patient", "Condition", "Medication", "LabResult", "Symptom", "Trial"]:
        assert required in ontology.NODE_TYPES


def test_required_edge_types_present():
    for required in ["SUGGESTS", "RULED_OUT_BY", "ELIGIBLE_FOR", "INTERACTS_WITH"]:
        assert required in ontology.EDGE_TYPES


def test_normalization_fields_cover_core_vocabularies():
    vocabs = set(ontology.NORMALIZED_CODE_FIELDS.values())
    for v in ["HPO", "RxNorm", "LOINC", "SNOMED CT", "ICD-10", "Orphanet", "OMIM"]:
        assert v in vocabs


def test_ruled_out_status_exists():
    assert ontology.ConditionStatus.ruled_out.value == "ruled_out"


def test_extraction_prompt_forbids_diagnosis():
    assert "Do NOT infer or state a diagnosis" in ontology.CUSTOM_EXTRACTION_PROMPT
