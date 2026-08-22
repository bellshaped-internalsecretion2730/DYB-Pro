"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import AgentSwarm from "@/components/AgentSwarm";
import ProviderBadge from "@/components/ProviderBadge";
import ShortlistPanel from "@/components/ShortlistPanel";
import VersionDag from "@/components/VersionDag";
import {
  api,
  apiKey,
  setApiKey,
  type AgentRun,
  type Cycle,
  type Graph,
  type Observation,
  type Project,
  type Provider,
  type Shortlist,
} from "@/lib/api";

const TERMINAL = ["committed", "partial", "failed", "cancelled"];

export default function Workspace() {
  const [provider, setProvider] = useState<Provider | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [brief, setBrief] = useState("");
  const [cycle, setCycle] = useState<Cycle | null>(null);
  const [agents, setAgents] = useState<AgentRun[]>([]);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [timeline, setTimeline] = useState<Observation[]>([]);
  const [shortlist, setShortlist] = useState<Shortlist | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  const loadProjects = useCallback(async () => {
    const rows = await api.get<Project[]>("/projects");
    setProjects(rows);
    return rows;
  }, []);

  useEffect(() => {
    setKeyInput(apiKey());
    api.get<Provider>("/providers").then(setProvider).catch(fail);
    loadProjects()
      .then((rows) => {
        const demo = rows.find((p) => p.is_demo) || rows[0];
        if (demo) selectProject(demo);
      })
      .catch(fail);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadProjects]);

  const refreshProject = useCallback(async (id: string) => {
    const [p, g, t] = await Promise.all([
      api.get<Project>(`/projects/${id}`),
      api.get<Graph>(`/projects/${id}/graph`),
      api.get<Observation[]>(`/projects/${id}/timeline?limit=80`),
    ]);
    setProject(p);
    setGraph(g);
    setTimeline(t);
  }, []);

  const selectProject = useCallback(
    async (p: Project) => {
      setProject(p);
      setShortlist(null);
      setAgents([]);
      setBrief(p.goal);
      try {
        await refreshProject(p.id);
        const cycles = await api.get<Cycle[]>(`/projects/${p.id}/cycles`);
        if (cycles.length > 0) await attachCycle(cycles[0]);
      } catch (e) {
        fail(e);
      }
    },
    [refreshProject],
  );

  const attachCycle = useCallback(async (c: Cycle) => {
    setCycle(c);
    setAgents(await api.get<AgentRun[]>(`/cycles/${c.id}/agents`));
    if (TERMINAL.includes(c.status)) {
      try {
        setShortlist(await api.get<Shortlist>(`/cycles/${c.id}/shortlist`));
      } catch {
        setShortlist(null);
      }
    }
  }, []);

  // poll a running cycle
  useEffect(() => {
    if (!cycle || TERMINAL.includes(cycle.status)) return;
    const timer = setInterval(async () => {
      try {
        const fresh = await api.get<Cycle>(`/cycles/${cycle.id}`);
        setCycle(fresh);
        setAgents(await api.get<AgentRun[]>(`/cycles/${cycle.id}/agents`));
        if (project) await refreshProject(project.id);
        if (TERMINAL.includes(fresh.status)) {
          try {
            setShortlist(await api.get<Shortlist>(`/cycles/${cycle.id}/shortlist`));
          } catch {
            /* nothing committed */
          }
        }
      } catch (e) {
        fail(e);
      }
    }, 2500);
    return () => clearInterval(timer);
  }, [cycle, project, refreshProject]);

  async function seedDemo() {
    setBusy("seed");
    setError(null);
    try {
      const res = await api.post<{ project: Project }>("/demo/seed");
      const rows = await loadProjects();
      await selectProject(rows.find((p) => p.id === res.project.id) || res.project);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  async function upload(files: FileList | null) {
    if (!files || !project) return;
    setBusy("upload");
    setError(null);
    try {
      for (const file of Array.from(files)) {
        await api.upload(`/projects/${project.id}/uploads`, file);
      }
      await refreshProject(project.id);
      if (fileRef.current) fileRef.current.value = "";
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  async function runCycle() {
    if (!project) return;
    setBusy("cycle");
    setError(null);
    setShortlist(null);
    try {
      const created = await api.post<Cycle>(`/projects/${project.id}/cycles`, {
        brief: brief || project.goal,
        branch: "main",
      });
      await attachCycle(created);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  async function cancelCycle() {
    if (!cycle) return;
    try {
      await api.post(`/cycles/${cycle.id}/cancel`);
    } catch (e) {
      fail(e);
    }
  }

  const running = cycle !== null && !TERMINAL.includes(cycle.status);

  return (
    <>
      <header className="top">
        <div>
          <div className="brand">
            DYB<span> Pro</span>
          </div>
          <div className="tagline">pre-wetlab protein design OS</div>
        </div>
        <ProviderBadge provider={provider} />
        {provider?.openai_configured && <span className="badge">OpenAI analysis on</span>}
        <div className="grow" />
        <Link href="/pharmakon">Pharmakon drug programs →</Link>
        <input
          type="text"
          style={{ width: 220 }}
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          aria-label="API key"
        />
        <button
          className="secondary"
          type="button"
          onClick={() => {
            setApiKey(keyInput);
            window.location.reload();
          }}
        >
          use key
        </button>
      </header>

      <main>
        {error && (
          <div className="panel wide">
            <p className="err" style={{ margin: 0 }}>
              {error}
            </p>
          </div>
        )}

        <section className="panel">
          <h2>1 · Research brief</h2>
          <p className="hint">
            Say what you want in plain language, drop FASTA/PDB/CIF/CSV, then run a cycle. Three
            clicks: load demo → run cycle → export shortlist.
          </p>
          <div className="row" style={{ marginBottom: 8 }}>
            <select
              value={project?.id || ""}
              onChange={(e) => {
                const p = projects.find((x) => x.id === e.target.value);
                if (p) selectProject(p);
              }}
              style={{ maxWidth: 320 }}
            >
              <option value="">— select project —</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} {p.is_demo ? "(demo)" : ""}
                </option>
              ))}
            </select>
            <button className="secondary" type="button" onClick={seedDemo} disabled={busy === "seed"}>
              load demo project
            </button>
          </div>
          <textarea
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            placeholder="e.g. Improve GB1 thermal stability and solubility without losing predicted Fc binding."
          />
          <div className="row" style={{ marginTop: 8 }}>
            <input
              ref={fileRef}
              type="file"
              multiple
              accept=".fasta,.fa,.faa,.pdb,.ent,.cif,.mmcif,.csv,.tsv"
              onChange={(e) => upload(e.target.files)}
              disabled={!project || busy === "upload"}
            />
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button type="button" onClick={runCycle} disabled={!project || busy === "cycle" || running}>
              {running ? "cycle running…" : "run design cycle"}
            </button>
            {running && (
              <button className="secondary" type="button" onClick={cancelCycle}>
                cancel
              </button>
            )}
            {project && (
              <span className="muted">
                {project.commit_count} commits · {project.cycle_count} cycles · branches{" "}
                {project.branches.join(", ")}
              </span>
            )}
          </div>
        </section>

        <section className="panel">
          <h2>2 · Agent swarm</h2>
          <p className="hint">
            One Devin orchestrator plans the round and fans out to sequence, structure, docking,
            literature and ranking children.
          </p>
          <AgentSwarm cycle={cycle} agents={agents} />
        </section>

        <section className="panel">
          <h2>3 · Protein version DAG</h2>
          <p className="hint">
            Every design is an immutable commit: sequence, structure, scores, parent, agent, prompt
            and citations.
          </p>
          <VersionDag graph={graph} />
        </section>

        <section className="panel">
          <h2>Observation log</h2>
          <p className="hint">Append-only memory the next cycle reads before proposing designs.</p>
          <div className="timeline">
            {timeline.map((o) => (
              <div className="event" key={o.id}>
                <div className="kind">
                  {o.kind} · {o.role}
                </div>
                <div>{o.summary}</div>
              </div>
            ))}
            {timeline.length === 0 && <p className="muted">no observations yet</p>}
          </div>
        </section>

        <section className="panel wide">
          <h2>4 · Wet-lab shortlist</h2>
          <ShortlistPanel shortlist={shortlist} cycleId={cycle?.id ?? null} />
        </section>
      </main>
    </>
  );
}
