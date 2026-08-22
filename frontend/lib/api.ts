"use client";

const DIRECT_API_BASE = process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "";
export const API_BASE = DIRECT_API_BASE || "/api/dyb-pro";

const DEFAULT_KEY = DIRECT_API_BASE
  ? process.env.NEXT_PUBLIC_DEMO_API_KEY || ""
  : "";

export function apiKey(): string {
  if (typeof window === "undefined") return DEFAULT_KEY;
  return window.localStorage.getItem("dyb-pro.apiKey") || DEFAULT_KEY;
}

export function setApiKey(key: string) {
  window.localStorage.setItem("dyb-pro.apiKey", key);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const key = apiKey();
  if (DIRECT_API_BASE && key && !headers.has("X-API-Key")) headers.set("X-API-Key", key);
  if (!DIRECT_API_BASE && key) headers.set("X-API-Key", key);
  const res = await fetch(`${API_BASE}${DIRECT_API_BASE ? `/api${path}` : path}`, {
    ...init,
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    let detail = await res.text();
    try {
      detail = JSON.stringify(JSON.parse(detail).detail ?? detail);
    } catch {
      /* plain text */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  upload: async <T,>(path: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<T>(path, { method: "POST", body: form });
  },
  downloadUrl: (path: string) => `${API_BASE}${DIRECT_API_BASE ? `/api${path}` : path}`,
};

export type Provider = {
  provider: string | null;
  devin_configured: boolean;
  devin_reachable?: boolean;
  devin_api_flavor: string;
  local_simulation_allowed: boolean;
  openai_configured: boolean;
  error?: string | null;
};

export type Project = {
  id: string;
  name: string;
  goal: string;
  target_name?: string | null;
  is_demo: boolean;
  commit_count: number;
  cycle_count: number;
  branches: string[];
  head_commit_id?: string | null;
};

export type Cycle = {
  id: string;
  project_id: string;
  status: string;
  provider?: string | null;
  round: number;
  branch: string;
  brief: string;
  summary?: string | null;
  error?: string | null;
  acu_limit: number;
  acus_used: number;
  orchestrator_session_url?: string | null;
  plan: { strategy?: string; agents?: { role: string; task: string }[] };
};

export type AgentRun = {
  id: string;
  role: string;
  task: string;
  provider: string;
  status: string;
  devin_status?: string | null;
  devin_session_url?: string | null;
  tags: string[];
  attempts: number;
  acus: number;
  acu_limit: number;
  error?: string | null;
  log: { at: string; message: string }[];
};

export type GraphNode = {
  id: string;
  short_id: string;
  label?: string | null;
  branch: string;
  message?: string | null;
  agent_role?: string | null;
  provider?: string | null;
  devin_session_url?: string | null;
  mutations: (string | null)[];
  scores: Record<string, number>;
  passed_filters: boolean;
  failed_filters: string[];
  cycle_round?: number | null;
  is_head: boolean;
  head_of?: string | null;
  rationale?: string | null;
};

export type Graph = {
  nodes: GraphNode[];
  edges: { source: string; target: string }[];
  branches: { name: string; head: string | null; head_short: string }[];
};

export type RankedCandidate = {
  label: string;
  rank: number;
  composite_score: number;
  confidence: number;
  pareto_optimal: boolean;
  passed_filters: boolean;
  failed_filters: string[];
  scores: Record<string, number>;
  uncertainty: Record<string, number>;
  why: string;
  why_not_next?: string | null;
  excluded_reason?: string | null;
  missing_objectives?: string[];
  confidence_meaning?: string;
};

export type ShortlistItem = {
  rank: number;
  label: string;
  sequence: string;
  mutations: (string | null)[];
  composite_score: number;
  confidence: number;
  pareto_optimal: boolean;
  construct: { vector: string; orf_length_bp: number; orf: string; expression_host: string };
  primers: {
    mutation: string;
    forward: string;
    reverse: string;
    tm_c: number;
    template_source: string;
    orderable: boolean;
  }[];
  assay_plan: {
    assay: string;
    readout: string;
    tests_in_silico_proxy: string;
    in_silico_value: number | null;
    in_silico_units: string;
    decision_rule: string;
    estimated_cost_usd: number;
  }[];
  cost: { route: string; total_usd: number };
  geometry_usable: boolean;
  why: string;
  why_not_next?: string | null;
  citations: string[];
};

export type Economics = {
  shortlist_size: number;
  candidate_pool: number;
  cost_per_candidate_usd: number;
  shortlist_cost_usd: number;
  not_shortlisted: number;
  spend_avoided_usd: number;
  basis: string;
  caveat: string;
  cost_exclusions: string[];
};

/** What is measured, not predicted: a null hit rate means nothing has been measured yet. */
export type ValidationStatus = {
  measured_results: number;
  measured_hit_rate: number | null;
  proxy_agreement: Record<string, { kendall_tau: number | null; pairs: number; note?: string }>;
  note: string;
};

export type Shortlist = {
  pack: {
    project: string;
    shortlist: ShortlistItem[];
    economics: Economics;
    validation: ValidationStatus;
    template_source: string;
    primers_orderable: boolean;
    risks: { label: string; notes: string[] }[];
    citations: string[];
  };
  ranked: RankedCandidate[];
  narrative: { headline: string; body?: string; source?: string };
  commits: { label: string; commit: string }[];
};

export type ResearchFinding = {
  topic_key: string;
  topic: string;
  from_cache: boolean;
  provider: string;
  findings: { claim?: string; citation?: string; implication?: string }[];
};

// ----------------------------------------------------------------- Pharmakon

export type DrugProgram = {
  id: string;
  project_id: string;
  name: string;
  target_name: string;
  indication: string;
  objective: string;
  current_stage: string;
  stage_order: number;
  stage_name: string;
  autonomy_level: number;
  autonomy_label: string;
  status: string;
  provider: string;
  rounds_run: number;
  stage_cycles: number;
  acus_used: number;
  assays_ingested: number;
  molecule_count: number;
  daemon_enabled: boolean;
  knowledge_note_id?: string | null;
  candidate_molecule_id?: string | null;
  created_at: string;
  last_research_at?: string | null;
};

export type GateCriterion = {
  key: string;
  label: string;
  requirement: string;
  observed: number | null;
  passed: boolean;
  blocking: boolean;
  weight: number;
  missing_evidence: boolean;
};

export type GateDecision = {
  id: string;
  stage: string;
  decision: "go" | "no_go" | "recycle" | "kill";
  score: number;
  criteria: GateCriterion[];
  blocking_failures: GateCriterion[];
  rationale: string;
  recommended_actions: string[];
  next_stage?: string | null;
  autonomy_level: number;
  requires_approval: boolean;
  approval_reason: string;
  approval_status: string;
  approval_note: string;
  applied: boolean;
  created_at: string;
};

export type MoleculeEvaluation = {
  descriptors?: { molecular_weight?: number; clogp?: number; tpsa?: number; qed_like?: number };
  binding?: { pkd?: number; kd_nm?: number; basis?: string };
  admet?: { admet_score?: number };
  synthesis?: { sa_score?: number; cost_per_gram_usd?: number };
  liabilities?: { blocking?: string[]; alerts?: { name: string }[] };
};

export type ProgramMolecule = {
  id: string;
  short_id: string;
  label: string;
  smiles: string;
  formula: string;
  scaffold_key: string;
  stage: string;
  composite_score: number;
  verdict: string;
  rationale: string;
  agent_role: string;
  provider: string;
  devin_session_url?: string | null;
  evaluation: MoleculeEvaluation;
};

export type ProgramExperiment = {
  id: string;
  stage: string;
  assay: string;
  endpoint: string;
  unit: string;
  gate_metric: string;
  molecules: string[];
  predicted_value: number | null;
  falsification: string;
  cost_usd: number;
  turnaround_days: number;
  blocking: boolean;
  priority: number;
  status: string;
};

export type ResearchEvent = {
  id: string;
  project_id: string;
  status: "queued" | "running" | "done" | "failed";
  trigger: string;
  triggers: { trigger: string; ref?: string | null; detail?: string; at: string }[];
  coalesced: number;
  summary: string;
  changed: Record<string, unknown>;
  findings: ResearchFinding[];
  metrics: Record<string, unknown>;
  drift: Record<string, unknown>;
  cache_hits: number;
  cache_writes: number;
  provider: string;
  devin_session_url?: string | null;
  acus: number;
  error?: string | null;
  created_at: string;
  finished_at?: string | null;
};

export type ResearchNote = {
  id: string;
  topic_key: string;
  topic: string;
  question: string;
  findings: { claim?: string; citation?: string; implication?: string }[];
  citations: string[];
  provider: string;
  devin_session_url?: string | null;
  reuse_count: number;
  created_at: string;
  last_used_at: string;
};

export type DaemonStatus = {
  enabled: boolean;
  provider: string | null;
  debounce_seconds: number;
  tick_seconds: number;
  queued: number;
  running: number;
  failed: number;
  last_event_at?: string | null;
  cached_topics: number;
};

export type ProjectResearch = {
  daemon: DaemonStatus;
  session: { session_url?: string | null; status: string; messages_sent: number } | null;
  events: ResearchEvent[];
  notes: ResearchNote[];
  cache: { topics: number; reuses: number; note: string };
};

export type ProgramResearchEvent = {
  id: string;
  role: string;
  kind: string;
  claim: string;
  citation: string;
  implication: string;
  provider: string;
  created_at: string;
};

export type ProgramEconomics = {
  autonomous: { total_cost_usd: number; months_elapsed: number; acus_used: number };
  human_baseline: { total_cost_usd: number; months: number; source: string };
  delta: { cost_usd_saved: number; cost_ratio: number | null; months_saved: number };
  forward_look: {
    remaining_stages: string[];
    probability_of_reaching_fih: number;
    risk_adjusted_value_usd: number;
    caveat: string;
  };
};

export type ProgramDaemonStatus = {
  devin_configured: boolean;
  devin_schedules_supported: boolean;
  mode: string;
  note?: string;
  error?: string;
  schedules: { name?: string; enabled?: boolean; frequency?: string; last_executed_at?: string }[];
};

export type ProgramDetail = {
  program: DrugProgram;
  stage: { key: string; name: string; question: string; order: number };
  ladder: { key: string; name: string; order: number }[];
  latest_gate: GateDecision | null;
  gate_history: { stage_key: string; decision: string; score: number; approval_status: string }[];
  drift: PredictionDrift;
  economics: ProgramEconomics;
  daemon: ProgramDaemonStatus;
  next_action: { action: string; reason: string; stage?: string | null };
};

export type RoundSummary = {
  id: string;
  stage: string;
  number: number;
  status: string;
  provider: string;
  summary: string;
  error?: string | null;
  acus_used: number;
  metrics: Record<string, number>;
  findings: Record<string, unknown>;
  gate: Partial<GateDecision>;
  experiment_plan: {
    proposals: ProgramExperiment[];
    evidence_tasks: { metric: string; label: string; requirement: string; settled_by: string }[];
    total_cost_usd: number;
    critical_path_days: number;
  };
  orchestrator_session_url?: string | null;
  created_at: string;
};

export type PredictionDrift = {
  n: number;
  rmse: number | null;
  bias: number | null;
  pairs: { molecule_hash: string; predicted_pkd: number; observed_pkd: number }[];
  worst: { molecule_hash: string; predicted: number; observed: number } | null;
  interpretation: string;
  method: string;
};

export type Observation = {
  id: string;
  role: string;
  kind: string;
  summary: string;
  cycle_id?: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export type Commit = {
  id: string;
  short_id: string;
  project_id: string;
  branch: string;
  label: string;
  message: string;
  sequence: string;
  structure_key?: string | null;
  structure_source?: string;
  parent_ids?: string[];
  mutations: { mutation?: string | null }[];
  scores: Record<string, number>;
  uncertainty: Record<string, number>;
  filters?: Record<string, unknown>;
  rationale: string;
  agent_role: string;
  provider: string;
  devin_session_url?: string | null;
  citations?: unknown[];
  cycle_round: number;
  created_at: string;
};

// ------------------------------------------------------ research lab loop types
// The /lab surface: cached papers, residue labels, wet-lab plans/results, drift models and the
// proposal they feed. Distinct from the project research daemon above, hence the Lab* prefixes.

export type DaemonTask = {
  id: string;
  kind: string;
  status: string;
  commit_id?: string | null;
  coalesced_count: number;
  attempts: number;
  error?: string | null;
  created_at: string;
  run_after: string;
  processed_at?: string | null;
  event_id?: string | null;
};

export type LabDaemonStatus = {
  campaign_id: string;
  project_id: string;
  name: string;
  status: string;
  detail: string;
  provider: string;
  devin_session_url?: string | null;
  heartbeat_at?: string | null;
  knowledge_version: number;
  event_sequence: number;
  queue: Record<string, number>;
  queue_depth: number;
  tick_seconds: number;
  debounce_seconds: number;
  tasks: DaemonTask[];
};

export type LabResearchEvent = {
  id: string;
  sequence_no: number;
  kind: string;
  trigger: string;
  role: string;
  summary: string;
  payload: Record<string, unknown>;
  skills_used: string[];
  citations: string[];
  provider: string;
  devin_session_url?: string | null;
  commit_id?: string | null;
  created_at: string;
};

export type Paper = {
  paper_key: string;
  title: string;
  doi?: string | null;
  year?: number | null;
  venue?: string | null;
  url?: string | null;
  source: string;
  citation: string;
  citation_count: number;
  relevance: number;
  extracted_metrics: { name: string; value: number; unit: string; context: string }[];
  query: string;
  first_seen_at: string;
};

export type Drift = {
  metric: string;
  n_observations: number;
  bias: number;
  slope: number;
  intercept: number;
  residual_sd: number;
  rmse: number;
  history: { residual?: number; predicted?: number; measured?: number }[];
  method: string;
  updated_at: string;
};

export type Learned = {
  campaign_id: string;
  headline: string;
  lessons: string[];
  drift: {
    metric: string;
    n: number;
    bias: number;
    rmse: number;
    first_abs_residual: number | null;
    latest_abs_residual: number | null;
    shrinking: boolean;
  }[];
  versions: {
    label: string;
    commit_id: string;
    mutations: string[];
    scores: Record<string, number>;
    measured: Record<string, number | boolean>;
  }[];
  paper_count: number;
  hypotheses: { supported: number; refuted: number; open: number };
  generated_at: string;
};

export type DigestVersion = {
  commit_id: string;
  short: string;
  label: string;
  created_at?: string | null;
  mutations: string[];
  scores: Record<string, number>;
  measured: Record<string, number | boolean>;
  labels: { kind: string; name: string; residues: number[]; note: string }[];
  provider: string;
};

export type SeedCampaignSummary = {
  campaign_id: string;
  seeded: boolean;
  reason?: string;
  versions?: { id: string; label: string }[];
  papers?: number;
  drift_metrics?: Record<string, { n: number; bias: number; rmse: number }>;
  lab_bias?: Record<string, number>;
  daemon: LabDaemonStatus;
};

export type CampaignOverview = {
  campaign: {
    id: string;
    project_id: string;
    name: string;
    question: string;
    knowledge_version: number;
    daemon_status: string;
  };
  daemon: LabDaemonStatus;
  digest: {
    version_count: number;
    paper_count: number;
    result_count: number;
    versions: DigestVersion[];
    head: DigestVersion | null;
    drift: { metric: string; n: number; bias: number; rmse: number }[];
  };
  learned: Learned;
  proposal: Proposal | null;
  events: LabResearchEvent[];
};

export type LabelNode = {
  id: string;
  kind: string;
  name: string;
  residues: number[];
  note: string;
  version: number;
  superseded: boolean;
  created_at?: string | null;
  refinements: LabelNode[];
};

export type LabelsResponse = {
  commit_id: string;
  sequence_length: number;
  labels: Label[];
  tree: LabelNode[];
};

export type MetricDefinitionOut = {
  name: string;
  unit: string;
  higher_is_better: boolean;
  assay: string;
  assay_sd: number;
  sd_kind: string;
  pass_rule: string;
  skill: string;
  citations: string[];
};

export type RiskReport = {
  commit_id: string;
  predictions: Record<string, Prediction>;
  skills_used: string[];
  risk: { risks: Risk[]; failure_modes: string[] };
};

export type Label = {
  id: string;
  kind: string;
  name: string;
  residues: number[];
  note: string;
  version: number;
  parent_label_id?: string | null;
  superseded_by?: string | null;
  created_at: string;
};

export type Prediction = {
  value: number | boolean | null;
  sd: number;
  unit: string;
  method: string;
  skill: string;
  calibrated?: boolean;
};

export type Risk = { risk: string; level: string; detail: string; mitigation: string };

export type WetlabPlan = {
  id: string;
  commit_id: string;
  status: string;
  risk: { risks: Risk[]; failure_modes: string[] };
  predictions: Record<string, Prediction>;
  constructs: {
    label: string;
    vector: string;
    orf: string;
    expression_host: string;
    route: string;
    notes: string;
    build_cost_usd: number;
  }[];
  assays: { assay: string; measures: string[]; cost_usd: number; days: number; readout: string }[];
  controls: { control: string; why: string }[];
  thresholds: { metric: string; accept: string; reject: string }[];
  failure_modes: string[];
  total_cost_usd: number;
  information_per_usd: number;
  rationale: string;
  skills_used: string[];
  citations: string[];
  provider: string;
};

export type WetlabResult = {
  id: string;
  commit_id: string;
  plan_id?: string | null;
  source: string;
  construct_label: string;
  measurements: { metric: string; value: number | boolean | null; unit: string; assay: string; passed?: boolean | null }[];
  residuals: Record<string, { predicted: number; measured: number; residual: number; z?: number }>;
  error_model: { description: string; seed?: number | null };
  notes: string;
  operator: string;
  created_at: string;
};

export type Proposal = {
  proposed: {
    label: string;
    mutations: string[];
    sequence: string;
    rationale: string;
    scores: Record<string, number>;
    developability_index?: number | null;
    predicted_wetlab: Record<string, Prediction>;
    predicted_delta: Record<string, number>;
    citations: string[];
  } | null;
  target_metric: string;
  reason?: string;
  why_this_metric?: string;
  ranking?: { label: string; score: number }[];
  considered?: { label: string; mutations: string[]; passed_filters: boolean; rationale: string }[];
  excluded_by_history?: string[];
  protected_residues?: number[];
  skills_used?: string[];
};
