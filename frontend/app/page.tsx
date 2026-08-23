"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import AgentSwarm from "@/components/AgentSwarm";
import AskPane from "@/components/AskPane";
import CommandPalette, { type Command } from "@/components/CommandPalette";
import DatabaseSearch from "@/components/DatabaseSearch";
import FoldStrip from "@/components/FoldStrip";
import IconRail, { RAIL_SECTIONS, type RailSection } from "@/components/IconRail";
import MetricStrip from "@/components/MetricStrip";
import ProteinViewer from "@/components/ProteinViewer";
import ProviderBadge from "@/components/ProviderBadge";
import ResearchPane from "@/components/ResearchPane";
import ResearchWorkspace from "@/components/ResearchWorkspace";
import SequenceLoader from "@/components/SequenceLoader";
import ShortlistPanel from "@/components/ShortlistPanel";
import StructureViewer from "@/components/StructureViewer";
import VersionDag from "@/components/VersionDag";
import {
  api,
  apiKey,
  setApiKey,
  type AgentRun,
  type Cycle,
  type Graph,
  type GraphNode,
  type Observation,
  type Project,
  type ProjectResearch,
  type Provider,
  type Shortlist,
} from "@/lib/api";

const TERMINAL = ["committed", "partial", "failed", "cancelled"];
type CenterTab = "structure" | "lineage" | "shortlist" | "research" | "lab" | "data";

const CENTER_TABS: [CenterTab, string, string][] = [
  ["structure", "structure", "the selected version in 3D, with its sequence and scores"],
  ["lineage", "lineage", "every design as an immutable commit: parent, agent, prompt, citations"],
  ["shortlist", "shortlist", "ranked wet-lab candidates for the attached cycle"],
  ["research", "research", "the always-live research daemon and the append-only observation log"],
  ["lab", "lab", "labels, evidence, wet-lab loop and what the campaign learned"],
  ["data", "data", "public database search with the upstream source shown"],
];

