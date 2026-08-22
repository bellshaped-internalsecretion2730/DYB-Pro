"use client";

import type { ProjectResearch, ResearchEvent } from "@/lib/api";

function statusPill(status: string) {
  if (status === "done") return "pill ok";
  if (status === "failed") return "pill no";
  return "pill";
}

function driftLine(drift: Record<string, unknown>): string {
  const objectives = (drift?.objectives || {}) as Record<string, { kendall_tau: number | null; pairs: number }>;
  const reported = Object.entries(objectives).filter(([, v]) => v.kendall_tau !== null);
  if (reported.length === 0) {
    return "no objective has enough measured designs to report proxy-vs-measurement agreement yet";
  }
  return reported.map(([k, v]) => `${k}: tau ${v.kendall_tau} over ${v.pairs} designs`).join(" · ");
}

function EventRow({ event }: { event: ResearchEvent }) {
  return (
    <tr>
      <td>
        <span className={statusPill(event.status)}>{event.status}</span>
        <div className="muted">{new Date(event.created_at).toLocaleTimeString()}</div>
      </td>
      <td>
        <b>{event.trigger}</b>
        {event.coalesced > 0 && <div className="muted">+{event.coalesced} coalesced</div>}
        <div className="muted">{event.provider}</div>
      </td>
      <td>
        {event.error ? <span className="err">{event.error}</span> : event.summary || "—"}
        <div className="muted">{driftLine(event.drift)}</div>
        {event.findings.slice(0, 4).map((f) => (
          <div key={f.topic_key} className="muted">
            {f.from_cache ? "cached" : "researched"} · {f.topic}
            {f.findings[0]?.citation ? ` — ${f.findings[0].citation}` : ""}
          </div>
        ))}
      </td>
      <td className="mono">
        {event.cache_hits}/{event.cache_hits + event.cache_writes}
        <div className="muted">{event.acus} ACU</div>
      </td>
    </tr>
  );
}

export default function ResearchPane({
  research,
  onTrigger,
  busy,
}: {
  research: ProjectResearch | null;
  onTrigger: () => void;
  busy?: boolean;
}) {
  if (!research) {
    return <p className="hint">Load a project to see what the research daemon has been doing.</p>;
  }
  const { daemon, session, events, notes, cache } = research;
  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className={daemon.enabled ? "pill ok" : "pill no"}>
          daemon {daemon.enabled ? "live" : "off"}
        </span>
        <span className="badge">{daemon.provider || "no provider configured"}</span>
        <span className="badge">{daemon.queued} queued</span>
        <span className="badge">{daemon.running} running</span>
        {daemon.failed > 0 && <span className="pill no">{daemon.failed} failed</span>}
        <span className="badge">
          debounce {daemon.debounce_seconds}s · tick {daemon.tick_seconds}s
        </span>
        <span className="badge">
          cache {cache.topics} topics · {cache.reuses} reuses
        </span>
        {session?.session_url && (
          <a className="badge" href={session.session_url} target="_blank" rel="noreferrer">
            daemon session ↗
          </a>
        )}
        <button onClick={onTrigger} disabled={busy}>
          {busy ? "queueing…" : "research now"}
        </button>
      </div>
      <p className="hint">
        The daemon reacts to commits, uploads and measured results: triggers arriving together are
        coalesced into one event rather than dropped, and every answered question stays in the
        append-only cache for later versions.
      </p>
      <table>
        <thead>
          <tr>
            <th>Event</th>
            <th>Trigger</th>
            <th>What it found</th>
            <th>From cache</th>
          </tr>
        </thead>
        <tbody>
          {events.map((e) => (
            <EventRow key={e.id} event={e} />
          ))}
          {events.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                nothing has changed in this lineage yet
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {notes.length > 0 && (
        <>
          <h4 style={{ marginBottom: 4 }}>Cached research</h4>
          <table>
            <thead>
              <tr>
                <th>Topic</th>
                <th>Question</th>
                <th>Reused</th>
              </tr>
            </thead>
            <tbody>
              {notes.slice(0, 12).map((n) => (
                <tr key={n.id}>
                  <td>
                    <b>{n.topic}</b>
                    <div className="muted mono">{n.topic_key}</div>
                  </td>
                  <td className="muted">
                    {n.question}
                    {n.citations.length > 0 && (
                      <div className="muted">{n.citations.join(" · ")}</div>
                    )}
                  </td>
                  <td className="mono">
                    {n.reuse_count}×
                    <div className="muted">{n.provider}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
