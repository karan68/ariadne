"""Global reference knowledge for the ConnectionsAgent (literature) and TrialsAgent.

Two small, curated, demo-sized corpora ingested into global read-only Cognee Cloud
brains (`reference_literature`, `reference_trials`). Everything here is synthetic /
illustrative — trial NCT ids, thresholds and abstracts are fabricated for the demo
and must not be treated as real medical guidance.

Design intent:
- The hero patient is a young woman with years of constitutional symptoms + raised
  ESR/CRP + anemia, then a vascular phase (inter-arm BP discrepancy, bruits, absent
  radial pulse, claudication, renovascular hypertension), HLA-B*52:01. The correct
  literature pattern is **Takayasu arteritis (large-vessel vasculitis)**.
- The differentials below share *some* features (fever, raised ESR, arthralgia,
  weight loss) but each lacks the vascular signature — so ConnectionsAgent has to
  discriminate on the *constellation*, not any single symptom. This is what makes
  the cited red-thread convincing rather than a keyword match.
- Trials include studies the hero SHOULD match (confirmed large-vessel vasculitis,
  active disease by ESR/CRP, age/sex in range) and ones she should NOT (RA, SLE,
  paediatric-only, remission-only) so precision/recall is demonstrable.
"""

from __future__ import annotations

from typing import Dict, Iterator, List, Tuple

# --- Literature patterns (condition -> characteristic constellation) ---------

