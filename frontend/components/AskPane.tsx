"use client";

import type { RefObject } from "react";
import { agentState } from "@/components/AgentSwarm";
import type { AgentRun, Cycle, Project } from "@/lib/api";

const UNAVAILABLE = "not exposed by the Foldsmith API — cancel is the only live run control";

export default function AskPane({
  projects,
  project,
  cycles,
  cycle,
  agents,
  brief,
  busy,
  running,
  fileRef,
  onSelectProject,
  onBriefChange,
  onRun,
  onCancel,
  onSeedDemo,
  onUpload,
  onAttachCycle,
}: {
  projects: Project[];
  project: Project | null;
  cycles: Cycle[];
  cycle: Cycle | null;
  agents: AgentRun[];
  brief: string;
  busy: string | null;
  running: boolean;
  fileRef: RefObject<HTMLInputElement | null>;
  onSelectProject: (p: Project) => void;
  onBriefChange: (value: string) => void;
  onRun: () => void;
  onCancel: () => void;
  onSeedDemo: () => void;
  onUpload: (files: FileList | null) => void;
  onAttachCycle: (c: Cycle) => void;
}) {
  const planned = cycle?.plan?.agents || [];

  return (
    <div data-testid="ask-pane">
      <div className="pane-header">
        <span className="label">ask</span>
        <span className="meta">⌘K commands</span>
      </div>

      <div className="section">
        <select
          value={project?.id || ""}
          aria-label="project"
          onChange={(e) => {
            const p = projects.find((x) => x.id === e.target.value);
            if (p) onSelectProject(p);
          }}
        >
          <option value="">— select project —</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name} {p.is_demo ? "(demo)" : ""}
            </option>
          ))}
        </select>
        <textarea
          value={brief}
          aria-label="research brief"
          onChange={(e) => onBriefChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && project && !running) onRun();
          }}
          placeholder="Improve GB1 thermal stability without losing predicted Fc binding…"
        />
        <div className="row">
          <button type="button" onClick={onRun} disabled={!project || busy === "cycle" || running}>
            {running ? "cycle running…" : "run design cycle"}
          </button>
          {running && (
            <button className="secondary" type="button" onClick={onCancel}>
              cancel
            </button>
          )}
          <span className="kbd">⌘↵</span>
        </div>
        <div className="row">
          <button className="secondary" type="button" onClick={onSeedDemo} disabled={busy === "seed"}>
            load demo project
          </button>
        </div>
        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".fasta,.fa,.faa,.pdb,.ent,.cif,.mmcif,.csv,.tsv"
          aria-label="upload sequences or structures"
          onChange={(e) => onUpload(e.target.files)}
          disabled={!project || busy === "upload"}
        />
        {project && (
          <span className="mono" style={{ color: "var(--faint)", fontSize: 10.5 }}>
            {project.commit_count} commits · {project.cycle_count} cycles · {project.branches.join(", ")}
          </span>
        )}
      </div>

      <div className="section tight">
        <span className="label">agent control</span>
        <div className="row">
          <button className="secondary" type="button" onClick={onCancel} disabled={!running}>
            cancel run
          </button>
          {["pause", "resume", "redirect", "spawn"].map((c) => (
            <button className="ghost" type="button" key={c} disabled aria-disabled title={UNAVAILABLE}>
              {c}
            </button>
          ))}
        </div>
        <div className="agent-state">
          <span
            className="dot"
            style={{ background: running ? "var(--thinking)" : "var(--idle)" }}
          />
          <span>
            {cycle ? `round ${cycle.round} · ${cycle.status}` : "no cycle attached"}
          </span>
          {cycle && (
            <span className="mono" style={{ color: "var(--faint)" }}>
              {cycle.acus_used}/{cycle.acu_limit} ACU
            </span>
          )}
        </div>
      </div>

      <div className="section tight">
        <span className="label">playbook / tasks</span>
        {planned.length === 0 && <span className="hint">the orchestrator plan lands here</span>}
        <div className="list">
          {planned.map((p, i) => {
            const run = agents.find((a) => a.role === p.role);
            const state = run ? agentState(run.status) : null;
            return (
              <div className="list-row" key={`${p.role}-${i}`} title={p.task}>
                <span className="k">
                  <span className="dot" style={{ background: state?.color || "var(--idle)", marginRight: 6 }} />
                  {p.role}
                </span>
                <span className="v">{state?.label || "planned"}</span>
              </div>
            );
          })}
        </div>
      </div>

      <div className="section tight">
        <span className="label">recent briefs</span>
        {cycles.length === 0 && <span className="hint">no cycles yet</span>}
        <div className="list">
          {cycles.slice(0, 6).map((c) => (
            <button
              className="list-row"
              type="button"
              key={`brief-${c.id}`}
              title={c.brief}
              onClick={() => onBriefChange(c.brief)}
            >
              <span className="k">{c.brief || "(empty brief)"}</span>
              <span className="v">r{c.round}</span>
            </button>
          ))}
        </div>
      </div>

      <div className="section tight">
        <span className="label">session history</span>
        <div className="list">
          {cycles.map((c) => (
            <button
              className="list-row"
              type="button"
              key={c.id}
              aria-current={cycle?.id === c.id}
              onClick={() => onAttachCycle(c)}
            >
              <span className="k">
                cycle r{c.round} · {c.status}
              </span>
              <span className="v">{c.provider || "—"}</span>
            </button>
          ))}
          {cycles.length === 0 && <span className="hint">run a cycle to build history</span>}
        </div>
      </div>
    </div>
  );
}
