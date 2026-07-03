"""Terminology normalization + light entity resolution.

After extraction, clinical mentions in the graph are free text ("low-grade evening
fevers", "Pred 20 mg OD", "ESR 62 mm/hr"). This layer resolves a mention + category
to a standard code:

    medication -> RxNorm
    lab        -> LOINC
    symptom    -> HPO   (phenotype; drives ConnectionsAgent pattern-matching)
    condition  -> SNOMED CT / ICD-10 (+ Orphanet / OMIM for rare disease)

Design:
- Curated, category-scoped dictionaries with synonyms / brand<->generic / abbreviations.
  Deterministic and unit-testable (no external API or LLM cost). Seeded from the hero
  Takayasu GOLDEN set plus common general-medicine synonyms; extend per new patient.
- Matching is layered: exact -> whole-word substring (handles dose/route/frequency
  noise and phrase mentions like "left calf claudication") -> fuzzy (difflib) fallback.
- `canonical()` collapses variants to one display name for entity resolution (dedupe
  brand<->generic, "MTX" == "Methotrexate", provider spelling drift).

The same `Normalizer` is used at three points: (1) the P1 eval computes normalization
coverage %, (2) future ingests can fill the ontology's normalized-code fields
(Medication.rxnorm, LabResult.loinc, ...), (3) the P2 ConnectionsAgent turns a
patient's symptom set into an HPO term set for Orphanet/OMIM matching.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --- code systems -----------------------------------------------------------
RXNORM = "RxNorm"
LOINC = "LOINC"
HPO = "HPO"
SNOMED = "SNOMED-CT"
ICD10 = "ICD-10"
ORPHANET = "Orphanet"
OMIM = "OMIM"

FUZZY_THRESHOLD = 0.86

# Dose / route / frequency / form noise stripped before matching medications & labs.
_NOISE_TOKENS = {
    "mg", "mcg", "g", "ml", "iu", "units", "unit", "u", "%", "mmol", "mm", "hr",
    "mg/dl", "g/dl", "mm/hr", "mg/l", "ng/ml",
    "po", "iv", "im", "sc", "oral", "orally", "intravenous", "subcut", "subcutaneous",
    "od", "bd", "tds", "qds", "qd", "bid", "tid", "prn", "daily", "weekly", "nocte",
    "mane", "once", "twice", "every", "day", "week", "hours", "hourly", "at", "night",
    "tablet", "tablets", "tab", "tabs", "cap", "caps", "capsule", "injection", "inj",
    "dose", "low", "high", "started", "commenced", "on", "of", "the", "a", "with",
    "and", "or", "for", "to", "per", "level", "count", "test",
}

_TOKEN_RE = re.compile(r"[a-z0-9\.\-\+/:]+")

# Map assorted Unicode hyphens/dashes to ASCII "-" so tokens like "light‑headedness"
# (non-breaking hyphen U+2011, common in extracted clinical text) don't fragment.
_DASHES = {ord(ch): "-" for ch in "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"}


@dataclass
class NormalizedCode:
    system: str
    code: str
    display: str
    confidence: float
    method: str            # "exact" | "synonym" | "fuzzy"
    input: str = ""

    def as_dict(self) -> dict:
        return {
            "system": self.system, "code": self.code, "display": self.display,
            "confidence": round(self.confidence, 3), "method": self.method,
            "input": self.input,
        }


@dataclass
class _Entry:
    system: str
    code: str
    display: str
    synonyms: List[str] = field(default_factory=list)  # lowercased triggers

    def all_terms(self) -> List[str]:
        terms = [self.display.lower()] + [s.lower() for s in self.synonyms]
        # longest first so "intermittent claudication" beats "claudication"
        return sorted(set(terms), key=len, reverse=True)


def _clean(text: str) -> str:
    text = (text or "").translate(_DASHES)
    toks = _TOKEN_RE.findall(text.lower())
    kept = [t for t in toks if t not in _NOISE_TOKENS and not t.replace(".", "").isdigit()]
    return " ".join(kept).strip()


def _whole_word(needle: str, haystack: str) -> bool:
    if not needle:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


# --- curated dictionaries (hero Takayasu + common synonyms) -----------------
def _medications() -> List[_Entry]:
    return [
        _Entry(RXNORM, "8638", "Prednisolone",
               ["pred", "prednisone", "glucocorticoid", "corticosteroid", "steroid", "solu-medrol", "methylprednisolone"]),
        _Entry(RXNORM, "6851", "Methotrexate", ["mtx", "methotrexate sodium"]),
        _Entry(RXNORM, "612865", "Tocilizumab", ["actemra", "anti-il-6", "il-6 inhibitor", "roactemra"]),
        _Entry(RXNORM, "1191", "Aspirin", ["asa", "acetylsalicylic acid", "acetyl salicylic acid", "low-dose aspirin"]),
        _Entry(RXNORM, "17767", "Amlodipine", ["norvasc", "amlodipine besylate"]),
        _Entry(RXNORM, "35296", "Ramipril", ["altace", "ace inhibitor"]),
        _Entry(RXNORM, "4832", "Ferrous sulfate", ["iron", "iron salt", "ferrous sulphate", "iron supplement", "ferrous"]),
    ]


def _labs() -> List[_Entry]:
    return [
        _Entry(LOINC, "4537-7", "ESR",
               ["erythrocyte sedimentation rate", "sed rate", "sedimentation rate", "esr"]),
        _Entry(LOINC, "1988-5", "CRP", ["c-reactive protein", "c reactive protein", "crp"]),
        _Entry(LOINC, "718-7", "Hemoglobin", ["haemoglobin", "hgb", "hb"]),
        _Entry(LOINC, "2160-0", "Creatinine", ["serum creatinine", "cr"]),
        _Entry(LOINC, "2276-4", "Ferritin", ["serum ferritin"]),
        _Entry(LOINC, "3016-3", "TSH", ["thyroid stimulating hormone", "thyrotropin", "thyroid function"]),
        _Entry(LOINC, "2823-3", "Potassium", ["serum potassium", "k+"]),
        _Entry(LOINC, "24362-6", "Renal panel",
               ["renal function panel", "renal function", "u&e", "urea and electrolytes", "kidney panel", "renal profile"]),
        _Entry(LOINC, "24325-3", "Liver panel",
               ["liver function tests", "lfts", "hepatic function panel", "liver function", "liver profile"]),
    ]


def _symptoms() -> List[_Entry]:
    return [
        _Entry(HPO, "HP:0012378", "Fatigue", ["tiredness", "malaise", "exhaustion", "lethargy", "tired", "washed out", "washed out feeling", "run down"]),
        _Entry(HPO, "HP:0001945", "Fever", ["pyrexia", "low-grade fever", "low grade fever", "febrile", "fevers", "evening fever"]),
        _Entry(HPO, "HP:0001824", "Weight loss", ["weight-loss", "losing weight", "unintentional weight loss"]),
        _Entry(HPO, "HP:0002829", "Arthralgia", ["joint pain", "joint aches", "arthralgias", "knee pain", "wrist pain", "joint ache", "knee ache", "wrist ache", "elbow ache"]),
        _Entry(HPO, "HP:0030166", "Night sweats", ["nightsweats", "drenching sweats", "sweats"]),
        _Entry(HPO, "HP:0003565", "Elevated ESR", ["raised esr", "elevated erythrocyte sedimentation rate", "high esr", "raised inflammatory markers", "elevated inflammatory markers"]),
        _Entry(HPO, "HP:0011227", "Elevated CRP", ["raised crp", "elevated c-reactive protein", "high crp"]),
        _Entry(HPO, "HP:0001903", "Anemia", ["anaemia", "low hemoglobin", "low haemoglobin", "normocytic anemia", "normocytic anaemia"]),
        _Entry(HPO, "HP:0000822", "Hypertension", ["high blood pressure", "raised blood pressure", "elevated blood pressure", "htn"]),
        _Entry(HPO, "HP:0004417", "Intermittent claudication", ["claudication", "limb claudication", "calf claudication", "arm claudication", "left arm claudication"]),
        _Entry(HPO, "HP:0025153", "Reduced pulse", ["absent pulse", "weak pulse", "diminished pulse", "pulseless", "reduced radial pulse", "absent radial pulse", "unequal pulses"]),
        _Entry(HPO, "HP:0031955", "Arterial bruit", ["bruit", "carotid bruit", "subclavian bruit", "vascular bruit"]),
        _Entry(HPO, "HP:0002321", "Vertigo", ["dizziness", "dizzy", "light-headedness", "lightheadedness", "lightheaded"]),
        _Entry(HPO, "HP:0000505", "Visual impairment", ["blurred vision", "visual disturbance", "visual loss", "vision loss", "blurring of vision", "transient visual", "blurring", "blurred"]),
        _Entry(HPO, "HP:0031664", "Blood pressure difference between arms", ["inter-arm blood pressure difference", "interarm bp difference", "blood pressure difference between the arms", "unequal arm blood pressure", "inter-arm bp difference"]),
        _Entry(HPO, "HP:0002315", "Headache", ["headaches", "cephalgia"]),
        _Entry(HPO, "HP:0031273", "Presyncope", ["near-fainting", "near fainting", "near-syncope", "near syncope", "presyncope", "drop attack", "drop attacks"]),
        _Entry(HPO, "HP:0001962", "Palpitations", ["palpitation", "racing heart", "heart racing"]),
        _Entry(HPO, "HP:0500015", "Cold extremities", ["cold sensation", "cold hand", "cold hands", "hand cold", "cold extremity", "cold extremities", "left hand cold sensation", "coldness"]),
        _Entry(HPO, "HP:0000858", "Irregular menstruation", ["irregular menstrual cycles", "irregular periods", "menstrual irregularity", "irregular menses"]),
    ]


def _conditions() -> List[_Entry]:
    # Multi-system codes are attached via CONDITION_EXTRA; primary match returns SNOMED.
    return [
        _Entry(SNOMED, "155441006", "Takayasu arteritis",
               ["takayasu", "takayasu's arteritis", "takayasu disease", "pulseless disease", "aortic arch syndrome", "large-vessel vasculitis", "tak"]),
        _Entry(SNOMED, "38341003", "Hypertension", ["high blood pressure", "htn", "essential hypertension"]),
        _Entry(SNOMED, "271737000", "Anemia", ["anaemia", "normocytic anemia"]),
    ]


# Extra cross-vocabulary codes for conditions (rare-disease registries).
CONDITION_EXTRA: Dict[str, Dict[str, str]] = {
    "155441006": {ICD10: "M31.4", ORPHANET: "ORPHA:3287", OMIM: "207600"},
}

_CATEGORY_BUILDERS = {
    "medication": _medications,
    "lab": _labs,
    "symptom": _symptoms,
    "condition": _conditions,
}


class Normalizer:
    def __init__(self) -> None:
        self._dicts: Dict[str, List[_Entry]] = {
            cat: builder() for cat, builder in _CATEGORY_BUILDERS.items()
        }

    def categories(self) -> List[str]:
        return list(self._dicts)

    def normalize(self, mention: str, category: str) -> Optional[NormalizedCode]:
        entries = self._dicts.get(category)
        if not entries or not (mention or "").strip():
            return None
        cleaned = _clean(mention)
        if not cleaned:
            cleaned = (mention or "").lower().strip()

        best: Optional[Tuple[float, str, _Entry, str]] = None  # (score, method_rank, entry, method)

        for entry in entries:
            for term in entry.all_terms():
                if cleaned == term:
                    return self._code(entry, 1.0, "exact", mention)
                if _whole_word(term, cleaned) or _whole_word(cleaned, term):
                    # confidence scales with how much of the mention the term covers
                    conf = 0.9 if len(term) >= 4 else 0.8
                    cand = (conf, "b", entry, "synonym")
                    if best is None or cand[0] > best[0]:
                        best = cand

        if best is not None:
            _, _, entry, method = best
            return self._code(entry, best[0], method, mention)

        # fuzzy fallback across all terms in the category
        term_index: Dict[str, _Entry] = {}
        for entry in entries:
            for term in entry.all_terms():
                term_index[term] = entry
        match = difflib.get_close_matches(cleaned, list(term_index), n=1, cutoff=FUZZY_THRESHOLD)
        if match:
            ratio = difflib.SequenceMatcher(None, cleaned, match[0]).ratio()
            return self._code(term_index[match[0]], ratio, "fuzzy", mention)
        return None

    def canonical(self, mention: str, category: str) -> str:
        """Return the canonical display name for entity resolution (or cleaned text)."""
        code = self.normalize(mention, category)
        return code.display if code else _clean(mention) or (mention or "").strip()

    def scan(self, text: str, category: str) -> List[NormalizedCode]:
        """Find every dictionary term of `category` that appears as a whole word in
        free `text`, returning one NormalizedCode per distinct code (first matching
        term wins). Deterministic — grounded in the literal text — so it can date a
        phenotype by the note that documents it (used by the P4 time-travel), without
        inventing a feature the note does not contain.
        """
        entries = self._dicts.get(category) or []
        hay = (text or "").translate(_DASHES).lower()
        out: List[NormalizedCode] = []
        seen: set = set()
        for entry in entries:
            for term in entry.all_terms():
                if _whole_word(term, hay):
                    if entry.code not in seen:
                        seen.add(entry.code)
                        out.append(self._code(entry, 0.9, "scan", term))
                    break
        return out

    def _code(self, entry: _Entry, confidence: float, method: str, mention: str) -> NormalizedCode:
        return NormalizedCode(entry.system, entry.code, entry.display, confidence, method, mention)

    def extra_codes(self, category: str, primary_code: str) -> Dict[str, str]:
        if category == "condition":
            return dict(CONDITION_EXTRA.get(primary_code, {}))
        return {}


@dataclass
class CoverageReport:
    category: str
    total: int
    normalized: int
    unmatched: List[str] = field(default_factory=list)
    results: List[NormalizedCode] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return (self.normalized / self.total) if self.total else 1.0


def coverage(mentions: List[str], category: str, normalizer: Optional[Normalizer] = None) -> CoverageReport:
    norm = normalizer or Normalizer()
    results: List[NormalizedCode] = []
    unmatched: List[str] = []
    seen = set()
    for m in mentions:
        key = _clean(m) or (m or "").lower().strip()
        if key in seen:
            continue
        seen.add(key)
        code = norm.normalize(m, category)
        if code:
            results.append(code)
        else:
            unmatched.append(m)
    total = len(seen)
    return CoverageReport(category, total, len(results), unmatched, results)


_DEFAULT = Normalizer()


def normalize(mention: str, category: str) -> Optional[NormalizedCode]:
    """Module-level convenience using a shared default Normalizer."""
    return _DEFAULT.normalize(mention, category)


def resolve_hpo_set(symptom_mentions: List[str]) -> List[str]:
    """Symptom mentions -> deduped sorted list of HPO ids (for phenotype matching)."""
    ids = []
    for m in symptom_mentions:
        code = _DEFAULT.normalize(m, "symptom")
        if code and code.code not in ids:
            ids.append(code.code)
    return sorted(ids)


def hpo_display_map() -> Dict[str, str]:
    """HPO id -> human-readable display, for rendering matched phenotype features."""
    return {e.code: e.display for e in _DEFAULT._dicts["symptom"]}
