from app.recall_parse import parse_recall

# Mirrors the real Cloud recall response shape captured in scripts/smoke_cloud.py.
_REAL_TEXT = (
    "- **Symptoms** \u2013 6 months of low-grade fevers, malaise and left-arm claudication.\n"
    "- **Exam findings** \u2013 BP discrepancy between arms and a left subclavian bruit.\n"
    "- **Laboratory results** \u2013 ESR 74 mm/hr and CRP 41 mg/L.\n\n"
    "Evidence:\n"
    "- chunk 1 of document doc_0 (data_id: 1bf60001-08dc-5877-ab5a-3289d1f7e789, "
    "chunk_id: c70aa607-489d-5b25-afa8-d7c3c5732390): \"Clinic note 2022-03-14. "
    "26-year-old woman. Six months of low-grade fevers, malaise, and left arm claudication.\""
)

_RESPONSE = [
    {
        "kind": "graph_completion",
        "search_type": "GRAPH_COMPLETION",
        "text": _REAL_TEXT,
        "score": None,
        "dataset_id": "8d1b1fc4-5d46-58b2-960a-687468eb83c9",
        "dataset_name": "ariadne_smoke_gm",
        "metadata": {},
        "raw": {"value": _REAL_TEXT},
        "structured": None,
        "source": "graph",
    }
]


def test_parses_answer_and_strips_evidence_block():
    r = parse_recall(_RESPONSE)
    assert "Symptoms" in r.answer
    assert "Evidence:" not in r.answer  # citations split out of the answer body
    assert r.search_type == "GRAPH_COMPLETION"
    assert r.source == "graph"
    assert "8d1b1fc4-5d46-58b2-960a-687468eb83c9" in r.dataset_ids


def test_extracts_structured_citation():
    r = parse_recall(_RESPONSE)
    assert r.has_citations
    assert len(r.references) == 1
    ref = r.references[0]
    assert ref.data_id == "1bf60001-08dc-5877-ab5a-3289d1f7e789"
    assert ref.chunk_id == "c70aa607-489d-5b25-afa8-d7c3c5732390"
    assert ref.document_name == "doc_0"
    assert ref.snippet and "Clinic note 2022-03-14" in ref.snippet


def test_empty_response_is_safe():
    r = parse_recall([])
    assert r.answer == ""
    assert r.references == []
    assert r.has_citations is False


def test_multiple_citations_and_no_evidence_block():
    text = (
        "Some answer.\n\nEvidence:\n"
        "- chunk 1 of document doc_2 (data_id: aaa11111-2222-3333-4444-555566667777, "
        "chunk_id: bbb11111-2222-3333-4444-555566667777): \"quote one\"\n"
        "- chunk 4 of document doc_9 (data_id: ccc11111-2222-3333-4444-555566667777, "
        "chunk_id: ddd11111-2222-3333-4444-555566667777): \"quote two\""
    )
    r = parse_recall([{"text": text}])
    assert len(r.references) == 2
    assert {ref.document_name for ref in r.references} == {"doc_2", "doc_9"}

    r2 = parse_recall([{"text": "Just an answer, no citations."}])
    assert r2.answer == "Just an answer, no citations."
    assert r2.references == []
