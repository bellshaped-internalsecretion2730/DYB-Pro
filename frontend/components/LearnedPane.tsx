"use client";

import type { Learned, Proposal } from "@/lib/api";
import { fmt } from "@/lib/research";

export default function LearnedPane({
  learned,
  proposal,
  busy,
  onProposal,
}: {
  learned: Learned | null;
  proposal: Proposal | null;
  busy: string | null;
  onProposal: () => void;
}) {
  return (
    <div>
      <p className="hint">
        The merged knowledge base: what changed v1→latest, whether in-silico↔wet-lab drift is
        shrinking, and the next version the daemon would build.
      </p>
      {learned ? (
        <>
          <p>
            <b>{learned.headline}</b>
          </p>
          <ul className="steps">
            {learned.lessons.slice(0, 8).map((l) => (
              <li key={l}>{l}</li>
            ))}
            {learned.lessons.length === 0 && <li>no cross-version lessons yet</li>}
          </ul>
          {learned.drift.length > 0 && (
            <table style={{ marginTop: 10 }}>
              <thead>
                <tr>
                  <th>metric</th>
                  <th>n</th>
                  <th>bias</th>
                  <th>rmse</th>
                  <th>|resid| first → latest</th>
                </tr>
              </thead>
              <tbody>
                {learned.drift.map((d) => (
                  <tr key={d.metric}>
                    <td>{d.metric}</td>
                    <td>{d.n}</td>
                    <td>{fmt(d.bias)}</td>
                    <td>{fmt(d.rmse)}</td>
                    <td>
                      {fmt(d.first_abs_residual)} → {fmt(d.latest_abs_residual)}{" "}
                      <span className={`pill ${d.shrinking ? "ok" : "no"}`}>
                        {d.shrinking ? "shrinking" : "not yet"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      ) : (
        <p className="muted">no campaign knowledge yet</p>
      )}

      <div className="row" style={{ marginTop: 12 }}>
        <button
          type="button"
          data-testid="proposal-refresh"
          onClick={onProposal}
          disabled={busy === "proposal"}
        >
          {busy === "proposal" ? "proposing…" : "propose next version"}
        </button>
      </div>

      {proposal && !proposal.proposed && (
        <p className="muted" style={{ marginTop: 10 }} data-testid="proposal-blocked">
          no next version proposed for {proposal.target_metric}:{" "}
          {proposal.reason ?? "no candidate survived the filters"}
        </p>
      )}

      {proposal?.proposed && (
        <div style={{ marginTop: 10 }}>
          <h3 className="subhead">
            {proposal.proposed.label} · target {proposal.target_metric}
          </h3>
          <p className="muted">{proposal.why_this_metric}</p>
          <p>
            mutations:{" "}
            <span className="mono">{proposal.proposed.mutations.join(" + ") || "none"}</span>
          </p>
          <p className="muted">{proposal.proposed.rationale}</p>
          <table>
            <thead>
              <tr>
                <th>predicted wet-lab</th>
                <th>value</th>
                <th>± sd</th>
                <th>method (skill)</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(proposal.proposed.predicted_wetlab).map(([metric, p]) => (
                <tr key={metric}>
                  <td>
                    {metric} <span className="muted">{p.unit}</span>
                  </td>
                  <td>{fmt(p.value)}</td>
                  <td>{fmt(p.sd)}</td>
                  <td className="muted">
                    {p.method}
                    <div className="mono">{p.skill}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {(proposal.excluded_by_history ?? []).length > 0 && (
            <p className="muted">
              excluded by history: {proposal.excluded_by_history?.join(", ")}
            </p>
          )}
          <p className="muted">skills: {(proposal.skills_used ?? []).join(", ")}</p>
        </div>
      )}
    </div>
  );
}
