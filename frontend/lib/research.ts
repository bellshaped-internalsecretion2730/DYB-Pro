"use client";

import {
  api,
  type CampaignOverview,
  type LabDaemonStatus,
  type Drift,
  type Label,
  type LabelsResponse,
  type Learned,
  type MetricDefinitionOut,
  type Paper,
  type Proposal,
  type RiskReport,
  type SeedCampaignSummary,
  type WetlabPlan,
  type WetlabResult,
} from "@/lib/api";

/** Research Module + wet-lab loop calls, kept in one place so panes stay dumb. */
export const research = {
  overview: (projectId: string) =>
    api.get<CampaignOverview>(`/lab/projects/${projectId}/research`),
  daemon: (projectId: string) => api.get<LabDaemonStatus>(`/lab/projects/${projectId}/research/daemon`),
  refresh: (projectId: string, commitId?: string) =>
    api.post<{ task_id: string; status: string; daemon: LabDaemonStatus }>(
      `/lab/projects/${projectId}/research/refresh${commitId ? `?commit_id=${commitId}` : ""}`,
    ),
  tick: (projectId: string, limit = 2) =>
    api.post<{ processed: number; outcomes: unknown[]; daemon: LabDaemonStatus }>(
      `/lab/projects/${projectId}/research/tick?limit=${limit}`,
    ),
  seedCampaign: (projectId: string) =>
    api.post<SeedCampaignSummary>(`/lab/projects/${projectId}/research/seed-campaign`),
  papers: (projectId: string, limit = 60) =>
    api.get<Paper[]>(`/lab/projects/${projectId}/research/papers?limit=${limit}`),
  drift: (projectId: string) => api.get<Drift[]>(`/lab/projects/${projectId}/research/drift`),
  learned: (projectId: string) => api.get<Learned>(`/lab/projects/${projectId}/research/learned`),
  proposal: (projectId: string, commitId?: string) =>
    api.get<Proposal>(
      `/lab/projects/${projectId}/research/proposal${commitId ? `?commit_id=${commitId}` : ""}`,
    ),
  metrics: () => api.get<MetricDefinitionOut[]>("/lab/research/metrics"),

  labels: (commitId: string) => api.get<LabelsResponse>(`/lab/commits/${commitId}/labels`),
  addLabel: (
    commitId: string,
    body: { kind: string; name: string; residues: number[]; note: string; parent_label_id?: string },
  ) =>
    api.post<{ label: Label; daemon_task: { id: string; status: string }; daemon: LabDaemonStatus }>(
      `/lab/commits/${commitId}/labels`,
      body,
    ),

  risk: (commitId: string, host?: string) =>
    api.get<RiskReport>(
      `/lab/commits/${commitId}/wetlab/risk${host ? `?host=${encodeURIComponent(host)}` : ""}`,
    ),
  latestPlan: (commitId: string) => api.get<WetlabPlan | null>(`/lab/commits/${commitId}/wetlab/plan`),
  buildPlan: (commitId: string, body: { host: string; max_assays: number }) =>
    api.post<WetlabPlan>(`/lab/commits/${commitId}/wetlab/plan`, body),
  simulate: (planId: string, seed?: number) =>
    api.post<{ result: WetlabResult; daemon_task: { id: string; status: string } }>(
      `/lab/wetlab/plans/${planId}/simulate`,
      { seed: seed ?? null },
    ),
  results: (commitId: string) => api.get<WetlabResult[]>(`/lab/commits/${commitId}/wetlab/results`),
  submitResults: (
    commitId: string,
    body: { text?: string; rows?: Record<string, unknown>[]; plan_id?: string; notes?: string },
  ) =>
    api.post<{
      result: WetlabResult;
      unknown_metrics: string[];
      daemon_task: { id: string; status: string };
    }>(`/lab/commits/${commitId}/wetlab/results`, body),
};

export function fmt(value: number | boolean | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "yes" : "no";
  return Number.isInteger(value) ? String(value) : value.toFixed(digits);
}
