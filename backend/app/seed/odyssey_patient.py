"""The hero "diagnostic odyssey" patient (synthetic — no real PHI).

Disease: Takayasu arteritis (large-vessel vasculitis) — under-recognised, with a
strong young-female predominance in South Asian / Indian populations, and a real
multi-year diagnostic delay. The clinical *signal* (a young woman with persistently
elevated ESR/CRP + constitutional symptoms for years, then limb claudication,
inter-arm blood-pressure discrepancy, and multi-territory bruits) is deliberately
scattered across many providers who each apply a plausible wrong label
("post-viral", "iron deficiency", "fibromyalgia", "anxiety", "musculoskeletal").
No single clinician connects the inflammatory years to the later vascular phase —
which is exactly the gap Ariadne's longitudinal graph closes.

Everything here is fabricated for demonstration. Codes are illustrative and should
be treated as synthetic; the normalization layer (P1) re-derives/validates them.
"""

from __future__ import annotations

from typing import Dict, Iterator, List, Tuple

HERO_PATIENT: Dict[str, str] = {
    "id": "odyssey",
    "display_name": "Meera N. (synthetic)",
    "sex": "female",
    "year_of_birth": "1994",
    "context": "Young adult woman; South Asian. Fully synthetic demonstration patient.",
    "true_diagnosis": "Takayasu arteritis",
    "true_diagnosis_date": "2024-03-01",
    # First point at which the *connected* record already supported flagging large-
    # vessel vasculitis: >12 months of persistently raised ESR/CRP + constitutional
    # symptoms in a young woman, now with the first unambiguous VASCULAR discriminator
    # (upper-limb claudication, 2022-08-05). This is the honest, deterministic anchor
    # the P4 time-travel counterfactual reproduces from memory (app/timetravel.py) —
    # the ranking, not fiat, decides the date; ~18 completed months before diagnosis.
    "earliest_flaggable_date": "2022-08-05",
    "months_earlier": 18,
}


