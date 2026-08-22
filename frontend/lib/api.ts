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

export type Observation = {
  id: string;
  role: string;
  kind: string;
  summary: string;
  cycle_id?: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};
