"use client";

import type { AgentRun, Cycle } from "@/lib/api";

function statusPill(status: string) {
  if (status === "succeeded") return "pill ok";
  if (status === "failed" || status === "cancelled") return "pill no";
  return "pill";
}

export default function AgentSwarm({
  cycle,
  agents,
}: {
  cycle: Cycle | null;
  agents: AgentRun[];
}) {
  if (!cycle) {
    return <p className="hint">Run a cycle to watch the orchestrator fan out to child agents.</p>;
  }
  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className="badge">
          round {cycle.round} · {cycle.status}
        </span>
        <span className="badge">{cycle.provider || "provider pending"}</span>
        <span className="badge">
          {cycle.acus_used} / {cycle.acu_limit} ACU
        </span>
        {cycle.orchestrator_session_url && (
          <a className="badge" href={cycle.orchestrator_session_url} target="_blank" rel="noreferrer">
            orchestrator session ↗
          </a>
        )}
      </div>
      {cycle.plan?.strategy && <p className="hint">Plan: {cycle.plan.strategy}</p>}
      {cycle.error && <p className="err">{cycle.error}</p>}
      <table>
        <thead>
          <tr>
            <th>Agent</th>
            <th>Status</th>
            <th>ACU</th>
            <th>Latest log</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((a) => (
            <tr key={a.id}>
              <td>
                <b>{a.role}</b>
                <div className="muted">{a.task}</div>
                {a.devin_session_url && (
                  <a href={a.devin_session_url} target="_blank" rel="noreferrer">
                    devin session ↗
                  </a>
                )}
              </td>
              <td>
                <span className={statusPill(a.status)}>{a.status}</span>
                <div className="muted">{a.provider}</div>
                {a.attempts > 1 && <div className="muted">attempt {a.attempts}</div>}
              </td>
              <td className="mono">
                {a.acus}/{a.acu_limit}
              </td>
              <td className="muted">
                {a.error ? <span className="err">{a.error}</span> : a.log?.at(-1)?.message || "—"}
              </td>
            </tr>
          ))}
          {agents.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                waiting for the orchestrator to spawn children…
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
