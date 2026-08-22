"use client";

import { useCallback, useEffect, useState } from "react";
import DaemonPane from "@/components/DaemonPane";
import LabelStudio from "@/components/LabelStudio";
import LearnedPane from "@/components/LearnedPane";
import ResearchFeed from "@/components/ResearchFeed";
import WetlabLoop from "@/components/WetlabLoop";
import {
  api,
  type CampaignOverview,
  type Commit,
  type LabDaemonStatus,
  type LabelsResponse,
  type Paper,
  type Proposal,
  type RiskReport,
  type WetlabPlan,
  type WetlabResult,
} from "@/lib/api";
import { research } from "@/lib/research";

/**
 * Research Module pane: the always-on daemon, the immutable research cache, the labelling UX
 * and the wet-lab loop for whichever version is in focus.
 */
export default function ResearchWorkspace({ projectId }: { projectId: string | null }) {
  const [overview, setOverview] = useState<CampaignOverview | null>(null);
  const [daemon, setDaemon] = useState<LabDaemonStatus | null>(null);
  const [papers, setPapers] = useState<Paper[]>([]);
  const [commits, setCommits] = useState<Commit[]>([]);
  const [commitId, setCommitId] = useState<string>("");
  const [labels, setLabels] = useState<LabelsResponse | null>(null);
  const [risk, setRisk] = useState<RiskReport | null>(null);
  const [plan, setPlan] = useState<WetlabPlan | null>(null);
  const [results, setResults] = useState<WetlabResult[]>([]);
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [unknownMetrics, setUnknownMetrics] = useState<string[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  const loadCampaign = useCallback(async (id: string) => {
    const [ov, ps, cs] = await Promise.all([
      research.overview(id),
      research.papers(id),
      api.get<Commit[]>(`/projects/${id}/commits`),
    ]);
    setOverview(ov);
    setDaemon(ov.daemon);
    setProposal(ov.proposal);
    setPapers(ps);
    setCommits(cs);
    return cs;
  }, []);

  const loadCommit = useCallback(async (id: string) => {
    const [lb, rk, pl, rs] = await Promise.all([
      research.labels(id),
      research.risk(id),
      research.latestPlan(id),
      research.results(id),
    ]);
    setLabels(lb);
    setRisk(rk);
    setPlan(pl);
    setResults(rs);
  }, []);

  useEffect(() => {
    if (!projectId) return;
    setError(null);
    loadCampaign(projectId)
      .then((cs) => {
        const head = cs[0];
        if (head) setCommitId(head.id);
      })
      .catch(fail);
  }, [projectId, loadCampaign]);

  useEffect(() => {
    if (!commitId) return;
    loadCommit(commitId).catch(fail);
  }, [commitId, loadCommit]);

  // keep the daemon pane live without hammering the whole campaign query
  useEffect(() => {
    if (!projectId) return;
    const timer = setInterval(() => {
      research.daemon(projectId).then(setDaemon).catch(() => undefined);
    }, 4000);
    return () => clearInterval(timer);
  }, [projectId]);

  const commit = commits.find((c) => c.id === commitId) || null;

  async function act<T>(tag: string, fn: () => Promise<T>): Promise<T | null> {
    setBusy(tag);
    setError(null);
    try {
      return await fn();
    } catch (e) {
      fail(e);
      return null;
    } finally {
      setBusy(null);
    }
  }

  const reload = useCallback(async () => {
    if (!projectId) return;
    await loadCampaign(projectId);
    if (commitId) await loadCommit(commitId);
  }, [projectId, commitId, loadCampaign, loadCommit]);

  if (!projectId) return null;

  return (
    <>
      {error && (
        <section className="panel wide">
          <p className="err" style={{ margin: 0 }}>
            {error}
          </p>
        </section>
      )}

      <section className="panel wide">
        <h2>5 · Research Daemon (always on)</h2>
        <div className="row" style={{ marginBottom: 10 }}>
          <select value={commitId} onChange={(e) => setCommitId(e.target.value)} style={{ maxWidth: 380 }}>
            <option value="">— select version —</option>
            {commits.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label} · r{c.cycle_round} · {c.short_id}
              </option>
            ))}
          </select>
          <button
            type="button"
            data-testid="seed-campaign"
            disabled={busy !== null || (overview?.digest.result_count ?? 0) > 0}
            onClick={() =>
              act("seed", async () => {
                const res = await research.seedCampaign(projectId);
                setDaemon(res.daemon);
                const cs = await loadCampaign(projectId);
                const head = cs[0];
                if (head) setCommitId(head.id);
              })
            }
          >
            {busy === "seed" ? "seeding campaign…" : "seed demo campaign"}
          </button>
          {overview && (
            <span className="muted" data-testid="campaign-summary">
              campaign “{overview.campaign.name}” · {overview.digest.version_count} versions ·{" "}
              {overview.digest.paper_count} papers · {overview.digest.result_count} result records
            </span>
          )}
        </div>
        <DaemonPane
          daemon={daemon}
          busy={busy}
          onRefresh={() =>
            act("refresh", async () => {
              const res = await research.refresh(projectId, commitId || undefined);
              setDaemon(res.daemon);
            })
          }
          onTick={() =>
            act("tick", async () => {
              const res = await research.tick(projectId, 3);
              setDaemon(res.daemon);
              await reload();
            })
          }
        />
      </section>

      <section className="panel">
        <h2>6 · Label residues</h2>
        <LabelStudio
          commit={commit}
          labels={labels}
          busy={busy}
          onCreate={async (body) => {
            await act("label", async () => {
              if (!commitId) return;
              const res = await research.addLabel(commitId, body);
              setDaemon(res.daemon);
              setLabels(await research.labels(commitId));
            });
          }}
        />
      </section>

      <section className="panel">
        <h2>7 · Research cache</h2>
        <ResearchFeed events={overview?.events || []} papers={papers} />
      </section>

      <section className="panel">
        <h2>8 · Wet-lab loop</h2>
        <WetlabLoop
          commit={commit}
          risk={risk}
          plan={plan}
          results={results}
          busy={busy}
          unknownMetrics={unknownMetrics}
          onPlan={async (host, maxAssays) => {
            await act("plan", async () => {
              if (!commitId) return;
              setPlan(await research.buildPlan(commitId, { host, max_assays: maxAssays }));
            });
          }}
          onSimulate={async (seed) => {
            await act("simulate", async () => {
              if (!plan || !commitId) return;
              await research.simulate(plan.id, seed);
              setResults(await research.results(commitId));
              await loadCampaign(projectId);
            });
          }}
          onSubmit={async (text, notes) => {
            await act("ingest", async () => {
              if (!commitId) return;
              const res = await research.submitResults(commitId, {
                text,
                notes,
                plan_id: plan?.id,
              });
              setUnknownMetrics(res.unknown_metrics);
              setResults(await research.results(commitId));
              await loadCampaign(projectId);
            });
          }}
        />
      </section>

      <section className="panel">
        <h2>9 · What we learned v1 → latest</h2>
        <LearnedPane
          learned={overview?.learned || null}
          proposal={proposal}
          busy={busy}
          onProposal={() =>
            act("proposal", async () => {
              setProposal(await research.proposal(projectId, commitId || undefined));
            })
          }
        />
      </section>
    </>
  );
}
