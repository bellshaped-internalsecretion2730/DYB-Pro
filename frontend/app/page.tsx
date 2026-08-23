"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import AgentSwarm from "@/components/AgentSwarm";
import AskPane from "@/components/AskPane";
import CommandPalette, { type Command } from "@/components/CommandPalette";
import DatabaseSearch from "@/components/DatabaseSearch";
import FoldStrip from "@/components/FoldStrip";
import MetricStrip from "@/components/MetricStrip";
import ProteinViewer from "@/components/ProteinViewer";
import ProviderBadge from "@/components/ProviderBadge";
import ResearchPane from "@/components/ResearchPane";
import ResearchWorkspace from "@/components/ResearchWorkspace";
import SequenceLoader from "@/components/SequenceLoader";
import ShortlistPanel from "@/components/ShortlistPanel";
import StructureViewer from "@/components/StructureViewer";
import VersionDag from "@/components/VersionDag";
import { research as lab } from "@/lib/research";
import {
  DEFAULT_WORKFLOW_TOOLS,
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
  type WorkflowTool,
  type WorkflowToolMode,
  type WorkflowTools,
} from "@/lib/api";

const TERMINAL = ["committed", "partial", "failed", "cancelled"];
type CenterTab = "structure" | "lineage" | "shortlist" | "research" | "lab" | "data";

const CENTER_TABS: [CenterTab, string, string][] = [
  ["structure", "3D viewer", "interactive structure, residue selection, measurements and exports"],
  ["lineage", "Designs", "version history, score charts and immutable design lineage"],
  ["shortlist", "Candidates", "ranked wet-lab candidates for the attached cycle"],
  ["research", "Research", "the autonomous research daemon and its evidence log"],
  ["lab", "Research lab", "labels, evidence, wet-lab loop and campaign learning"],
  ["data", "Sources", "public database search with explicit provenance"],
];