# Each encounter is one document ingested into the patient's clinical brain.
# `text` reads like a real note: it carries the true signal AND the misleading
# label a busy clinician actually wrote.
ENCOUNTERS: List[Dict[str, str]] = [
    {
        "id": "enc-2021-02-10-gp",
        "date": "2021-02-10",
        "provider": "Dr. A. Sharma",
        "specialty": "General Medicine",
        "doc_type": "clinic_note",
        "text": (
            "2021-02-10 — General Medicine (Dr. A. Sharma).\n"
            "27-year-old woman, 3 months of persistent fatigue, low-grade evening "
            "fevers (37.8–38.2 C), general malaise, and about 4 kg unintentional weight "
            "loss. Occasional aching in knees and wrists. No cough, no urinary symptoms. "
            "Examination unremarkable; chest clear, abdomen soft.\n"
            "Bloods: Hb 10.4 g/dL (low, normocytic), ESR 62 mm/hr (raised), "
            "CRP 18 mg/L (raised), ferritin 90 ng/mL. TSH normal.\n"
            "Impression: likely post-viral fatigue with reactive anaemia. Advised rest, "
            "a course of oral iron, and to return if not settling."
        ),
    },
    {
        "id": "enc-2021-05-18-gyn",
        "date": "2021-05-18",
        "provider": "Dr. R. Iyer",
        "specialty": "Gynaecology",
        "doc_type": "clinic_note",
        "text": (
            "2021-05-18 — Gynaecology (Dr. R. Iyer).\n"
            "Reviewed for tiredness and slightly irregular menstrual cycles. Feels "
            "washed out. Attributes symptoms to heavy periods. Continued on oral iron; "
            "fatigue ascribed to iron deficiency. Inflammatory markers not repeated at "
            "this visit. Reassured."
        ),
    },
    {
        "id": "enc-2021-09-02-gp",
        "date": "2021-09-02",
        "provider": "Dr. A. Sharma",
        "specialty": "General Medicine",
        "doc_type": "clinic_note",
        "text": (
            "2021-09-02 — General Medicine (Dr. A. Sharma).\n"
            "Symptoms have NOT settled after 6 months. Ongoing low-grade fevers, "
            "occasional drenching night sweats, and arthralgia of knees and wrists. "
            "Weight stable but not regained. No rash.\n"
            "Bloods: Hb 10.1 g/dL, ESR 71 mm/hr (persistently raised), CRP 22 mg/L, "
            "ferritin 110 ng/mL, normal renal and liver panels.\n"
            "Impression: chronic inflammatory process, query connective tissue disease. "
            "ANA and rheumatoid factor requested; referring to Rheumatology."
        ),
    },
    {
        "id": "enc-2021-11-20-rheum",
        "date": "2021-11-20",
        "provider": "Dr. S. Menon",
        "specialty": "Rheumatology",
        "doc_type": "consult_note",
        "text": (
            "2021-11-20 — Rheumatology (Dr. S. Menon).\n"
            "Referred for arthralgia with raised inflammatory markers. No frank "
            "synovitis on examination. ANA negative, rheumatoid factor negative, "
            "anti-CCP negative. ESR today 68 mm/hr, CRP 20 mg/L.\n"
            "Impression: seronegative picture; symptoms felt to be most consistent with "
            "fibromyalgia / non-specific arthralgia. Reassurance, simple analgesia and "
            "graded exercise advised. No immunosuppression indicated at present.\n"
            "(NB: objective inflammatory markers remain elevated but were attributed to "
            "a benign cause.)"
        ),
    },
    {
        "id": "enc-2022-03-14-ed",
        "date": "2022-03-14",
        "provider": "Dr. K. Rao",
        "specialty": "Emergency",
        "doc_type": "ed_note",
        "text": (
            "2022-03-14 — Emergency Department (Dr. K. Rao).\n"
            "Presented with dizziness, headache and one episode of near-fainting while "
            "standing. Observations: BP right arm 148/92 mmHg; BP left arm difficult to "
            "obtain / much lower and unrecordable on repeat. Heart rate 84, afebrile. "
            "Neurological examination grossly normal.\n"
            "Treated as presumed dehydration / anxiety. Given oral fluids and "
            "discharged with advice. Inter-arm blood-pressure difference noted in "
            "observations but not further investigated."
        ),
    },
    {
        "id": "enc-2022-08-05-gp",
        "date": "2022-08-05",
        "provider": "Dr. A. Sharma",
        "specialty": "General Medicine",
        "doc_type": "clinic_note",
        "text": (
            "2022-08-05 — General Medicine (Dr. A. Sharma).\n"
            "New complaint: the LEFT arm 'tires and aches' with sustained use — combing "
            "hair, hanging washing — and the left hand often feels cold. Relieved by "
            "rest. No chest pain. Query musculoskeletal strain of the shoulder girdle.\n"
            "Advised simple analgesia and physiotherapy. (In retrospect this is upper-limb "
            "claudication.)"
        ),
    },
    {
        "id": "enc-2023-01-22-cardio",
        "date": "2023-01-22",
        "provider": "Dr. P. Gupta",
        "specialty": "Cardiology",
        "doc_type": "consult_note",
        "text": (
            "2023-01-22 — Cardiology (Dr. P. Gupta).\n"
            "Referred for palpitations and the left-arm symptoms. On examination the "
            "LEFT radial pulse is weak and the left-arm blood pressure is unrecordable; "
            "right arm 150/94 mmHg. A bruit is audible over the LEFT supraclavicular "
            "fossa (subclavian region). ECG sinus rhythm; echocardiogram shows mild "
            "concentric left-ventricular hypertrophy.\n"
            "ESR 59 mm/hr. Impression: probable left subclavian arterial narrowing of "
            "uncertain cause; recommend vascular imaging. Blood pressure to be measured "
            "in the right arm henceforth."
        ),
    },
    {
        "id": "enc-2023-06-30-nephro",
        "date": "2023-06-30",
        "provider": "Dr. N. Das",
        "specialty": "Nephrology / Hypertension",
        "doc_type": "consult_note",
        "text": (
            "2023-06-30 — Nephrology / Hypertension clinic (Dr. N. Das).\n"
            "Young woman with hypertension now poorly controlled on two agents "
            "(amlodipine and ramipril). Abdominal examination reveals a bruit in the "
            "epigastrium/flank. Creatinine mildly elevated at 96 µmol/L; potassium "
            "normal. ESR 55 mm/hr.\n"
            "Impression: resistant hypertension in a young patient — query renovascular "
            "hypertension / secondary cause. Arranged CT/MR angiography of aorta and "
            "renal arteries."
        ),
    },
    {
        "id": "enc-2023-10-12-neuro",
        "date": "2023-10-12",
        "provider": "Dr. L. Fernandes",
        "specialty": "Neurology",
        "doc_type": "consult_note",
        "text": (
            "2023-10-12 — Neurology (Dr. L. Fernandes).\n"
            "Transient blurring of vision, light-headedness on exertion, and one 'drop "
            "attack'. A bruit is heard over the RIGHT carotid artery. No fixed focal "
            "deficit. Query transient ischaemic attacks / vertebrobasilar insufficiency.\n"
            "Awaiting the angiography already requested by nephrology."
        ),
    },
    {
        "id": "enc-2024-02-08-radiology",
        "date": "2024-02-08",
        "provider": "Dr. V. Kulkarni",
        "specialty": "Radiology",
        "doc_type": "imaging_report",
        "text": (
            "2024-02-08 — CT/MR angiography, aorta and great vessels (Dr. V. Kulkarni).\n"
            "Findings: long-segment stenosis of the LEFT subclavian artery; stenosis of "
            "the LEFT common carotid artery; circumferential mural thickening of the "
            "aortic arch and descending thoracic aorta; stenosis of the RIGHT renal "
            "artery. No atheromatous calcification to suggest atherosclerosis in this age.\n"
            "Impression: multi-territory large-vessel arteritis with wall thickening and "
            "stenoses — appearances consistent with TAKAYASU ARTERITIS (Numano type V)."
        ),
    },
    {
        "id": "enc-2024-03-01-rheum-dx",
        "date": "2024-03-01",
        "provider": "Dr. S. Menon",
        "specialty": "Rheumatology",
        "doc_type": "consult_note",
        "text": (
            "2024-03-01 — Rheumatology (Dr. S. Menon) — DIAGNOSIS.\n"
            "Integrating the multi-year history of constitutional symptoms with "
            "persistently raised inflammatory markers, upper-limb claudication, absent "
            "left arm pulse/BP, multi-territory bruits and the angiographic findings, "
            "the diagnosis is CONFIRMED as Takayasu arteritis.\n"
            "Commenced prednisolone 1 mg/kg/day with a taper, plus methotrexate as a "
            "steroid-sparing agent; low-dose aspirin added; antihypertensives optimised. "
            "Given active disease and vascular involvement, IL-6 receptor blockade "
            "(tocilizumab) is planned and clinical-trial options are being explored."
        ),
    },
    {
        "id": "enc-2024-03-20-genetics",
        "date": "2024-03-20",
        "provider": "Dr. S. Menon",
        "specialty": "Immunogenetics",
        "doc_type": "lab_report",
        "text": (
            "2024-03-20 — HLA typing (immunology).\n"
            "HLA-B*52:01 positive. This allele is associated with Takayasu arteritis, "
            "particularly in Asian populations, and is consistent with the clinical "
            "diagnosis. Reported for completeness."
        ),
    },
    {
        "id": "enc-2024-09-15-rheum-fu",
        "date": "2024-09-15",
        "provider": "Dr. S. Menon",
        "specialty": "Rheumatology",
        "doc_type": "consult_note",
        "text": (
            "2024-09-15 — Rheumatology follow-up (Dr. S. Menon).\n"
            "On tapering prednisolone with tocilizumab. Symptomatically much improved; "
            "upper-limb claudication reduced. Inflammatory markers have normalised: "
            "ESR 18 mm/hr, CRP 4 mg/L. Blood pressure improved on current regimen.\n"
            "Referred to interventional radiology to consider angioplasty of the right "
            "renal artery. Continues to be assessed for enrolment in a biologic therapy "
            "trial for large-vessel vasculitis."
        ),
    },
]


