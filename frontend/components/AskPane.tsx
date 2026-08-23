"use client";

import type { ReactNode, RefObject } from "react";
import type {
  Cycle,
  Project,
  WorkflowTool,
  WorkflowToolMode,
  WorkflowTools,
} from "@/lib/api";

const WORKFLOW_TOOL_OPTIONS: {
  id: WorkflowTool;
  label: string;
  description: string;
}[] = [
  {
    id: "alphafold",
    label: "AlphaFold",
    description: "Predict a backbone when the run needs a trustworthy structure",
  },
  {
    id: "proteinmpnn",
    label: "ProteinMPNN",
    description: "Propose sequences against the selected or predicted backbone",
  },
];

const WORKFLOW_MODES: { id: WorkflowToolMode; label: string }[] = [
  { id: "off", label: "Off" },
  { id: "auto", label: "Auto" },
  { id: "required", label: "Required" },
];

export default function AskPane({
  projects,
  project,
  cycle,
  brief,
  busy,
  running,
  workflowTools,
  fileRef,
  inputTools,
  onSelectProject,
  onBriefChange,
  onWorkflowToolChange,
  onRun,
  onCancel,
  onSeedDemo,
  onUpload,
}: {
  projects: Project[];
  project: Project | null;
  cycle: Cycle | null;
  brief: string;
  busy: string | null;
  running: boolean;
  workflowTools: WorkflowTools;
  fileRef: RefObject<HTMLInputElement | null>;
  inputTools?: ReactNode;
  onSelectProject: (p: Project) => void;
  onBriefChange: (value: string) => void;
  onWorkflowToolChange: (tool: WorkflowTool, mode: WorkflowToolMode) => void;
  onRun: () => void;
  onCancel: () => void;
  onSeedDemo: () => void;
  onUpload: (files: FileList | null) => void;
}) {
  const hasProtein = Boolean(project && project.commit_count > 0);
  const hasTarget = Boolean(project?.target_sequence?.trim());
  const hasGoal = brief.trim().length > 0;
  const acuProgress = cycle?.acu_limit
    ? Math.min(100, Math.max(0, (cycle.acus_used / cycle.acu_limit) * 100))
    : 0;

  let assistantMessage = "Choose a project, then add the protein and target you want to investigate.";
  if (project && !hasProtein) assistantMessage = "Project selected. Attach a protein sequence or structure to begin.";
  if (hasProtein && !hasTarget) {
    assistantMessage = "Protein received. Add the target sequence, then describe the outcome you want.";
  }
  if (hasProtein && hasTarget && !hasGoal) {
    assistantMessage = "Molecular context is ready. Tell me the binding or design objective.";
  }
  if (hasProtein && hasGoal && !running) {
    assistantMessage = "The context is ready. Submit the goal when you want the swarm to start.";
  }
  if (running) {
    assistantMessage = "The swarm is working autonomously. Live execution appears in the panel on the right.";
  }

  return (
    <div className="chat-pane" data-testid="ask-pane">
      <div className="pane-header chat-header">
        <div>
          <span className="eyebrow">Research copilot</span>
          <strong>Design brief</strong>
        </div>
        <span className={`live-indicator${running ? " active" : ""}`}>
          <i /> {running ? "Running" : "Ready"}
        </span>
      </div>

      <div className="chat-thread" role="log" aria-label="research conversation">
        <div className="chat-message assistant">
          <span className="chat-avatar" aria-hidden="true">D</span>
          <div className="chat-bubble">
            <span className="chat-author">DYB copilot</span>
            <p>{assistantMessage}</p>
          </div>
        </div>

        {cycle && (
          <div className="run-card" data-testid="cycle-progress">
            <div className="run-card-head">
              <span>Round {cycle.round}</span>
              <strong>{cycle.status.replaceAll("_", " ")}</strong>
            </div>
            <span className="progress-track" aria-label={`${acuProgress.toFixed(0)} percent ACU used`}>
              <span style={{ width: `${acuProgress}%` }} />
            </span>
            <div className="run-card-foot">
              <span>{cycle.provider || "Provider pending"}</span>
              <span>{cycle.acus_used}/{cycle.acu_limit} ACU</span>
            </div>
          </div>
        )}
      </div>

      <div className="readiness" aria-label="run readiness">
        <span
          className={`readiness-item tip${hasProtein ? " ready" : ""}`}
          data-tip="A committed FASTA, PDB or mmCIF working protein"
          tabIndex={0}
        >
          <i /> Protein
        </span>
        <span
          className={`readiness-item tip${hasTarget ? " ready" : ""}`}
          data-tip="The target sequence used by docking features"
          tabIndex={0}
        >
          <i /> Target
        </span>
        <span
          className={`readiness-item tip${hasGoal ? " ready" : ""}`}
          data-tip="The outcome and constraints the orchestrator will plan against"
          tabIndex={0}
        >
          <i /> Goal
        </span>
      </div>

      <div className="chat-composer">
        <label className="field compact-field">
          <span className="sr-only">Project context</span>
          <select
            value={project?.id || ""}
            aria-label="project"
            onChange={(event) => {
              const next = projects.find((row) => row.id === event.target.value);
              if (next) onSelectProject(next);
            }}
          >
            <option value="">Select project</option>
            {projects.map((row) => {
              const demoSuffix = row.is_demo && !row.name.toLowerCase().includes("(demo)") ? " (demo)" : "";
              return (
                <option key={row.id} value={row.id}>
                  {row.name}{demoSuffix}
                </option>
              );
            })}
          </select>
        </label>

        <label className="field prompt-field">
          <span className="sr-only">Research goal</span>
          <textarea
            value={brief}
            aria-label="research brief"
            onChange={(event) => onBriefChange(event.target.value)}
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                (event.metaKey || event.ctrlKey) &&
                project &&
                !running &&
                hasProtein &&
                hasTarget &&
                hasGoal
              ) {
                event.preventDefault();
                onRun();
              }
            }}
            placeholder="Describe the desired binding, stability, selectivity, and constraints…"
          />
        </label>

        <div className="workflow-controls" role="group" aria-label="compute workflow">
          <div className="workflow-controls-head">
            <span>Compute workflow</span>
            <span
              className="tip"
              data-tip="Auto lets the swarm decide. Required stops the run if that compute step cannot complete."
              tabIndex={0}
              aria-label="Compute workflow mode help"
            >
              ?
            </span>
          </div>
          {WORKFLOW_TOOL_OPTIONS.map((tool) => (
            <div className="workflow-tool-row" key={tool.id}>
              <span className="workflow-tool-name tip" data-tip={tool.description} tabIndex={0}>
                <i aria-hidden="true" />
                {tool.label}
              </span>
              <span
                className="workflow-mode-options"
                role="group"
                aria-label={`${tool.label} workflow mode`}
              >
                {WORKFLOW_MODES.map((mode) => (
                  <button
                    className="workflow-mode"
                    type="button"
                    key={mode.id}
                    aria-pressed={workflowTools[tool.id] === mode.id}
                    onClick={() => onWorkflowToolChange(tool.id, mode.id)}
                    disabled={running}
                  >
                    {mode.label}
                  </button>
                ))}
              </span>
            </div>
          ))}
        </div>

        <div className="composer-tools">
          {inputTools}
          <label className={`attachment-button${!project || busy === "upload" ? " disabled" : ""}`}>
            <input
              ref={fileRef}
              className="sr-only"
              type="file"
              multiple
              accept=".fasta,.fa,.faa,.pdb,.ent,.cif,.mmcif,.csv,.tsv"
              aria-label="upload sequences or structures"
              onChange={(event) => onUpload(event.target.files)}
              disabled={!project || busy === "upload"}
            />
            <span aria-hidden="true">＋</span> Attach file
          </label>
          <button className="ghost compact" type="button" onClick={onSeedDemo} disabled={busy === "seed"}>
            Load demo
          </button>
        </div>

        <button
          className="run-button"
          type="button"
          onClick={onRun}
          disabled={!project || !hasProtein || !hasTarget || !hasGoal || busy === "cycle" || running}
        >
          <span>{running ? "Autonomous run in progress" : "Send goal & start autonomous run"}</span>
          <span className="kbd">Ctrl ↵</span>
        </button>
        {running && (
          <button className="cancel-button" type="button" onClick={onCancel}>
            Cancel current run
          </button>
        )}
      </div>
    </div>
  );
}
