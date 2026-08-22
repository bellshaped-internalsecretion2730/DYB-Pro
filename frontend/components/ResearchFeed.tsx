"use client";

import { useState } from "react";
import type { Paper, LabResearchEvent } from "@/lib/api";

export default function ResearchFeed({
  events,
  papers,
}: {
  events: LabResearchEvent[];
  papers: Paper[];
}) {
  const [tab, setTab] = useState<"events" | "papers">("events");
  return (
    <div>
      <div className="row" style={{ marginBottom: 8 }}>
        <button
          type="button"
          className={tab === "events" ? "" : "secondary"}
          onClick={() => setTab("events")}
        >
          research events ({events.length})
        </button>
        <button
          type="button"
          className={tab === "papers" ? "" : "secondary"}
          onClick={() => setTab("papers")}
        >
          cached papers ({papers.length})
        </button>
      </div>
      {tab === "events" ? (
        <div className="timeline">
          {events.map((e) => (
            <div className="event" key={e.id}>
              <div className="kind">
                #{e.sequence_no} {e.kind} · {e.role} · {e.trigger}
              </div>
              <div>{e.summary}</div>
              <div className="muted mono">
                {e.provider}
                {e.skills_used.length > 0 ? ` · skills: ${e.skills_used.join(", ")}` : ""}
                {e.devin_session_url ? (
                  <>
                    {" · "}
                    <a href={e.devin_session_url} target="_blank" rel="noreferrer">
                      devin session
                    </a>
                  </>
                ) : null}
              </div>
            </div>
          ))}
          {events.length === 0 && <p className="muted">the daemon has not written an event yet</p>}
        </div>
      ) : (
        <div className="timeline">
          {papers.map((p) => (
            <div className="event" key={p.paper_key}>
              <div className="kind">
                {p.source} · {p.year || "n/a"} · {p.citation_count} citations
              </div>
              <div>
                {p.url ? (
                  <a href={p.url} target="_blank" rel="noreferrer">
                    {p.title}
                  </a>
                ) : (
                  p.title
                )}
              </div>
              {p.extracted_metrics.length > 0 && (
                <div className="muted mono">
                  {p.extracted_metrics
                    .slice(0, 4)
                    .map((m) => `${m.name}=${m.value}${m.unit || ""}`)
                    .join(" · ")}
                </div>
              )}
              {p.doi ? <div className="muted mono">doi:{p.doi}</div> : null}
            </div>
          ))}
          {papers.length === 0 && (
            <p className="muted">no cached papers yet — run a research pass</p>
          )}
        </div>
      )}
    </div>
  );
}