# Golden expectations for the P1 ingestion/normalization eval gate. Codes are
# illustrative/synthetic targets the normalization layer should resolve to.
GOLDEN: Dict[str, object] = {
    "condition": {
        "name": "Takayasu arteritis",
        "status": "confirmed",
        "icd10": "M31.4",
        "snomed": "155441006",
        "orphanet": "ORPHA:3287",
        "omim": "207600",
        "date": "2024-03-01",
    },
    # Phenotype constellation → HPO (drives ConnectionsAgent matching in P2).
    "symptoms_hpo": {
        "Fatigue": "HP:0012378",
        "Fever": "HP:0001945",
        "Weight loss": "HP:0001824",
        "Arthralgia": "HP:0002829",
        "Night sweats": "HP:0030166",
        "Elevated ESR": "HP:0003565",
        "Elevated CRP": "HP:0011227",
        "Anemia": "HP:0001903",
        "Hypertension": "HP:0000822",
        "Intermittent claudication": "HP:0004417",
        "Reduced pulse": "HP:0025153",
        "Arterial bruit": "HP:0031955",
        "Vertigo": "HP:0002321",
        "Visual impairment": "HP:0000505",
    },
    "medications_rxnorm": {
        "Prednisolone": "8638",
        "Methotrexate": "6851",
        "Tocilizumab": "612865",
        "Aspirin": "1191",
        "Amlodipine": "17767",
        "Ramipril": "35296",
        "Ferrous sulfate": "type: iron salt",
    },
    "labs_loinc": {
        "ESR": "4537-7",
        "CRP": "1988-5",
        "Hemoglobin": "718-7",
        "Creatinine": "2160-0",
    },
    "gene_variants": [
        {"gene": "HLA-B", "variant": "HLA-B*52:01", "significance": "risk allele (Takayasu)"},
    ],
    "providers_expected": [
        "General Medicine", "Gynaecology", "Rheumatology", "Emergency",
        "Cardiology", "Nephrology / Hypertension", "Neurology", "Radiology",
    ],
    "true_diagnosis_date": "2024-03-01",
    "earliest_flaggable_date": "2022-08-05",
    "months_earlier": 18,
    # Minimum entities we expect ingestion to extract (graph-integrity snapshot).
    "min_counts": {
        "conditions": 1,
        "medications": 4,
        "labs": 4,
        "symptoms": 8,
        "providers": 6,
        "encounters": 10,
    },
}


def iter_documents() -> Iterator[Tuple[str, str, Dict[str, str]]]:
    """Yield (doc_id, text, metadata) for each encounter to remember()."""
    for enc in ENCOUNTERS:
        metadata = {
            "date": enc["date"],
            "provider": enc["provider"],
            "specialty": enc["specialty"],
            "doc_type": enc["doc_type"],
        }
        yield enc["id"], enc["text"], metadata


def encounter_dates() -> List[str]:
    return [enc["date"] for enc in ENCOUNTERS]
