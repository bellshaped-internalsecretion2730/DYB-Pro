"use client";

import type { RefObject } from "react";
import { agentState } from "@/components/AgentSwarm";
import type { RailSection } from "@/components/IconRail";
import type { AgentRun, Cycle, Project } from "@/lib/api";

const TITLES: Record<RailSection, string> = {
  ask: "Ask",
  agents: "Agents",
  history: "History",
  files: "Files",
  handoff: "Handoff",
};

export default function AskPane({
  section,
  projects,
  project,
  cycles,
  cycle,
  agents,
  brief,
  busy,
  running,
  fileRef,
  selectedLabel,
  handoffNote,
  onSelectProject,
  onBriefChange,
  onRun,
  onCancel,
  onSeedDemo,
  onUpload,
  onAttachCycle,
  onHandoff,
}: {
  section: RailSection;
  projects: Project[];
  project: Project | null;
  cycles: Cycle[];
  cycle: Cycle | null;
  agents: AgentRun[];
  brief: string;
  busy: string | null;
  running: boolean;
  fileRef: RefObject<HTMLInputElement | null>;
  selectedLabel: string | null;
  handoffNote: string | null;
  onSelectProject: (p: Project) => void;
  onBriefChange: (value: string) => void;
  onRun: () => void;
  onCancel: () => void;
  onSeedDemo: () => void;
  onUpload: (files: FileList | null) => void;
  onAttachCycle: (c: Cycle) => void;
  onHandoff: () => void;
}) {
  const planned = cycle?.plan?.agents || [];

  return (
    <div data-testid="ask-pane">
      <div className="pane-header">
        <span className="label">{TITLES[section]}</span>
        <span className="meta">⌘K commands</span>
      </div>

      {section === "ask" && (
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
              {running ? "Cycle running…" : "Run design cycle"}
            </button>
            {running && (
              <button className="secondary" type="button" onClick={onCancel}>
                Cancel
              </button>
            )}
            <span className="kbd">⌘↵</span>
          </div>
          <div className="row">
            <button className="secondary" type="button" onClick={onSeedDemo} disabled={busy === "seed"}>
              Load demo project
            </button>
          </div>
        </div>
      )}

      {section === "agents" && (
        <>
          <div className="section tight">
            <span className="label">Run control</span>
            <div className="row">
              <button className="secondary" type="button" onClick={onCancel} disabled={!running}>
                Cancel run
              </button>
              <span
                className="hint tip"
                data-tip="the API exposes cancel only — there is no pause, resume, redirect or spawn route, so no button pretends to offer them"
                tabIndex={0}
              >
                Cancel is the only live control
              </span>
            </div>
            <div className="agent-state">
              <span className="dot" style={{ background: running ? "var(--thinking)" : "var(--idle)" }} />
              <span>{cycle ? `Round ${cycle.round} · ${cycle.status}` : "No cycle attached"}</span>
              {cycle && (
                <span className="mono" style={{ color: "var(--faint)" }}>
                  {cycle.acus_used}/{cycle.acu_limit} ACU
                </span>
              )}
            </div>
          </div>

          <div className="section tight">
            <span className="label">Playbook / tasks</span>
            {planned.length === 0 && <span className="hint">The orchestrator plan lands here</span>}
            <div className="list">
              {planned.map((p, i) => {
                const run = agents.find((a) => a.role === p.role);
                const state = run ? agentState(run.status) : null;
                return (
                  <div className="list-row tip" key={`${p.role}-${i}`} data-tip={p.task} tabIndex={0}>
                    <span className="k">
                      <span
                        className="dot"
                        style={{ background: state?.color || "var(--idle)", marginRight: 6 }}
                      />
                      {p.role}
                    </span>
                    <span className="v">{state?.label || "Planned"}</span>
                  </div>
                );
              })}
            </div>
          </div>
        </>
      )}

      {section === "history" && (
        <>
          <div className="section tight">
            <span className="label">Recent briefs</span>
            {cycles.length === 0 && <span className="hint">No cycles yet</span>}
            <div className="list">
              {cycles.slice(0, 6).map((c) => (
                <button
                  className="list-row tip"
                  type="button"
                  key={`brief-${c.id}`}
                  data-tip={c.brief || "empty brief"}
                  onClick={() => onBriefChange(c.brief)}
                >
                  <span className="k">
                    <span className="clip">{c.brief || "(empty brief)"}</span>
                  </span>
                  <span className="v">r{c.round}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="section tight">
            <span className="label">Session history</span>
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
                    Cycle r{c.round} · {c.status}
                  </span>
                  <span className="v">{c.provider || "—"}</span>
                </button>
              ))}
              {cycles.length === 0 && <span className="hint">Run a cycle to build history</span>}
            </div>
          </div>
        </>
      )}

      {section === "files" && (
        <div className="section tight">
          <span className="label">Uploads</span>
          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".fasta,.fa,.faa,.pdb,.ent,.cif,.mmcif,.csv,.tsv"
            aria-label="upload sequences or structures"
            onChange={(e) => onUpload(e.target.files)}
            disabled={!project || busy === "upload"}
          />
          <span
            className="hint tip"
            data-tip="FASTA becomes a root commit with a sequence only; PDB/mmCIF becomes a commit that carries the uploaded coordinates; CSV is read as assay results"
            tabIndex={0}
          >
            FASTA · PDB · mmCIF · CSV
          </span>
          {project && (
            <div className="list">
              <div className="list-row">
                <span className="k">Commits</span>
                <span className="v">{project.commit_count}</span>
              </div>
              <div className="list-row">
                <span className="k">Cycles</span>
                <span className="v">{project.cycle_count}</span>
              </div>
              <div className="list-row">
                <span className="k">Branches</span>
                <span className="v">{project.branches.join(", ")}</span>
              </div>
            </div>
          )}
        </div>
      )}

      {section === "handoff" && (
        <div className="section tight">
          <span className="label">Hand to the swarm</span>
          <span className="hint">
            {selectedLabel ? `From ${selectedLabel}` : "No version selected"}
          </span>
          <button
            type="button"
            className="tip"
            data-tip="queues one handoff task for the selected version with the current brief as notes, then starts a design cycle — the autonomy entry points the API exposes"
            disabled={!project || running || busy !== null}
            onClick={onHandoff}
          >
            {busy === "handoff" ? "Handing off…" : "Hand off current brief"}
          </button>
          {handoffNote && (
            <span className="hint" data-testid="rail-handoff-receipt">
              {handoffNote}
            </span>
          )}
          <span
            className="hint tip"
            data-tip="a cycle is one planned pass: propose, score, filter, shortlist. It does not loop until an objective is met, and it cannot be redirected mid-flight"
            tabIndex={0}
          >
            One planned pass, not an open loop
          </span>
        </div>
      )}
    </div>
  );
}