LITERATURE_PATTERNS: List[Dict[str, str]] = [
    {
        "id": "lit-takayasu",
        "condition": "Takayasu arteritis",
        "text": (
            "Takayasu arteritis — large-vessel vasculitis pattern.\n"
            "Takayasu arteritis is a chronic granulomatous large-vessel vasculitis that "
            "predominantly affects women under 40 years of age, with a markedly higher "
            "incidence in South Asian, East Asian and Latin American populations. It "
            "classically evolves in two phases. An early 'pre-pulseless' inflammatory "
            "phase presents with non-specific constitutional features: persistent "
            "fatigue, low-grade fever, night sweats, unintentional weight loss, "
            "arthralgia, and a normocytic anaemia of chronic disease, accompanied by a "
            "persistently elevated ESR and CRP over months to years. Because these "
            "features are non-specific, patients are frequently mislabelled as "
            "post-viral, iron deficiency, fibromyalgia, or anxiety, producing a long "
            "diagnostic delay. A later 'pulseless' vascular phase reveals the "
            "distinguishing signature: an inter-arm systolic blood-pressure difference "
            "greater than 10 mmHg, a diminished or absent radial or brachial pulse, "
            "arterial bruits over the carotid, subclavian or abdominal vessels, upper- "
            "or lower-limb claudication, and renovascular hypertension from renal-artery "
            "stenosis. HLA-B*52:01 is an established risk allele. Diagnosis is confirmed "
            "by cross-sectional angiography (CT or MR angiography, or PET) showing "
            "concentric wall thickening and stenoses of the aorta and its primary "
            "branches. Key red flags that should prompt vascular imaging: a young woman "
            "with more than 12 months of unexplained raised inflammatory markers who "
            "develops any inter-arm blood-pressure discrepancy, absent pulse, vascular "
            "bruit, or limb claudication.\n"
            "Distinguishing features versus mimics: large-vessel involvement with "
            "absent pulses and bruits (unlike adult-onset Still's disease or lupus); "
            "young age (unlike giant cell arteritis); systemic inflammation with "
            "elevated acute-phase reactants (unlike fibromuscular dysplasia)."
        ),
        "source": "Synthetic reference abstract (illustrative; not real guidance).",
    },
    {
        "id": "lit-gca",
        "condition": "Giant cell arteritis",
        "text": (
            "Giant cell arteritis — large-vessel vasculitis of older adults.\n"
            "Giant cell arteritis is a large- and medium-vessel vasculitis that almost "
            "exclusively affects adults over 50 years of age, with peak incidence after "
            "70. It shares large-vessel inflammation and a very high ESR and CRP with "
            "Takayasu arteritis, but the demographic and cranial features differ. "
            "Typical presentation: new-onset temporal headache, scalp tenderness, jaw "
            "claudication, and visual disturbance including amaurosis fugax or sudden "
            "painless vision loss, often with polymyalgia rheumatica (shoulder and hip- "
            "girdle stiffness). Temporal artery biopsy or temporal/axillary artery "
            "ultrasound supports the diagnosis. Distinguishing feature versus Takayasu: "
            "age over 50 with cranial ischaemic symptoms rather than a young woman with "
            "limb claudication and inter-arm pressure discrepancy. Age under 40 makes "
            "giant cell arteritis very unlikely."
        ),
        "source": "Synthetic reference abstract (illustrative; not real guidance).",
    },
    {
        "id": "lit-aosd",
        "condition": "Adult-onset Still's disease",
        "text": (
            "Adult-onset Still's disease — an autoinflammatory mimic.\n"
            "Adult-onset Still's disease is a systemic autoinflammatory disorder of "
            "young adults presenting with a characteristic quotidian (once-daily) spiking "
            "fever, an evanescent salmon-pink maculopapular rash that appears with fever "
            "spikes, arthralgia or frank arthritis, sore throat, and lymphadenopathy. "
            "Laboratory hallmarks are a markedly elevated serum ferritin (often more than "
            "five times the upper limit), neutrophilic leukocytosis, and raised ESR and "
            "CRP, typically with a negative ANA and rheumatoid factor. It shares fever, "
            "arthralgia and raised inflammatory markers with early Takayasu arteritis, "
            "but it does NOT cause absent pulses, arterial bruits, inter-arm blood- "
            "pressure discrepancy, or large-vessel stenosis. The salmon rash and very "
            "high ferritin are the discriminators; the absence of any vascular sign "
            "argues against a large-vessel vasculitis."
        ),
        "source": "Synthetic reference abstract (illustrative; not real guidance).",
    },
    {
        "id": "lit-sle",
        "condition": "Systemic lupus erythematosus",
        "text": (
            "Systemic lupus erythematosus — a multisystem autoimmune mimic.\n"
            "Systemic lupus erythematosus is a multisystem autoimmune disease that "
            "predominantly affects women of childbearing age and can present with "
            "constitutional symptoms (fatigue, fever, weight loss), arthralgia, and "
            "raised inflammatory markers, overlapping with the early phase of Takayasu "
            "arteritis. Distinguishing features include a malar (butterfly) facial rash, "
            "photosensitivity, oral ulcers, serositis, nephritis, cytopenias "
            "(leukopenia, lymphopenia, thrombocytopenia), and, importantly, a positive "
            "antinuclear antibody (ANA) with anti-double-stranded-DNA antibodies and low "
            "complement. Unlike Takayasu arteritis it does not typically cause absent "
            "peripheral pulses, large-vessel bruits, or an inter-arm blood-pressure "
            "discrepancy. Serology (ANA, anti-dsDNA, complement) is the key discriminator."
        ),
        "source": "Synthetic reference abstract (illustrative; not real guidance).",
    },
    {
        "id": "lit-ie",
        "condition": "Infective endocarditis",
        "text": (
            "Infective endocarditis — an infective mimic of chronic inflammation.\n"
            "Infective endocarditis can present sub-acutely with weeks to months of "
            "fever, night sweats, malaise, weight loss, anaemia and raised inflammatory "
            "markers, mimicking a systemic inflammatory disease. Discriminating features "
            "include a new or changing heart murmur, embolic phenomena (splinter "
            "haemorrhages, Janeway lesions, Osler nodes, splenic or cerebral emboli), and "
            "— critically — persistently positive blood cultures with vegetations on "
            "echocardiography. Unlike Takayasu arteritis, it does not cause large-artery "
            "stenosis with absent pulses or inter-arm pressure discrepancy, and blood "
            "cultures plus echocardiography establish the diagnosis. Any patient with an "
            "unexplained inflammatory illness and a murmur warrants blood cultures and "
            "echocardiography before immunosuppression."
        ),
        "source": "Synthetic reference abstract (illustrative; not real guidance).",
    },
    {
        "id": "lit-lymphoma",
        "condition": "Lymphoma",
        "text": (
            "Lymphoma — a malignant cause of B symptoms.\n"
            "Lymphoma, particularly Hodgkin and aggressive non-Hodgkin subtypes, can "
            "cause 'B symptoms': recurrent fever, drenching night sweats and "
            "unintentional weight loss, together with fatigue, anaemia and a raised ESR, "
            "overlapping with the constitutional phase of a large-vessel vasculitis. The "
            "discriminating features are persistent painless lymphadenopathy, "
            "hepatosplenomegaly, and an elevated LDH; diagnosis is by lymph-node biopsy "
            "and cross-sectional imaging. Unlike Takayasu arteritis, lymphoma does not "
            "produce absent peripheral pulses, arterial bruits, or inter-arm "
            "blood-pressure discrepancy. Lymphadenopathy and tissue biopsy are the key "
            "discriminators."
        ),
        "source": "Synthetic reference abstract (illustrative; not real guidance).",
    },
    {
        "id": "lit-fmd",
        "condition": "Fibromuscular dysplasia",
        "text": (
            "Fibromuscular dysplasia — a non-inflammatory vascular mimic.\n"
            "Fibromuscular dysplasia is a non-inflammatory, non-atherosclerotic arterial "
            "disease that predominantly affects young to middle-aged women and can cause "
            "renovascular hypertension, renal-artery stenosis with a 'string-of-beads' "
            "angiographic appearance, carotid or vertebral involvement, and bruits — "
            "overlapping with the vascular phase of Takayasu arteritis. The critical "
            "discriminator is the ABSENCE of systemic inflammation: ESR and CRP are "
            "normal and there are no constitutional symptoms. When a young woman has "
            "renovascular hypertension AND persistently elevated inflammatory markers "
            "with constitutional symptoms, a large-vessel vasculitis such as Takayasu is "
            "more likely than fibromuscular dysplasia."
        ),
        "source": "Synthetic reference abstract (illustrative; not real guidance).",
    },
]


