"use client";

import type { Learned, Proposal } from "@/lib/api";
import MiniBars, { type MiniBar } from "@/components/MiniBars";
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
  const driftBars: MiniBar[] = (learned?.drift ?? []).map((d) => ({
    key: d.metric,
    name: d.metric,
    value: d.latest_abs_residual ?? 0,
    mark: d.first_abs_residual ?? undefined,
    tone: d.shrinking ? "ok" : "warn",
    readout: `rmse ${fmt(d.rmse)} · ${d.shrinking ? "shrinking" : "not yet"}`,
    tip: `${d.n} measurements · bias ${fmt(d.bias)} · rmse ${fmt(d.rmse)} · |residual| ${fmt(
      d.first_abs_residual,
    )} at the first version, ${fmt(d.latest_abs_residual)} now (marker = where it started)`,
  }));

  return (
    <div>
      {learned ? (
        <>
          <p>
            <b
              className="tip"
              data-tip="The merged knowledge base: what changed v1 to latest, whether in-silico vs wet-lab drift is shrinking, and the next version the daemon would build."
              tabIndex={0}
            >
              {learned.headline}
            </b>
          </p>
          {driftBars.length > 0 ? (
            <>
              <h3 className="subhead">In-silico vs wet-lab drift</h3>
              <MiniBars bars={driftBars} />
            </>
          ) : (
            <ul className="steps">
              {learned.lessons.slice(0, 6).map((l) => (
                <li key={l}>{l}</li>
              ))}
              {learned.lessons.length === 0 && <li>No cross-version lessons yet</li>}
            </ul>
          )}
        </>
      ) : (
        <p className="muted">No campaign knowledge yet</p>
      )}

      <div className="row" style={{ marginTop: 12 }}>
        <button
          type="button"
          data-testid="proposal-refresh"
          onClick={onProposal}
          disabled={busy === "proposal"}
        >
          {busy === "proposal" ? "Proposing…" : "Propose next version"}
        </button>
      </div>

      {proposal && !proposal.proposed && (
        <p className="muted" style={{ marginTop: 10 }} data-testid="proposal-blocked">
          No next version proposed for {proposal.target_metric}:{" "}
          {proposal.reason ?? "No candidate survived the filters"}
        </p>
      )}

      {proposal?.proposed && (
        <div style={{ marginTop: 10 }}>
          <h3 className="subhead">
            <span className="tip" data-tip={proposal.why_this_metric || ""} tabIndex={0}>
              {proposal.proposed.label} · Target {proposal.target_metric}
            </span>
          </h3>
          <p className="tip" data-tip={proposal.proposed.rationale} tabIndex={0}>
            Mutations:{" "}
            <span className="mono">{proposal.proposed.mutations.join(" + ") || "None"}</span>
          </p>
          <table>
            <thead>
              <tr>
                <th>Predicted wet-lab</th>
                <th>Value</th>
                <th>± sd</th>
                <th>Method (skill)</th>
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
