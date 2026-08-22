"use client";

import type { LabDaemonStatus } from "@/lib/api";

const LIVE = ["idle", "working", "researching"];

export default function DaemonPane({
  daemon,
  busy,
  onRefresh,
  onTick,
}: {
  daemon: LabDaemonStatus | null;
  busy: string | null;
  onRefresh: () => void;
  onTick: () => void;
}) {
  if (!daemon) return <p className="muted">pick a campaign to see the Research Daemon</p>;
  const degraded = daemon.status === "degraded" || daemon.status === "error";
  const provider = daemon.provider || "local-simulation";
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <span className={`badge ${degraded ? "bad" : LIVE.includes(daemon.status) ? "devin" : "sim"}`}>
          daemon: {daemon.status}
        </span>
        <span className={`badge ${provider === "devin" ? "devin" : "sim"}`}>{provider}</span>
        <span
          className="badge tip"
          data-tip={`${daemon.queue_depth} debounced task(s) waiting · tick every ${daemon.tick_seconds}s · debounce ${daemon.debounce_seconds}s`}
          tabIndex={0}
        >
          queue {daemon.queue_depth}
        </span>
        <span className="badge">events {daemon.event_sequence}</span>
        <span
          className="badge tip"
          data-tip={
            daemon.heartbeat_at
              ? `last heartbeat ${new Date(daemon.heartbeat_at).toLocaleTimeString()}`
              : "no heartbeat yet"
          }
          tabIndex={0}
        >
          knowledge v{daemon.knowledge_version}
        </span>
        {daemon.devin_session_url && (
          <a className="badge devin" href={daemon.devin_session_url} target="_blank" rel="noreferrer">
            supervisor session
          </a>
        )}
      </div>
      <p className="hint" style={{ marginBottom: 8 }}>
        {daemon.detail || "watching diffs, labels, new versions and incoming wet-lab results"}
      </p>
      {degraded && (
        <p className="err">
          Devin is unreachable — events stay queued and are labelled honestly; nothing is faked.
        </p>
      )}
      <div className="row" style={{ marginBottom: 10 }}>
        <button
          type="button"
          className="secondary"
          data-testid="daemon-refresh"
          onClick={onRefresh}
          disabled={busy === "refresh"}
        >
          queue research pass
        </button>
        <button type="button" data-testid="daemon-tick" onClick={onTick} disabled={busy === "tick"}>
          {busy === "tick" ? "daemon working…" : "run due work now"}
        </button>
      </div>
      <table>
        <thead>
          <tr>
            <th>event</th>
            <th>status</th>
            <th>coalesced</th>
            <th>try</th>
            <th>queued</th>
          </tr>
        </thead>
        <tbody>
          {daemon.tasks.slice(0, 8).map((t) => (
            <tr key={t.id}>
              <td>{t.kind}</td>
              <td>
                <span className={`pill ${t.status === "failed" ? "no" : t.status === "done" ? "ok" : ""}`}>
                  {t.status}
                </span>
                {t.error ? <div className="err">{t.error}</div> : null}
              </td>
              <td>{t.coalesced_count}</td>
              <td>{t.attempts}</td>
              <td className="mono">{new Date(t.created_at).toLocaleTimeString()}</td>
            </tr>
          ))}
          {daemon.tasks.length === 0 && (
            <tr>
              <td colSpan={5} className="muted">
                no daemon events yet — add a label or register results
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