# --- Clinical trials (with eligibility text) ---------------------------------

TRIALS: List[Dict[str, str]] = [
    {
        "id": "trial-nct-tak-toci",
        "nct_id": "NCT09000001",
        "condition": "Takayasu arteritis",
        "match_expectation": "match",  # hero should match
        "text": (
            "NCT09000001 — Tocilizumab for Active Takayasu Arteritis (TAKT-2).\n"
            "Phase 3, randomised, double-blind, placebo-controlled. Status: Recruiting.\n"
            "Conditions: Takayasu arteritis; large-vessel vasculitis.\n"
            "Inclusion criteria:\n"
            "- Age 18 to 65 years.\n"
            "- Diagnosis of Takayasu arteritis confirmed by CT or MR angiography.\n"
            "- Active disease, defined as ESR greater than 30 mm/hr or CRP greater than "
            "10 mg/L within the last 4 weeks.\n"
            "- At least one vascular feature: absent or diminished pulse, arterial bruit, "
            "limb claudication, or inter-arm systolic blood-pressure difference greater "
            "than 10 mmHg.\n"
            "Exclusion criteria:\n"
            "- Active or chronic infection, including tuberculosis or hepatitis B or C.\n"
            "- Pregnancy or breastfeeding.\n"
            "- Prior tocilizumab within 12 weeks."
        ),
    },
    {
        "id": "trial-nct-lvv-registry",
        "nct_id": "NCT09000002",
        "condition": "Large-vessel vasculitis",
        "match_expectation": "match",
        "text": (
            "NCT09000002 — Longitudinal Registry of Large-Vessel Vasculitis (LOVELY).\n"
            "Observational cohort. Status: Recruiting.\n"
            "Conditions: Takayasu arteritis; giant cell arteritis; large-vessel "
            "vasculitis.\n"
            "Inclusion criteria:\n"
            "- Age 18 years or older.\n"
            "- Physician-confirmed large-vessel vasculitis (Takayasu arteritis or giant "
            "cell arteritis) on imaging or biopsy.\n"
            "- Willing to provide blood samples and imaging follow-up.\n"
            "Exclusion criteria:\n"
            "- Isolated small-vessel vasculitis.\n"
            "- Unable to undergo MR or CT angiography."
        ),
    },
    {
        "id": "trial-nct-jak-tak",
        "nct_id": "NCT09000003",
        "condition": "Takayasu arteritis",
        "match_expectation": "match",
        "text": (
            "NCT09000003 — JAK Inhibition in Refractory Takayasu Arteritis (JAK-TAK).\n"
            "Phase 2, open-label. Status: Recruiting.\n"
            "Conditions: Takayasu arteritis.\n"
            "Inclusion criteria:\n"
            "- Female or male, age 18 to 60 years.\n"
            "- Confirmed Takayasu arteritis with active or relapsing disease despite "
            "corticosteroids.\n"
            "- Elevated acute-phase reactants (ESR or CRP above the upper limit of "
            "normal).\n"
            "Exclusion criteria:\n"
            "- Serious infection within 8 weeks.\n"
            "- Estimated GFR below 40 mL/min.\n"
            "- Current malignancy."
        ),
    },
    {
        "id": "trial-nct-gca-only",
        "nct_id": "NCT09000004",
        "condition": "Giant cell arteritis",
        "match_expectation": "no-match",  # age/condition mismatch for the hero
        "text": (
            "NCT09000004 — Upadacitinib in Giant Cell Arteritis (GACE).\n"
            "Phase 3, randomised. Status: Recruiting.\n"
            "Conditions: Giant cell arteritis.\n"
            "Inclusion criteria:\n"
            "- Age 50 years or older.\n"
            "- New or relapsing giant cell arteritis confirmed by biopsy or imaging, "
            "with cranial symptoms or polymyalgia rheumatica.\n"
            "Exclusion criteria:\n"
            "- Age under 50 years.\n"
            "- Takayasu arteritis or other non-GCA vasculitis."
        ),
    },
    {
        "id": "trial-nct-sle-only",
        "nct_id": "NCT09000005",
        "condition": "Systemic lupus erythematosus",
        "match_expectation": "no-match",
        "text": (
            "NCT09000005 — Anifrolumab for Moderate-to-Severe Systemic Lupus "
            "Erythematosus (LUPUS-LIGHT).\n"
            "Phase 3, randomised, placebo-controlled. Status: Recruiting.\n"
            "Conditions: Systemic lupus erythematosus.\n"
            "Inclusion criteria:\n"
            "- Age 18 years or older.\n"
            "- Diagnosis of systemic lupus erythematosus meeting classification criteria "
            "with a positive antinuclear antibody (ANA) titre of 1:80 or higher.\n"
            "- Active disease by SLEDAI score.\n"
            "Exclusion criteria:\n"
            "- Active large-vessel vasculitis as the primary diagnosis.\n"
            "- Negative ANA."
        ),
    },
    {
        "id": "trial-nct-paeds-only",
        "nct_id": "NCT09000006",
        "condition": "Takayasu arteritis",
        "match_expectation": "no-match",  # right disease, wrong age band
        "text": (
            "NCT09000006 — Biologic Therapy for Childhood Takayasu Arteritis "
            "(KID-TAK).\n"
            "Phase 2, open-label. Status: Recruiting.\n"
            "Conditions: Takayasu arteritis; paediatric vasculitis.\n"
            "Inclusion criteria:\n"
            "- Age 5 to 17 years at enrolment.\n"
            "- Confirmed Takayasu arteritis.\n"
            "Exclusion criteria:\n"
            "- Age 18 years or older.\n"
            "- Pregnancy."
        ),
    },
]


