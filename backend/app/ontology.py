"""Ariadne clinical ontology.

Defines the node/edge types Cognee should extract into the per-patient knowledge
graph, the normalized-code fields that the normalization layer (P1) fills in, and
the custom extraction prompt. `clinical_graph_model_json()` produces a JSON schema
with a top-level `title` suitable for the Cognee `graph_model` parameter.

The concrete binding to Cognee's `graph_model` API is exercised/validated in P1;
P0 only needs this schema to be well-formed and to contain the required types.
"""

from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ConditionStatus(str, Enum):
    suspected = "suspected"
    confirmed = "confirmed"
    ruled_out = "ruled_out"  # negative memory: never re-suggest a ruled-out dx


# --- Node types -------------------------------------------------------------

class Provider(BaseModel):
    name: str
    specialty: Optional[str] = None
    npi: Optional[str] = None


class Encounter(BaseModel):
    date: str = Field(description="ISO date of the encounter")
    provider: Optional[str] = None
    setting: Optional[str] = Field(default=None, description="e.g. ED, outpatient, inpatient")
    reason: Optional[str] = None


class Symptom(BaseModel):
    name: str
    onset: Optional[str] = Field(default=None, description="ISO date symptom began")
    severity: Optional[str] = None
    hpo: Optional[str] = Field(default=None, description="Normalized HPO term id, e.g. HP:0001945")


class Condition(BaseModel):
    name: str
    # Plain string (not the enum) so extracted node properties serialize cleanly
    # to JSONB in the Cloud graph store. Allowed values live in the description;
    # the normalization/validation layer coerces to ConditionStatus in app logic.
    status: str = Field(
        default=ConditionStatus.suspected.value,
        description="condition status, one of: suspected | confirmed | ruled_out",
    )
    date: Optional[str] = None
    icd10: Optional[str] = None
    snomed: Optional[str] = None
    orphanet: Optional[str] = None
    omim: Optional[str] = None


class Medication(BaseModel):
    name: str
    start: Optional[str] = None
    end: Optional[str] = None
    dose: Optional[str] = None
    prescriber: Optional[str] = None
    rxnorm: Optional[str] = Field(default=None, description="Normalized RxNorm CUI")


class LabResult(BaseModel):
    analyte: str
    date: Optional[str] = None
    value: Optional[str] = None
    unit: Optional[str] = None
    ref_range: Optional[str] = None
    flag: Optional[str] = Field(default=None, description="H/L/N abnormal flag")
    loinc: Optional[str] = Field(default=None, description="Normalized LOINC code")


class ImagingStudy(BaseModel):
    modality: str
    date: Optional[str] = None
    body_site: Optional[str] = None
    impression: Optional[str] = None


class Procedure(BaseModel):
    name: str
    date: Optional[str] = None


class FamilyHistory(BaseModel):
    relation: str
    condition: str


class GeneVariant(BaseModel):
    gene: str
    variant: Optional[str] = None
    significance: Optional[str] = None


class EligibilityCriterion(BaseModel):
    text: str
    kind: str = Field(description="inclusion or exclusion")


class Trial(BaseModel):
    nct_id: str
    title: Optional[str] = None
    phase: Optional[str] = None
    status: Optional[str] = None
    conditions: List[str] = Field(default_factory=list)


class LiteraturePattern(BaseModel):
    condition: str
    features: List[str] = Field(default_factory=list, description="HPO-coded phenotype features")
    source: Optional[str] = None


# --- Graph container (usable as a Cognee graph_model) -----------------------

class ClinicalKnowledgeGraph(BaseModel):
    """Container model describing the clinical graph Cognee should build."""

    title: str = "AriadneClinicalKnowledgeGraph"
    patients: List[str] = Field(default_factory=list)
    providers: List[Provider] = Field(default_factory=list)
    encounters: List[Encounter] = Field(default_factory=list)
    symptoms: List[Symptom] = Field(default_factory=list)
    conditions: List[Condition] = Field(default_factory=list)
    medications: List[Medication] = Field(default_factory=list)
    labs: List[LabResult] = Field(default_factory=list)
    imaging: List[ImagingStudy] = Field(default_factory=list)
    procedures: List[Procedure] = Field(default_factory=list)
    family_history: List[FamilyHistory] = Field(default_factory=list)
    genetics: List[GeneVariant] = Field(default_factory=list)


# --- Reference-brain containers (global read-only literature + trials) -------

class ReferenceLiteratureGraph(BaseModel):
    """Literature patterns linking a condition to its phenotype constellation."""

    title: str = "AriadneReferenceLiteratureGraph"
    patterns: List[LiteraturePattern] = Field(default_factory=list)
    conditions: List[Condition] = Field(default_factory=list)


