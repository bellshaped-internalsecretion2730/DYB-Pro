"use client";

import PixelSprite from "@/components/PixelSprite";
import type { AgentRun, Cycle } from "@/lib/api";

const GROUPS: { key: string; title: string; roles: string[] }[] = [
  { key: "orchestrator", title: "Orchestrator", roles: ["orchestrator"] },
  { key: "literature", title: "Literature", roles: ["literature"] },
  { key: "metrics", title: "Metrics", roles: ["sequence", "structure", "docking"] },
  { key: "planner", title: "Wetlab planner", roles: ["ranking", "wetlab-planner", "planner"] },
];

const WORKFLOW_LABELS: Record<string, string> = {
  alphafold: "AlphaFold",
  proteinmpnn: "ProteinMPNN",
};

/** Maps a real backend AgentRun.status to a display state. Never invents activity. */
export function agentState(status: string): { label: string; color: string; active: boolean } {
  switch (status) {
    case "pending":
    case "queued":
      return { label: "Queued", color: "var(--idle)", active: false };
    case "running":
      return { label: "Thinking", color: "var(--thinking)", active: true };
    case "searching":
      return { label: "Searching", color: "var(--searching)", active: true };
    case "writing":
      return { label: "Writing", color: "var(--writing)", active: true };
    case "finished":
    case "succeeded":
      return { label: "Done", color: "var(--ok)", active: false };
    case "failed":
    case "timeout":
      return { label: status, color: "var(--bad)", active: false };
    case "cancelled":
      return { label: "Cancelled", color: "var(--warn)", active: false };
    default:
      return { label: status, color: "var(--idle)", active: false };
  }
}

function familyOf(role: string): string {
  const g = GROUPS.find((x) => x.roles.includes(role));
  return g ? g.key : "default";
}

function statusPill(status: string) {
  if (status === "finished" || status === "succeeded") return "pill ok";
  if (status === "failed" || status === "cancelled" || status === "timeout") return "pill no";
  return "pill";
}

function AgentCard({ agent }: { agent: AgentRun }) {
  const state = agentState(agent.status);
  const feed = (agent.log || []).slice(-2);
  const family = familyOf(agent.role);
  return (
    <div className={`agent-card agent-${family}`} data-testid="agent-card">
      <PixelSprite
        family={family}
        color={state.color}
        active={state.active}
        title={`${agent.role} — ${state.label}`}
      />
      <div className="agent-meta">
        <div className="agent-name">
          <span className="k">{agent.role}</span>
          <span className="mono" style={{ color: "var(--faint)", fontSize: 10 }}>
            {agent.acus}/{agent.acu_limit} ACU
          </span>
        </div>
        <div className="agent-state">
          <span className="dot" style={{ background: state.color }} />
          <span>{state.label}</span>
          <span className={statusPill(agent.status)}>{agent.status}</span>
          <span style={{ color: "var(--faint)" }}>{agent.provider}</span>
          {agent.attempts > 1 && <span style={{ color: "var(--faint)" }}>attempt {agent.attempts}</span>}
        </div>
        <div className="agent-task" title={agent.task}>
          {agent.task}
        </div>
        {agent.error && <div className="err">{agent.error}</div>}
        {feed.length > 0 && (
          <div className="glass" data-testid="agent-feed">
            {feed.map((l, i) => (
              <div className="feed-line" key={i}>
                <time dateTime={l.at}>{(l.at || "").slice(11, 19) || "--:--:--"}</time>
                <span className="k">{l.message}</span>
              </div>
            ))}
          </div>
        )}
        {agent.devin_session_url && (
          <a href={agent.devin_session_url} target="_blank" rel="noreferrer" style={{ fontSize: 11 }}>
            Devin session ↗
          </a>
        )}
      </div>
    </div>
  );
}

