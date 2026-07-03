"""Unit tests for the terminology normalization layer (offline, deterministic)."""

import re

from app.normalize import (
    HPO,
    LOINC,
    RXNORM,
    SNOMED,
    Normalizer,
    coverage,
    normalize,
    resolve_hpo_set,
)
from app.seed.odyssey_patient import GOLDEN


def test_exact_medication():
    code = normalize("Prednisolone", "medication")
    assert code is not None
    assert code.system == RXNORM and code.code == "8638"
    assert code.method == "exact" and code.confidence == 1.0


def test_medication_abbreviations_and_brands():
    assert normalize("MTX", "medication").code == "6851"
    assert normalize("ASA", "medication").code == "1191"
    assert normalize("Actemra", "medication").code == "612865"


def test_medication_dose_noise_stripped():
    assert normalize("Pred 20 mg OD", "medication").code == "8638"
    assert normalize("aspirin 75 mg po daily", "medication").code == "1191"


def test_lab_with_value_noise():
    assert normalize("ESR 62 mm/hr", "lab").code == "4537-7"
    assert normalize("C-reactive protein", "lab").code == "1988-5"
    assert normalize("Hb 10.4 g/dL", "lab").code == "718-7"


def test_symptom_phrase_substring():
    assert normalize("low-grade evening fevers", "symptom").code == "HP:0001945"
    assert normalize("left calf claudication on walking", "symptom").code == "HP:0004417"
    assert normalize("raised inflammatory markers", "symptom").code == "HP:0003565"


def test_condition_multivocab():
    norm = Normalizer()
    code = norm.normalize("Takayasu's arteritis", "condition")
    assert code is not None and code.system == SNOMED and code.code == "155441006"
    extra = norm.extra_codes("condition", code.code)
    assert extra["ICD-10"] == "M31.4"
    assert extra["Orphanet"] == "ORPHA:3287"
    assert extra["OMIM"] == "207600"


def test_fuzzy_fallback_on_typo():
    code = normalize("prednislone", "medication")  # missing 'o'
    assert code is not None and code.code == "8638"
    assert code.method == "fuzzy" and code.confidence >= 0.86


def test_unmatched_returns_none():
    assert normalize("unicorn dust", "medication") is None
    assert normalize("", "symptom") is None


def test_canonical_entity_resolution():
    norm = Normalizer()
    assert norm.canonical("MTX", "medication") == norm.canonical("Methotrexate", "medication")
    assert norm.canonical("Methotrexate 15 mg weekly", "medication") == "Methotrexate"


def test_resolve_hpo_set_dedupes_and_sorts():
    ids = resolve_hpo_set(["fatigue", "tiredness", "night sweats", "fatigue"])
    assert ids == sorted(ids)
    assert "HP:0012378" in ids and "HP:0030166" in ids
    assert len(ids) == len(set(ids))


def _looks_like_code(v: str) -> bool:
    return bool(re.search(r"\d", str(v))) and "type:" not in str(v)


def test_golden_symptom_coverage_100pct():
    mentions = list(GOLDEN["symptoms_hpo"].keys())
    rep = coverage(mentions, "symptom")
    assert rep.coverage == 1.0, f"unmatched: {rep.unmatched}"
    by_input = {c.input: c.code for c in rep.results}
    for name, hpo in GOLDEN["symptoms_hpo"].items():
        assert by_input[name] == hpo


def test_golden_medication_coverage_100pct():
    mentions = list(GOLDEN["medications_rxnorm"].keys())
    rep = coverage(mentions, "medication")
    assert rep.coverage == 1.0, f"unmatched: {rep.unmatched}"
    by_input = {c.input: c.code for c in rep.results}
    for name, rxnorm in GOLDEN["medications_rxnorm"].items():
        if _looks_like_code(rxnorm):
            assert by_input[name] == rxnorm


def test_golden_lab_coverage_100pct():
    mentions = list(GOLDEN["labs_loinc"].keys())
    rep = coverage(mentions, "lab")
    assert rep.coverage == 1.0, f"unmatched: {rep.unmatched}"
    by_input = {c.input: c.code for c in rep.results}
    for name, loinc in GOLDEN["labs_loinc"].items():
        assert by_input[name] == loinc


def test_unicode_hyphen_normalized():
    # non-breaking hyphen (U+2011) as seen in extracted clinical text
    code = normalize("Light\u2011headedness on exertion", "symptom")
    assert code is not None and code.code == "HP:0002321"


def test_new_lab_entries():
    assert normalize("Ferritin", "lab").code == "2276-4"
    assert normalize("TSH", "lab").code == "3016-3"
    assert normalize("U&E", "lab").code == "24362-6"
    assert normalize("LFTs", "lab").code == "24325-3"


def test_new_symptom_entries():
    assert normalize("near-fainting", "symptom").code == "HP:0031273"
    assert normalize("Palpitations", "symptom").code == "HP:0001962"
    assert normalize("Left hand cold sensation", "symptom").code == "HP:0500015"
    assert normalize("Knee ache", "symptom").code == "HP:0002829"


def test_negative_and_vague_stay_unmatched():
    assert normalize("No chest pain", "symptom") is None
    assert normalize("Left arm symptoms", "symptom") is None


def test_coverage_dedupes_mentions():
    rep = coverage(["ESR", "esr", "ESR 40 mm/hr"], "lab")
    assert rep.total == 1 and rep.normalized == 1
