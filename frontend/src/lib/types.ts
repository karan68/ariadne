// Types mirroring the backend snapshot / agent `to_dict()` shapes.

export interface EvidenceRef {
  data_id: string | null;
  chunk_id: string | null;
  document_name: string | null;
  snippet: string | null;
  source_date?: string | null;
}

export interface Finding {
  id: string;
  kind: string;
  summary: string;
  confidence: string;
  confidence_score: number;
  evidence: EvidenceRef[];
  agent?: string;
  session_id?: string | null;
}

export interface TimelineEvent {
  date: string;
  type: string;
  description: string;
  evidence: EvidenceRef[];
}

export interface TimelineResult {
  dataset_name: string | null;
  session_id: string | null;
  used_search_type: string | null;
  since: string | null;
  span: [string, string] | null;
  events: TimelineEvent[];
  narrative: Finding | null;
}

export interface RankingRow {
  condition: string;
  score: number;
  overlap_count: number;
  matched_features: string[];
  vascular_features: string[];
  n_pattern_features: number;
}

export interface ConnectionsResult {
  clinical_dataset: string | null;
  literature_dataset: string | null;
  session_id: string | null;
  patient_hpo: string[];
  constellation: string[];
  top_condition: string | null;
  ranking: RankingRow[];
  candidates: Finding[];
  narrative: Finding | null;
}

export interface TrialMatch {
  nct_id: string;
  title: string;
  eligible: boolean;
  deciding_criterion: string | null;
  matched_criteria: string[];
  unmet_criteria: string[];
  confidence: string;
  evidence: EvidenceRef[];
}

export interface TrialsResult {
  clinical_dataset: string | null;
  trials_dataset: string | null;
  session_id: string | null;
  hero_age: number | null;
  hero_conditions: string[];
  eligible_ids: string[];
  ineligible_ids: string[];
  matches: TrialMatch[];
  narrative: Finding | null;
  suppressed_uncited: string[];
}

export interface SafetyAlert {
  kind: string;
  medications: string[];
  severity: string;
  rationale: string;
  evidence: EvidenceRef[];
}

export interface SafetyResult {
  clinical_dataset: string | null;
  session_id: string | null;
  medications: string[];
  alerts: SafetyAlert[];
  narrative: Finding | null;
  suppressed_uncited: string[];
}

export interface Brief {
  patient_id: string;
  generated_at: string;
  summary: string;
  timeline_highlights: TimelineEvent[];
  open_questions: string[];
  findings: Finding[];
}

export interface BriefingResult {
  clinical_dataset: string | null;
  session_id: string | null;
  event_count: number;
  suppressed: string[];
  brief: Brief;
}

export interface PriorAuthElement {
  key: string;
  label: string;
  content: string;
  satisfied: boolean;
  source: string;
  evidence: EvidenceRef[];
}

export interface JustifyResult {
  clinical_dataset: string | null;
  reference_dataset: string | null;
  session_id: string | null;
  requested_drug: string | null;
  indication: string | null;
  complete: boolean;
  missing_elements: string[];
  elements: PriorAuthElement[];
  narrative: Finding | null;
  suppressed_uncited: string[];
}

export interface TraceStep {
  date: string;
  new_features: string[];
  phenotype_count: number;
  vascular_features: string[];
  top_condition: string | null;
  top_score: number;
  top_is_clear: boolean;
  has_vascular: boolean;
  ranking: RankingRow[];
}

export interface TimeTravelResult {
  patient_id: string;
  true_diagnosis: string;
  true_diagnosis_date: string;
  constitutional_lead_date: string | null;
  first_flag_date: string | null;
  months_earlier: number;
  candidates: string[];
  literature_dataset: string | null;
  clinical_dataset: string | null;
  trace: TraceStep[];
}

export interface ThreadHop {
  source_id: string;
  target_id: string;
  relation: string;
  source_type: string;
  target_type: string;
  source_label: string;
  target_label: string;
}

export interface RedThread {
  anchor: { id: string; type: string; label: string };
  resolved: boolean;
  chunk_id: string | null;
  document_id: string | null;
  document_label: string | null;
  quote: string;
  hops: ThreadHop[];
}

export interface RedThreadBundle {
  condition: string;
  clinical_dataset: string | null;
  literature_dataset: string | null;
  all_edges_exist: boolean;
  n_patient_threads: number;
  n_literature_threads: number;
  unresolved_anchors: string[];
  patient_threads: RedThread[];
  literature_threads: RedThread[];
}

export interface AgentAttribution {
  agent: string;
  session_count: number;
  run_count: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  error_count: number;
  patients: string[];
  first_activity: string | null;
  last_activity: string | null;
}

export interface SessionsReport {
  range: string;
  total_sessions: number;
  agents_seen: string[];
  all_agents_attributed: boolean;
  tokens_total: number;
  stats: Record<string, number>;
  cost_by_model: Array<Record<string, unknown>>;
  by_agent: Record<string, AgentAttribution>;
}

export interface GraphNode { id: string; type: string; label: string }
export interface GraphEdge { source: string; target: string; relation: string }
export interface GraphView {
  n_nodes: number;
  n_edges: number;
  counts_by_type: Record<string, number>;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface RoleGrant {
  role: string;
  role_name: string;
  role_id: string | null;
  grants: Array<{ brain: string; dataset_id: string; permission: string; ok: boolean }>;
}

export interface RbacView {
  roles: string[];
  brains: string[];
  matrix: Record<string, Record<string, boolean>>;
  guarded: Record<string, Record<string, string[]>>;
  role_names: Record<string, string>;
  report: {
    tenant_id: string | null;
    user_id: string | null;
    roles: RoleGrant[];
    agents: Array<{ name: string; principal_id: string | null; brains: string[]; dataset_names: string[] }>;
  } | null;
}

export interface ImproveDemo {
  available: boolean;
  patient_id?: string;
  downvoted?: string | null;
  baseline?: Array<{ label: string; score: number }>;
  after_feedback?: Array<{ label: string; score: number }>;
  note?: string;
}

export interface ForgetProof {
  captured?: boolean;
  live?: boolean;
  dataset: string;
  scenario?: string;
  nodes_before: number;
  nodes_after: number;
  edges_before: number;
  edges_after: number;
  nodes_removed: number;
  probe_query: string;
  probe_before: string;
  probe_after: string;
  unrelated_query: string;
  unrelated_after: string;
  probe_present_before: boolean;
  probe_absent_after: boolean;
  unrelated_survives: boolean;
  forget_status: string;
  is_surgical: boolean;
  note?: string;
}

export interface Hero {
  id: string;
  display_name: string;
  sex: string;
  year_of_birth: string;
  context: string;
  true_diagnosis: string;
  true_diagnosis_date: string;
  earliest_flaggable_date: string;
  months_earlier: string | number;
}

export interface Snapshot {
  generated_at: string;
  patient_id: string;
  condition: string;
  live: boolean;
  datasets: Record<string, string | null>;
  hero: Hero;
  agents: {
    timeline: TimelineResult;
    connections: ConnectionsResult;
    trials: TrialsResult;
    safety: SafetyResult;
    briefing: BriefingResult;
    justify: JustifyResult;
  };
  timetravel: TimeTravelResult;
  redthread: RedThreadBundle;
  sessions: SessionsReport;
  graph: GraphView;
  rbac: RbacView;
  improve_demo: ImproveDemo;
  forget_demo: ForgetProof;
  build_log: string[];
}

export type Role = "owner" | "provider" | "family";
export type Door = "patient" | "clinician";