class ReferenceTrialsGraph(BaseModel):
    """Clinical trials with their eligibility criteria."""

    title: str = "AriadneReferenceTrialsGraph"
    trials: List[Trial] = Field(default_factory=list)
    criteria: List[EligibilityCriterion] = Field(default_factory=list)


NODE_TYPES = [
    "Patient", "Provider", "Encounter", "Symptom", "Condition", "Medication",
    "LabResult", "ImagingStudy", "Procedure", "FamilyHistory", "GeneVariant",
    "Trial", "EligibilityCriterion", "LiteraturePattern",
]

EDGE_TYPES = [
    "PRESENTED_WITH", "PRECEDED", "MEASURED", "TREATED_WITH", "INTERACTS_WITH",
    "SUGGESTS", "RULED_OUT_BY", "ELIGIBLE_FOR", "CONTRAINDICATED_BY", "MATCHES_PATTERN",
]

# Normalized code field -> vocabulary, for the P1 normalization layer + coverage metric.
NORMALIZED_CODE_FIELDS = {
    "Symptom.hpo": "HPO",
    "Condition.icd10": "ICD-10",
    "Condition.snomed": "SNOMED CT",
    "Condition.orphanet": "Orphanet",
    "Condition.omim": "OMIM",
    "Medication.rxnorm": "RxNorm",
    "LabResult.loinc": "LOINC",
}


CUSTOM_EXTRACTION_PROMPT = (
    "You are extracting a longitudinal clinical knowledge graph from patient records. "
    "Extract entities as these node types: " + ", ".join(NODE_TYPES) + ". "
    "Connect them with these relationships: " + ", ".join(EDGE_TYPES) + ". "
    "Always capture the event DATE for every clinical fact (symptom onset, encounter, "
    "lab, medication start/stop) so the timeline can be reconstructed. "
    "Preserve exact source wording for values, doses, and lab numbers. "
    "Mark conditions as suspected, confirmed, or ruled_out. "
    "Do NOT infer or state a diagnosis; only extract what the record documents."
)


LITERATURE_EXTRACTION_PROMPT = (
    "You are extracting a medical literature knowledge graph. Each document describes a "
    "condition and the constellation of clinical features that suggests it. Extract "
    "LiteraturePattern nodes (condition + its characteristic features) and Condition nodes, "
    "and connect features to conditions with MATCHES_PATTERN / SUGGESTS edges. "
    "Capture every phenotype feature, distinguishing feature, and typical laboratory finding "
    "as separate feature strings. Preserve exact wording. Record the source citation. "
    "Do NOT invent facts beyond the document."
)


TRIALS_EXTRACTION_PROMPT = (
    "You are extracting a clinical-trials knowledge graph. Each document describes one trial. "
    "Extract a Trial node (nct_id, title, phase, status, conditions) and one "
    "EligibilityCriterion node per inclusion or exclusion criterion (kind = 'inclusion' or "
    "'exclusion'), connected with ELIGIBLE_FOR / CONTRAINDICATED_BY edges. "
    "Preserve exact criterion wording, thresholds, and NCT identifiers."
)


def _graph_model_json(model_cls) -> dict:
    """JSON schema (with top-level `title`) for a Cognee `graph_model` param.

    Prefer Cognee's own canonical serializer (`graph_model_to_graph_schema`) so the
    dict round-trips exactly through the server's `graph_schema_to_graph_model`
    reconstruction. Falls back to a plain pydantic schema if Cognee isn't importable
    (e.g. a pure-cloud environment without the SDK installed).
    """
    try:
        from cognee.shared.graph_model_utils import graph_model_to_graph_schema

        schema = graph_model_to_graph_schema(model_cls)
        schema.setdefault("title", model_cls.__name__)
        return schema
    except Exception:
        schema = model_cls.model_json_schema()
        title_field = model_cls.model_fields.get("title")
        schema["title"] = title_field.default if title_field else model_cls.__name__
        return schema


def clinical_graph_model_json() -> dict:
    """JSON schema (with top-level `title`) for the clinical `graph_model` param."""
    return _graph_model_json(ClinicalKnowledgeGraph)


def reference_literature_graph_model_json() -> dict:
    """JSON schema for the global literature reference `graph_model` param."""
    return _graph_model_json(ReferenceLiteratureGraph)


def reference_trials_graph_model_json() -> dict:
    """JSON schema for the global trials reference `graph_model` param."""
    return _graph_model_json(ReferenceTrialsGraph)
