import pytest
from pydantic import ValidationError

from app.models import (
    Confidence,
    DiagnosisLanguageError,
    EvidenceRef,
    Finding,
    FindingKind,
    assert_no_diagnosis,
    find_diagnosis_language,
)


def test_finding_requires_evidence():
    with pytest.raises(ValidationError):
        Finding(
            id="f1",
            kind=FindingKind.connection,
            summary="Consider an autoinflammatory pattern.",
            evidence=[],
            agent="connections",
        )


def test_finding_with_evidence_is_valid():
    f = Finding(
        id="f1",
        kind=FindingKind.connection,
        summary="Consider investigating an autoinflammatory pattern.",
        confidence=Confidence.moderate,
        evidence=[EvidenceRef(data_id="d1", document_name="labs.pdf", snippet="ferritin 900")],
        agent="connections",
    )
    assert f.evidence[0].data_id == "d1"
    assert "Not a diagnosis" in f.disclaimer


@pytest.mark.parametrize(
    "text",
    [
        "You have lupus.",
        "The diagnosis is Still's disease.",
        "Patient has rheumatoid arthritis.",
        "This is definitely sarcoidosis.",
        "These labs confirm the diagnosis.",
    ],
)
def test_diagnosis_language_is_flagged(text):
    assert find_diagnosis_language(text)
    with pytest.raises(DiagnosisLanguageError):
        assert_no_diagnosis(text)


@pytest.mark.parametrize(
    "text",
    [
        "Consider investigating an autoinflammatory pattern.",
        "These findings suggest evaluating for a systemic inflammatory process.",
        "Recommend specialist review of the constellation of symptoms.",
    ],
)
def test_decision_support_language_passes(text):
    assert find_diagnosis_language(text) == []
    assert assert_no_diagnosis(text) == text
