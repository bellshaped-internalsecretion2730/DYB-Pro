"use client";

import type {
  GateCriterion,
  GateDecision,
  ProgramDetail,
  ProgramExperiment,
  ProgramMolecule,
  ProgramResearchEvent,
} from "@/lib/api";

const DECISION_LABEL: Record<string, string> = {
  go: "GO — advance a stage",
  no_go: "NO-GO — hold at this stage",
  recycle: "RECYCLE — another design round",
  kill: "KILL — stop the program",
};

function money(usd: number): string {
  if (usd >= 1_000_000) return `$${(usd / 1_000_000).toFixed(1)}M`;
  if (usd >= 1_000) return `$${(usd / 1_000).toFixed(0)}k`;
  return `$${usd.toFixed(0)}`;
}

export function StageLadder({ detail }: { detail: ProgramDetail }) {
  const current = detail.program.stage_order;
  return (
    <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
      {detail.ladder.map((s) => {
        const state = s.order < current ? "ok" : s.order === current ? "" : "no";
        return (
          <span className={`pill ${state}`} key={s.key} title={s.key}>
            {s.order}. {s.name}
          </span>
        );
      })}
    </div>
  );
}

/** The gate is the decision itself: every criterion, what was required, what was observed. */
export function GatePanel({
  gate,
  onDecide,
  busy,
}: {
  gate: GateDecision | null;
  onDecide: (approve: boolean) => void;
  busy: boolean;
}) {
  if (!gate) return <p className="muted">No gate evaluated yet — run a round</p>;
  const missing = gate.criteria.filter((c) => c.missing_evidence);
  return (
    <>
      <div className="row" style={{ marginBottom: 8 }}>
        <span className={`pill ${gate.decision === "go" ? "ok" : "no"}`}>
          {DECISION_LABEL[gate.decision] ?? gate.decision}
        </span>
        <span className="muted">
          Score {gate.score.toFixed(2)} · stage {gate.stage} · autonomy L{gate.autonomy_level}
        </span>
      </div>
      <p style={{ marginTop: 0 }}>{gate.rationale}</p>
      {gate.requires_approval && gate.approval_status === "pending" && (
        <div className="row" style={{ marginBottom: 8 }}>
          <span className="badge bad">Human signature required</span>
          <span className="muted">{gate.approval_reason}</span>
          <button type="button" disabled={busy} onClick={() => onDecide(true)}>
            Approve
          </button>
          <button className="secondary" type="button" disabled={busy} onClick={() => onDecide(false)}>
            Reject
          </button>
        </div>
      )}
      {gate.approval_status === "approved" && <span className="badge">Approved by a human</span>}
      {gate.approval_status === "rejected" && <span className="badge bad">Rejected by a human</span>}
      <table>
        <thead>
          <tr>
            <th>Criterion</th>
            <th>Requirement</th>
            <th>Observed</th>
            <th>Blocking</th>
          </tr>
        </thead>
        <tbody>
          {gate.criteria.map((c: GateCriterion) => (
            <tr key={c.key}>
              <td>{c.label}</td>
              <td className="mono">{c.requirement}</td>
              <td className="mono">
                {c.observed === null ? (
                  <span className="muted">No evidence</span>
                ) : (
                  c.observed
                )}{" "}
                {c.passed ? <span className="pill ok">Pass</span> : <span className="pill no">Fail</span>}
              </td>
              <td>{c.blocking ? "Yes" : "No"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {missing.length > 0 && (
        <p className="hint">
          {missing.length} criteria have no evidence at all. Missing evidence is not a negative
          result: the gate holds the program instead of killing it.
        </p>
      )}
      {gate.recommended_actions.length > 0 && (
        <ul className="steps">
          {gate.recommended_actions.map((a) => (
            <li key={a}>{a}</li>
          ))}
        </ul>
      )}
    </>
  );
}

export function MoleculeTable({
  molecules,
  candidateId,
  onIngest,
}: {
  molecules: ProgramMolecule[];
  candidateId?: string | null;
  onIngest: (molecule: ProgramMolecule) => void;
}) {
  if (molecules.length === 0) return <p className="muted">No molecules committed yet</p>;
  return (
    <table>
      <thead>
        <tr>
          <th>Commit</th>
          <th>SMILES</th>
          <th>Score</th>
          <th>pKd (pred.)</th>
          <th>cLogP</th>
          <th>SA</th>
          <th>Verdict</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {molecules.map((m) => (
          <tr key={m.id}>
            <td className="mono">
              {m.short_id}
              {m.id === candidateId && <span className="pill ok">Candidate</span>}
            </td>
            <td className="mono">{m.smiles}</td>
            <td>{m.composite_score.toFixed(3)}</td>
            <td>{m.evaluation.binding?.pkd?.toFixed(2) ?? "—"}</td>
            <td>{m.evaluation.descriptors?.clogp?.toFixed(2) ?? "—"}</td>
            <td>{m.evaluation.synthesis?.sa_score?.toFixed(2) ?? "—"}</td>
            <td>
              {m.verdict}
              {(m.evaluation.liabilities?.blocking?.length ?? 0) > 0 && (
                <span className="pill no">{m.evaluation.liabilities?.blocking?.join(", ")}</span>
              )}
            </td>
            <td>
              <button className="secondary" type="button" onClick={() => onIngest(m)}>
                Add assay result
              </button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ExperimentTable({ experiments }: { experiments: ProgramExperiment[] }) {
  if (experiments.length === 0)
    return <p className="muted">No assay would change the current decision</p>;
  const total = experiments.reduce((sum, e) => sum + e.cost_usd, 0);
  return (
    <>
      <p className="hint">
        {experiments.length} proposed experiments · {money(total)} · longest turnaround{" "}
        {Math.max(...experiments.map((e) => e.turnaround_days))} days. Costs are CRO list-price
        order-of-magnitude estimates.
      </p>
      <table>
        <thead>
          <tr>
            <th>Assay</th>
            <th>Endpoint</th>
            <th>Predicted</th>
            <th>Cost</th>
            <th>Days</th>
            <th>Falsifies</th>
          </tr>
        </thead>
        <tbody>
          {experiments.map((e) => (
            <tr key={e.id}>
              <td>
                {e.assay}
                {e.blocking && <span className="pill no">Blocking</span>}
              </td>
              <td className="mono">
                {e.endpoint} ({e.unit})
              </td>
              <td className="mono">{e.predicted_value ?? "—"}</td>
              <td>{money(e.cost_usd)}</td>
              <td>{e.turnaround_days}</td>
              <td className="muted">{e.falsification}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export function EconomicsPanel({ detail }: { detail: ProgramDetail }) {
  const e = detail.economics;
  return (
    <>
      <table>
        <tbody>
          <tr>
            <td>Spent so far (agents + platform + assays)</td>
            <td className="mono">{money(e.autonomous.total_cost_usd)}</td>
          </tr>
          <tr>
            <td>Human team baseline for the same stages</td>
            <td className="mono">{money(e.human_baseline.total_cost_usd)}</td>
          </tr>
          <tr>
            <td>Elapsed vs baseline</td>
            <td className="mono">
              {e.autonomous.months_elapsed} vs {e.human_baseline.months} months
            </td>
          </tr>
          <tr>
            <td>Probability of reaching first-in-human (portfolio statistic)</td>
            <td className="mono">{(e.forward_look.probability_of_reaching_fih * 100).toFixed(1)}%</td>
          </tr>
          <tr>
            <td>Risk-adjusted value</td>
            <td className="mono">{money(e.forward_look.risk_adjusted_value_usd)}</td>
          </tr>
        </tbody>
      </table>
      <p className="hint">{e.human_baseline.source}</p>
      <p className="hint">{e.forward_look.caveat}</p>
    </>
  );
}

export function DriftPanel({ detail }: { detail: ProgramDetail }) {
  const d = detail.drift;
  return (
    <>
      <div className="row">
        <span className="pill">{d.n} prediction/measurement pairs</span>
        <span className="pill">RMSE {d.rmse ?? "—"}</span>
        <span className="pill">bias {d.bias ?? "—"}</span>
      </div>
      <p>{d.interpretation}</p>
      {d.worst && (
        <p className="muted mono">
          worst: {d.worst.molecule_hash.slice(0, 10)} predicted {d.worst.predicted}, measured{" "}
          {d.worst.observed}
        </p>
      )}
      {d.method && <p className="muted">{d.method}</p>}
    </>
  );
}

export function ResearchLog({ events }: { events: ProgramResearchEvent[] }) {
  if (events.length === 0) return <p className="muted">No research events yet</p>;
  return (
    <div className="timeline">
      {events.map((e) => (
        <div className="event" key={e.id}>
          <div className="kind">
            {e.kind} · {e.role} · {e.provider}
          </div>
          <div>{e.claim}</div>
          {e.citation ? (
            <div className="muted mono">{e.citation}</div>
          ) : (
            <div className="muted">No citation — cannot move a gate</div>
          )}
        </div>
      ))}
    </div>
  );
}