export default function AgentSwarm({
  cycle,
  agents,
}: {
  cycle: Cycle | null;
  agents: AgentRun[];
}) {
  const active = agents.filter((a) => agentState(a.status).active).length;
  const known = new Set(GROUPS.flatMap((g) => g.roles));
  const groups = [
    ...GROUPS.map((g) => ({ ...g, rows: agents.filter((a) => g.roles.includes(a.role)) })),
    { key: "other", title: "Other", roles: [], rows: agents.filter((a) => !known.has(a.role)) },
  ].filter((g) => g.rows.length > 0);
  const workflowTools = Object.entries(cycle?.plan?.workflow_tools ?? {}).filter(
    (entry): entry is [string, "auto" | "required"] => entry[1] === "auto" || entry[1] === "required",
  );
  const computeRuns = cycle?.plan?.workflow?.runs ?? [];

  return (
    <div data-testid="agent-swarm">
      <div className="pane-header">
        <span className="label">Agent swarm</span>
        <span className="meta">{cycle ? `${active} active · ${agents.length} runs` : "Idle"}</span>
      </div>

      {cycle && (
        <div className="section tight">
          <div className="swarm-kpis">
            <span className="badge">
              Round {cycle.round} · {cycle.status}
            </span>
            <span className="badge">{cycle.provider || "Provider pending"}</span>
            <span className="badge">
              {cycle.acus_used} / {cycle.acu_limit} ACU
            </span>
          </div>
          {workflowTools.length > 0 && (
            <div
              className="swarm-workflow"
              role="group"
              aria-label={`Requested compute: ${workflowTools
                .map(([tool, mode]) => `${WORKFLOW_LABELS[tool] ?? tool} ${mode}`)
                .join(", ")}`}
            >
              <span className="swarm-workflow-label">Compute</span>
              {workflowTools.map(([tool, mode]) => (
                <span
                  className={`workflow-request ${mode} tip`}
                  data-tip={
                    mode === "required"
                      ? `${WORKFLOW_LABELS[tool] ?? tool} must complete before this run can proceed`
                      : `The swarm may run ${WORKFLOW_LABELS[tool] ?? tool} when the evidence calls for it`
                  }
                  key={tool}
                  tabIndex={0}
                >
                  {WORKFLOW_LABELS[tool] ?? tool} / {mode}
                </span>
              ))}
            </div>
          )}
          {computeRuns.length > 0 && (
            <div className="compute-run-map" aria-label="Compute execution results">
              {computeRuns.map((run, index) => (
                <span
                  className={`compute-run compute-${run.status} tip`}
                  data-tip={`${WORKFLOW_LABELS[run.tool] ?? run.tool}${
                    run.target ? ` / ${run.target}` : ""
                  }: ${run.reason ?? run.status}`}
                  key={`${run.tool}-${run.target ?? "default"}-${index}`}
                  tabIndex={0}
                >
                  <i aria-hidden="true" />
                  {WORKFLOW_LABELS[run.tool] ?? run.tool}
                  {run.target ? ` · ${run.target}` : ""}
                  <b>{run.status}</b>
                </span>
              ))}
            </div>
          )}
          {agents.length > 0 && (
            <div className="swarm-map" aria-label="agent execution map">
              {agents.map((agent) => {
                const state = agentState(agent.status);
                return (
                  <span
                    className="tip"
                    key={agent.id}
                    data-tip={`${agent.role}: ${state.label}`}
                    style={{ background: state.color }}
                    tabIndex={0}
                  />
                );
              })}
            </div>
          )}
          {cycle.orchestrator_session_url && (
            <a
              href={cycle.orchestrator_session_url}
              target="_blank"
              rel="noreferrer"
              style={{ fontSize: 11 }}
            >
              orchestrator session ↗
            </a>
          )}
          {cycle.plan?.strategy && (
            <div
              className="glass plan-summary tip"
              data-tip={cycle.plan.strategy}
              data-testid="plan-strategy"
              tabIndex={0}
            >
              <span>Run strategy</span>
              <span className="sr-only">{cycle.plan.strategy}</span>
            </div>
          )}
          {cycle.error && <p className="err">{cycle.error}</p>}
        </div>
      )}

      {!cycle && (
        <p className="hint" style={{ padding: "10px 12px" }}>
          Run a cycle to watch the orchestrator fan out to child agents.
        </p>
      )}

      {cycle && groups.length === 0 && (
        <p className="hint" style={{ padding: "10px 12px" }}>
          waiting for the orchestrator to spawn children…
        </p>
      )}

      {groups.map((g) => (
        <section className="agent-group" key={g.key}>
          <div className="pane-header" style={{ position: "static" }}>
            <span className="label">{g.title}</span>
            <span className="meta">{g.rows.length}</span>
          </div>
          {g.rows.map((a) => (
            <AgentCard agent={a} key={a.id} />
          ))}
        </section>
      ))}
    </div>
  );
}
