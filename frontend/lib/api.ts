"use client";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://localhost:8000";

const DEFAULT_KEY = process.env.NEXT_PUBLIC_DEMO_API_KEY || "dyb-pro-demo-scientist";

export function apiKey(): string {
  if (typeof window === "undefined") return DEFAULT_KEY;
  return window.localStorage.getItem("dyb-pro.apiKey") || DEFAULT_KEY;
}

export function setApiKey(key: string) {
  window.localStorage.setItem("dyb-pro.apiKey", key);
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}/api${path}`, {
    ...init,
    headers: { "X-API-Key": apiKey(), ...(init.headers || {}) },
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
  downloadUrl: (path: string) => `${API_BASE}/api${path}`,
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
  primers: { mutation: string; forward: string; reverse: string; tm_c: number }[];
  assay_plan: { assay: string; readout: string; predicted_signal: string; estimated_cost_usd: number }[];
  cost: { route: string; total_usd: number };
  why: string;
  why_not_next?: string | null;
  citations: string[];
};

export type Economics = {
  shortlist_size: number;
  candidate_pool: number;
  shortlist_cost_usd: number;
  test_everything_cost_usd: number;
  savings_usd: number;
  savings_pct: number;
  expected_hits: number;
};

export type Shortlist = {
  pack: {
    project: string;
    shortlist: ShortlistItem[];
    economics: Economics;
    risks: { label: string; notes: string[] }[];
    citations: string[];
  };
  ranked: RankedCandidate[];
  narrative: { headline: string; body?: string; source?: string };
  commits: { label: string; commit: string }[];
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

export type DaemonStatus = {
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
  daemon: DaemonStatus;
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