# --- Iteration helpers (mirror odyssey_patient.iter_documents) ---------------

def iter_literature() -> Iterator[Tuple[str, str, Dict[str, str]]]:
    """Yield (doc_id, text, metadata) for each literature pattern."""
    for pat in LITERATURE_PATTERNS:
        meta = {"kind": "literature", "condition": pat["condition"], "source": pat["source"]}
        yield pat["id"], pat["text"], meta


def iter_trials() -> Iterator[Tuple[str, str, Dict[str, str]]]:
    """Yield (doc_id, text, metadata) for each trial."""
    for tr in TRIALS:
        meta = {
            "kind": "trial",
            "nct_id": tr["nct_id"],
            "condition": tr["condition"],
            "match_expectation": tr["match_expectation"],
        }
        yield tr["id"], tr["text"], meta


def literature_conditions() -> List[str]:
    return [p["condition"] for p in LITERATURE_PATTERNS]


def trial_nct_ids() -> List[str]:
    return [t["nct_id"] for t in TRIALS]


# Golden expectations for the reference eval (which patterns/trials the hero,
# a young woman with confirmed Takayasu + active inflammation + vascular signs,
# should surface vs. must not).
REFERENCE_GOLDEN: Dict[str, object] = {
    "literature_top_condition": "Takayasu arteritis",
    "literature_min_patterns": 6,
    "trials_min": 6,
    "trials_should_match": ["NCT09000001", "NCT09000002", "NCT09000003"],
    "trials_should_not_match": ["NCT09000004", "NCT09000005", "NCT09000006"],
}