export default function Workspace() {
  const [provider, setProvider] = useState<Provider | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [project, setProject] = useState<Project | null>(null);
  const [brief, setBrief] = useState("");
  const [workflowTools, setWorkflowTools] = useState<WorkflowTools>(() => ({
    ...DEFAULT_WORKFLOW_TOOLS,
  }));
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
  const [view, setView] = useState<"3d" | "schematic">("3d");
  const [pasted, setPasted] = useState<{ text: string; name: string } | null>(null);
  const [marked, setMarked] = useState<number[]>([]);
  const [handoffNote, setHandoffNote] = useState<string | null>(null);
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
    if (!project.head_commit_id || !project.target_sequence?.trim()) {
      setError("Add both the working protein and target sequence before starting the swarm.");
      return;
    }
    setBusy("cycle");
    setError(null);
    setShortlist(null);
    try {
      const created = await api.post<Cycle>(`/projects/${project.id}/cycles`, {
        brief: brief || project.goal,
        branch: "main",
        workflow_tools: workflowTools,
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
   * Hand-off uses the two autonomy entry points the API actually has: the research handoff task
   * for the selected version, and a design cycle for the current brief.
   */
  async function handoff() {
    if (!project) return;
    if (!project.head_commit_id || !project.target_sequence?.trim()) {
      setError("Add both the working protein and target sequence before handing off to the swarm.");
      return;
    }
    setBusy("handoff");
    setError(null);
    setShortlist(null);
    setHandoffNote(null);
    try {
      let note = "";
      try {
        const queued = await lab.handoff(project.id, {
          commit_id: selectedId ?? undefined,
          notes: brief || project.goal,
          workflow_tools: workflowTools,
        });
        note = `Queued ${queued.daemon_task.kind} (${queued.daemon_task.status}) for ${
          queued.handoff.commit_label || queued.handoff.commit_id.slice(0, 8)
        }`;
      } catch {
        /* the lab daemon is optional; the cycle below is the hand-off that always exists */
      }
      const created = await api.post<Cycle>(`/projects/${project.id}/cycles`, {
        brief: brief || project.goal,
        branch: "main",
        workflow_tools: workflowTools,
      });
      setCycles((rows) => [created, ...rows.filter((c) => c.id !== created.id)]);
      await attachCycle(created);
      setHandoffNote(`${note ? `${note} · ` : ""}cycle round ${created.round} is ${created.status}`);
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
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const commands: Command[] = [
    { id: "run", label: "Run design cycle", hint: "⌘↵", disabled: !project || running, run: runCycle },
    { id: "cancel", label: "Cancel running cycle", disabled: !running, run: cancelCycle },
    { id: "seed", label: "Load demo project", run: seedDemo },
    { id: "upload", label: "Upload sequence / structure", disabled: !project, run: () => fileRef.current?.click() },
    { id: "handoff", label: "Hand off to the agent swarm", disabled: !project || running, run: handoff },
    { id: "tab-structure", label: "View 3D structure", run: () => setTab("structure") },
    { id: "tab-lineage", label: "View version DAG", run: () => setTab("lineage") },
    { id: "tab-shortlist", label: "View wet-lab shortlist", run: () => setTab("shortlist") },
    { id: "tab-research", label: "View research daemon and log", run: () => setTab("research") },
    { id: "view-3d", label: "Show the 3D viewer", run: () => setView("3d") },
    { id: "view-schematic", label: "Show the 2D schematic", run: () => setView("schematic") },
    { id: "clear-pasted", label: "Unload pasted structure", disabled: !pasted, run: () => setPasted(null) },
    { id: "clear-compare", label: "Exit version compare", disabled: !compareId, run: () => setCompareId(null) },
    {
      id: "refresh",
      label: "Refresh project",
      disabled: !project,
      run: () => {
        if (project) refreshProject(project.id).catch(fail);
      },
    },
  ];

  const moleculeInputs = (
    <SequenceLoader
      project={project}
      referenceSequence={sequence}
      referenceLabel={selected?.label ?? null}
      busy={busy !== null}
      inline
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
      onSetTarget={(targetName, targetSequence) => {
        if (!project) return;
        setBusy("project");
        api
          .patch<Project>(`/projects/${project.id}`, {
            target_name: targetName,
            target_sequence: targetSequence,
          })
          .then(async (updatedProject) => {
            setProject(updatedProject);
            await refreshProject(updatedProject.id);
            await loadProjects();
          })
          .catch(fail)
          .finally(() => setBusy(null));
      }}
      onCompare={setMarked}
    />
  );

  return (
    <>
      <header className="top">
        <div className="brand-lockup">
          <span className="brand-mark" aria-hidden="true" />
          <div>
            <div className="brand">DYB <span>Pro</span></div>
            <span className="tagline">Autonomous protein design</span>
          </div>
        </div>
        <span className="project-context">
          <span className="project-dot" />
          {project?.name || "No project selected"}
        </span>
        <ProviderBadge provider={provider} />
        <div className="grow" />
        <button className="ghost top-command" type="button" onClick={() => setPaletteOpen(true)}>
          Commands <span className="kbd">Ctrl K</span>
        </button>
        <details className="app-menu">
          <summary>Menu</summary>
          <div className="menu-popover">
            <span className="menu-label">Workspace</span>
            <Link className="menu-item" href="/pharmakon">
              Pharmakon drug programs <span>↗</span>
            </Link>
            <button className="menu-item" type="button" onClick={() => setPaletteOpen(true)}>
              Command palette <span>Ctrl K</span>
            </button>
            <div className="menu-divider" />
            <label className="menu-field">
              <span>DYB app API key</span>
              <input
                type="text"
                value={keyInput}
                onChange={(event) => setKeyInput(event.target.value)}
                aria-label="DYB app API key"
                placeholder="Optional override"
              />
            </label>
            <button
              className="secondary menu-save"
              type="button"
              onClick={() => {
                setApiKey(keyInput);
                window.location.reload();
              }}
            >
              Save API key
            </button>
          </div>
        </details>
      </header>

      {error && (
        <div className="error-bar" role="alert">
          <p className="err">{error}</p>
        </div>
      )}

      <div className="workspace">
        <aside className="pane left" aria-label="ask and agent control">
          <AskPane
            projects={projects}
            project={project}
            cycle={cycle}
            brief={brief}
            busy={busy}
            running={running}
            workflowTools={workflowTools}
            fileRef={fileRef}
            inputTools={moleculeInputs}
            onSelectProject={selectProject}
            onBriefChange={setBrief}
            onWorkflowToolChange={(tool: WorkflowTool, mode: WorkflowToolMode) => {
              setWorkflowTools((current) => ({ ...current, [tool]: mode }));
            }}
            onRun={runCycle}
            onCancel={cancelCycle}
            onSeedDemo={seedDemo}
            onUpload={upload}
          />
        </aside>

        <main className="center">
          <div className="center-shell">
            <div className="center-nav" role="tablist" aria-label="center surface">
              <div className="surface-tabs">
                {CENTER_TABS.map(([id, label, hint]) => (
                  <button
                    className="surface-tab tip"
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
              {tab === "structure" && (
                <div className="view-switch" role="group" aria-label="viewer mode">
                  {(["3d", "schematic"] as const).map((mode) => (
                    <button
                      type="button"
                      key={mode}
                      aria-pressed={view === mode}
                      onClick={() => setView(mode)}
                    >
                      {mode === "3d" ? "3D" : "2D"}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {tab === "structure" &&
              (view === "3d" ? (
                <StructureViewer
                  commitId={selected?.id ?? project?.head_commit_id ?? null}
                  compareCommitId={compared?.id ?? null}
                  label={selected?.label ?? selected?.short_id ?? null}
                  sequence={sequence}
                  mutations={(selected?.mutations ?? []).filter(
                    (mutation): mutation is string => !!mutation,
                  )}
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

            {tab === "lineage" && (
              <div className="center-body designs-view">
                <section className="panel wide metrics-panel">
                  <div className="section-title">
                    <div>
                      <span className="eyebrow">Performance</span>
                      <h2>Design scorecard</h2>
                    </div>
                    <span className="meta">Hover any bar for methodology</span>
                  </div>
                  <MetricStrip node={selected} compareNode={compared} versions={versions} />
                </section>
                <section className="panel wide">
                  <div className="section-title">
                    <div>
                      <span className="eyebrow">Immutable history</span>
                      <h2>Design lineage</h2>
                    </div>
                    <span className="meta">{versions.length} versions</span>
                  </div>
                  <VersionDag graph={graph} />
                </section>
                <section className="panel wide version-browser">
                  <FoldStrip
                    nodes={versions}
                    selectedId={selected?.id ?? null}
                    compareId={compareId}
                    onSelect={(node) => {
                      setSelectedId(node.id);
                      setPasted(null);
                      setMarked([]);
                      setTab("structure");
                    }}
                    onCompare={(node) => setCompareId(node.id)}
                  />
                </section>
              </div>
            )}

            {tab === "shortlist" && (
              <div className="center-body">
                <section className="panel">
                  <div className="section-title">
                    <div>
                      <span className="eyebrow">Decision surface</span>
                      <h2>Wet-lab candidates</h2>
                    </div>
                    {cycles.length > 0 && (
                      <label className="cycle-picker">
                        <span className="sr-only">Cycle history</span>
                        <select
                          className="mini-select"
                          aria-label="cycle history"
                          value={cycle?.id || ""}
                          onChange={(event) => {
                            const nextCycle = cycles.find((row) => row.id === event.target.value);
                            if (nextCycle) attachCycle(nextCycle).catch(fail);
                          }}
                        >
                          {cycles.map((row) => (
                            <option key={row.id} value={row.id}>
                              Round {row.round} · {row.status}
                            </option>
                          ))}
                        </select>
                      </label>
                    )}
                  </div>
                  <ShortlistPanel shortlist={shortlist} cycleId={cycle?.id ?? null} />
                </section>
              </div>
            )}

            {tab === "research" && (
              <div className="center-body research-grid">
                <section className="panel">
                  <div className="section-title">
                    <div>
                      <span className="eyebrow">Always on</span>
                      <h2
                        className="tip"
                        data-tip="Watches commits, uploads and measured results, then reuses cached research and recalculates drift"
                      >
                        Research daemon
                      </h2>
                    </div>
                  </div>
                  <ResearchPane
                    research={research}
                    onTrigger={triggerResearch}
                    busy={busy === "research"}
                  />
                </section>
                <section className="panel">
                  <div className="section-title">
                    <div>
                      <span className="eyebrow">Append-only memory</span>
                      <h2
                        className="tip"
                        data-tip="Evidence the next design cycle reads before proposing mutations"
                      >
                        Evidence log
                      </h2>
                    </div>
                    <span className="meta">{timeline.length} events</span>
                  </div>
                  <div className="timeline">
                    {timeline.map((observation) => (
                      <div className="event" key={observation.id}>
                        <div className="kind">{observation.kind} · {observation.role}</div>
                        <div>{observation.summary}</div>
                      </div>
                    ))}
                    {timeline.length === 0 && <p className="muted">No observations yet</p>}
                  </div>
                </section>
              </div>
            )}

            {tab === "lab" && (
              <div className="center-body lab">
                <ResearchWorkspace projectId={project?.id ?? null} />
              </div>
            )}

            {tab === "data" && (
              <div className="center-body">
                <section className="panel">
                  <div className="section-title">
                    <div>
                      <span className="eyebrow">Provenance first</span>
                      <h2>Public data sources</h2>
                    </div>
                  </div>
                  <DatabaseSearch />
                </section>
              </div>
            )}
          </div>
        </main>

        <aside className="pane right" aria-label="pixel agent swarm">
          <AgentSwarm cycle={cycle} agents={agents} />
        </aside>
      </div>

      <CommandPalette open={paletteOpen} commands={commands} onClose={() => setPaletteOpen(false)} />
      {handoffNote && <span className="sr-only" aria-live="polite">{handoffNote}</span>}
    </>
  );
}