export default function Workspace() {
  const [provider, setProvider] = useState<Provider | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [brief, setBrief] = useState("");
  const [cycle, setCycle] = useState<Cycle | null>(null);
  const [cycles, setCycles] = useState<Cycle[]>([]);
  const [agents, setAgents] = useState<AgentRun[]>([]);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [timeline, setTimeline] = useState<Observation[]>([]);
  const [shortlist, setShortlist] = useState<Shortlist | null>(null);
  const [research, setResearch] = useState<ProjectResearch | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [tab, setTab] = useState<CenterTab>("structure");
  const [rail, setRail] = useState<RailSection>("ask");
  const [view, setView] = useState<"3d" | "schematic">("3d");
  const [pasted, setPasted] = useState<{ text: string; name: string } | null>(null);
  const [marked, setMarked] = useState<number[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [compareId, setCompareId] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
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
    const [p, g, t, r] = await Promise.all([
      api.get<Project>(`/projects/${id}`),
      api.get<Graph>(`/projects/${id}/graph`),
      api.get<Observation[]>(`/projects/${id}/timeline?limit=80`),
      api.get<ProjectResearch>(`/projects/${id}/research`),
    ]);
    setProject(p);
    setGraph(g);
    setTimeline(t);
    setResearch(r);
  }, []);

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

  const selectProject = useCallback(
    async (p: Project) => {
      setProject(p);
      setShortlist(null);
      setResearch(null);
      setAgents([]);
      setBrief(p.goal);
      setSelectedId(null);
      setCompareId(null);
      try {
        await refreshProject(p.id);
        const rows = await api.get<Cycle[]>(`/projects/${p.id}/cycles`);
        setCycles(rows);
        if (rows.length > 0) await attachCycle(rows[0]);
        else setCycle(null);
      } catch (e) {
        fail(e);
      }
    },
    [refreshProject, attachCycle],
  );

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
      setCycles((rows) => [created, ...rows.filter((c) => c.id !== created.id)]);
      await attachCycle(created);
      // Eager (inline) execution returns an already-terminal cycle, so the poller never runs and
      // the commit/cycle counters and DAG would keep their pre-cycle values.
      await refreshProject(project.id);
      await loadProjects();
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  async function triggerResearch() {
    if (!project) return;
    setBusy("research");
    setError(null);
    try {
      await api.post(`/projects/${project.id}/research`);
      await refreshProject(project.id);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  /**
   * Hand-off uses the two autonomy entry points the API actually has: a design cycle for the
   * current brief, and a research-daemon refresh anchored on the selected version.
   */
  async function handoff() {
    if (!project) return;
    setBusy("handoff");
    setError(null);
    setShortlist(null);
    try {
      const created = await api.post<Cycle>(`/projects/${project.id}/cycles`, {
        brief: brief || project.goal,
        branch: "main",
      });
      setCycles((rows) => [created, ...rows.filter((c) => c.id !== created.id)]);
      await attachCycle(created);
      const anchor = selectedId ? `?commit_id=${selectedId}` : "";
      try {
        await api.post(`/lab/projects/${project.id}/research/refresh${anchor}`);
      } catch {
        /* the lab daemon is optional; the cycle is the hand-off that matters */
      }
      await refreshProject(project.id);
      await loadProjects();
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

  const versions = useMemo<GraphNode[]>(() => {
    const nodes = graph?.nodes ? [...graph.nodes] : [];
    return nodes.sort((a, b) => {
      const ah = a.is_head ? 1 : 0;
      const bh = b.is_head ? 1 : 0;
      if (ah !== bh) return bh - ah;
      return (b.cycle_round ?? 0) - (a.cycle_round ?? 0);
    });
  }, [graph]);

  const selected = useMemo(
    () => versions.find((n) => n.id === selectedId) || versions.find((n) => n.is_head) || versions[0] || null,
    [versions, selectedId],
  );
  const compared = useMemo(() => versions.find((n) => n.id === compareId) || null, [versions, compareId]);

  const sequence = useMemo(() => {
    if (!selected) return null;
    const item = shortlist?.pack.shortlist.find((s) => s.label === selected.label);
    return item?.sequence ?? null;
  }, [selected, shortlist]);

  // Cmd/Ctrl+K command palette
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      }
      if (e.key === "Escape") setPaletteOpen(false);
      if (e.altKey && /^[1-9]$/.test(e.key)) {
        const next = RAIL_SECTIONS[Number(e.key) - 1];
        if (next) {
          e.preventDefault();
          setRail(next);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const commands: Command[] = [
    { id: "run", label: "run design cycle", hint: "⌘↵", disabled: !project || running, run: runCycle },
    { id: "cancel", label: "cancel running cycle", disabled: !running, run: cancelCycle },
    { id: "seed", label: "load demo project", run: seedDemo },
    { id: "upload", label: "upload sequence / structure", disabled: !project, run: () => fileRef.current?.click() },
    { id: "handoff", label: "hand off to the agent swarm", disabled: !project || running, run: handoff },
    { id: "tab-structure", label: "view 3D structure", run: () => setTab("structure") },
    { id: "tab-lineage", label: "view version DAG", run: () => setTab("lineage") },
    { id: "tab-shortlist", label: "view wet-lab shortlist", run: () => setTab("shortlist") },
    { id: "tab-research", label: "view research daemon and log", run: () => setTab("research") },
    { id: "view-3d", label: "show the 3D viewer", run: () => setView("3d") },
    { id: "view-schematic", label: "show the 2D schematic", run: () => setView("schematic") },
    { id: "clear-pasted", label: "unload pasted structure", disabled: !pasted, run: () => setPasted(null) },
    { id: "clear-compare", label: "exit version compare", disabled: !compareId, run: () => setCompareId(null) },
    {
      id: "refresh",
      label: "refresh project",
      disabled: !project,
      run: () => {
        if (project) refreshProject(project.id).catch(fail);
      },
    },
  ];

  return (
    <>
      <header className="top">
        <div className="brand">
          DYB<span> Pro</span>
        </div>
        <span className="tagline">pre-wetlab design OS</span>
        <ProviderBadge provider={provider} />
        {provider?.openai_configured && <span className="badge">OpenAI analysis on</span>}
        <div className="grow" />
        <Link href="/pharmakon">Pharmakon drug programs →</Link>
        <button className="ghost" type="button" onClick={() => setPaletteOpen(true)}>
          <span className="kbd">⌘K</span> command palette
        </button>
        <input
          type="text"
          style={{ width: 190 }}
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

      {error && (
        <div className="error-bar">
          <p className="err" style={{ margin: 0 }}>
            {error}
          </p>
        </div>
      )}

      <div className="workspace">
        <IconRail active={rail} onSelect={setRail} />

        <aside className="pane left" aria-label="ask and agent control">
          <AskPane
            section={rail}
            selectedLabel={selected?.label ?? selected?.short_id ?? null}
            onHandoff={handoff}
            projects={projects}
            project={project}
            cycles={cycles}
            cycle={cycle}
            agents={agents}
            brief={brief}
            busy={busy}
            running={running}
            fileRef={fileRef}
            onSelectProject={selectProject}
            onBriefChange={setBrief}
            onRun={runCycle}
            onCancel={cancelCycle}
            onSeedDemo={seedDemo}
            onUpload={upload}
            onAttachCycle={(c) => {
              attachCycle(c).catch(fail);
            }}
          />
        </aside>

        <main className="center">
          <div
            style={{
              display: "grid",
              gridTemplateRows: "auto minmax(0, 1fr)",
              minHeight: 0,
              minWidth: 0,
            }}
          >
            <div className="pane-header" role="tablist" aria-label="center surface">
              <div className="row">
                {CENTER_TABS.map(([id, label, hint]) => (
                  <button
                    className="tab tip"
                    type="button"
                    role="tab"
                    key={id}
                    aria-selected={tab === id}
                    data-tip={hint}
                    onClick={() => setTab(id)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="row">
                {tab === "structure" && (
                  <div className="row" role="group" aria-label="viewer mode">
                    {(["3d", "schematic"] as const).map((v) => (
                      <button
                        className="tab tip"
                        type="button"
                        key={v}
                        aria-selected={view === v}
                        data-tip={
                          v === "3d"
                            ? "coarse C\u03b1 coordinates rendered with Mol*"
                            : "2D schematic of the backbone; works without WebGL"
                        }
                        onClick={() => setView(v)}
                      >
                        {v}
                      </button>
                    ))}
                  </div>
                )}
                <SequenceLoader
                  project={project}
                  referenceSequence={sequence}
                  referenceLabel={selected?.label ?? null}
                  busy={busy !== null}
                  onRenderStructure={(text, name) => {
                    setPasted({ text, name });
                    setView("3d");
                    setTab("structure");
                  }}
                  onCommitFile={(file) => {
                    const list = new DataTransfer();
                    list.items.add(file);
                    upload(list.files).catch(fail);
                  }}
                  onCreateTargetProject={(targetName, targetSequence) => {
                    setBusy("project");
                    api
                      .post<Project>("/projects", {
                        name: targetName,
                        goal: brief,
                        target_name: targetName,
                        target_sequence: targetSequence,
                      })
                      .then(async (p) => {
                        await loadProjects();
                        await selectProject(p);
                      })
                      .catch(fail)
                      .finally(() => setBusy(null));
                  }}
                  onCompare={setMarked}
                />
                <span className="meta">{project ? project.name : "no project"}</span>
              </div>
            </div>

            {tab === "structure" &&
              (view === "3d" ? (
                <StructureViewer
                  commitId={selected?.id ?? project?.head_commit_id ?? null}
                  compareCommitId={compared?.id ?? null}
                  label={selected?.label ?? selected?.short_id ?? null}
                  sequence={sequence}
                  mutations={(selected?.mutations ?? []).filter((m): m is string => !!m)}
                  pasted={pasted}
                  marked={marked}
                />
              ) : (
                <ProteinViewer
                  node={selected}
                  compareNode={compared}
                  sequence={sequence}
                  onClearCompare={() => setCompareId(null)}
                />
              ))}

            {tab === "lab" && (
              <div className="center-body lab">
                <ResearchWorkspace projectId={project?.id ?? null} />
              </div>
            )}

            {tab === "data" && (
              <div className="center-body">
                <section className="panel">
                  <h2>Public database search</h2>
                  <p className="hint">
                    Search public records with the upstream source and provenance shown explicitly.
                  </p>
                  <DatabaseSearch />
                </section>
              </div>
            )}

            {tab === "lineage" && (
              <div className="center-body">
                <section className="panel">
                  <h2>Protein version DAG</h2>
                  <p className="hint">
                    Every design is an immutable commit: sequence, structure, scores, parent, agent, prompt
                    and citations.
                  </p>
                  <VersionDag graph={graph} />
                </section>
              </div>
            )}

            {tab === "shortlist" && (
              <div className="center-body">
                <section className="panel">
                  <h2>Wet-lab shortlist</h2>
                  <ShortlistPanel shortlist={shortlist} cycleId={cycle?.id ?? null} />
                </section>
              </div>
            )}

            {tab === "research" && (
              <div className="center-body">
                <section className="panel">
                  <h2
                    className="tip"
                    data-tip="always-live loop, separate from design cycles: it watches commits, uploads and measured results, reuses cached research and re-estimates proxy-vs-measurement drift"
                  >
                    Research daemon
                  </h2>
                  <ResearchPane
                    research={research}
                    onTrigger={triggerResearch}
                    busy={busy === "research"}
                  />
                </section>
                <section className="panel">
                  <h2
                    className="tip"
                    data-tip="append-only memory the next cycle reads before proposing designs"
                  >
                    Observation log
                  </h2>
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
              </div>
            )}
          </div>

          <div>
            {tab === "structure" && (
              <MetricStrip node={selected} compareNode={compared} versions={versions} />
            )}
            <FoldStrip
              nodes={versions}
              selectedId={selected?.id ?? null}
              compareId={compareId}
              onSelect={(n) => {
                setSelectedId(n.id);
                setPasted(null);
                setMarked([]);
                setTab("structure");
              }}
              onCompare={(n) => setCompareId(n.id)}
            />
          </div>
        </main>

        <aside className="pane right" aria-label="pixel agent swarm">
          <AgentSwarm cycle={cycle} agents={agents} />
        </aside>
      </div>

      <CommandPalette open={paletteOpen} commands={commands} onClose={() => setPaletteOpen(false)} />
    </>
  );
}
