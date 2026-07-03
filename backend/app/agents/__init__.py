"""Ariadne agent swarm.

Each agent is a scoped set of Cognee recall(s) + light synthesis, run under its own
`session_id` (so the Cloud Sessions page attributes tokens/cost/feedback per agent),
returning cited `Finding`s and structured outputs from `app.models`.

Build order: Timeline -> Connections -> Trials -> Briefing -> Safety -> Justify.
"""

from app.agents.base import AgentError, BaseAgent  # noqa: F401
from app.agents.timeline import TimelineAgent, TimelineResult, build_timeline_events  # noqa: F401
from app.agents.connections import (  # noqa: F401
    ConnectionsAgent,
    ConnectionsResult,
    CandidateScore,
    build_candidate_index,
    patient_phenotype,
    rank_candidates,
)
from app.agents.trials import (  # noqa: F401
    TrialsAgent,
    TrialsResult,
    TrialRecord,
    Criterion,
    AgeConstraint,
    EligibilityVerdict,
    build_trial_index,
    evaluate_eligibility,
    hero_confirmed_conditions,
    parse_age_constraint,
    compute_age,
)
from app.agents.briefing import (  # noqa: F401
    BriefingAgent,
    BriefingResult,
    select_highlights,
    parse_open_questions,
)
from app.agents.safety import (  # noqa: F401
    SafetyAgent,
    SafetyResult,
    MedicationRecord,
    InteractionSignal,
    DuplicationSignal,
    build_medication_index,
    detect_interactions,
    detect_duplications,
    canonical_drug,
    drug_classes,
)
from app.agents.justify import (  # noqa: F401
    JustifyAgent,
    JustifyResult,
    select_prior_auth_drug,
    prior_therapy_drugs,
    confirmed_condition_display,
    REQUIRED_ELEMENTS,
)
