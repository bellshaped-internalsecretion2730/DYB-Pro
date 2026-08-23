"use client";

import { useEffect, useState } from "react";
import type { Commit, RiskReport, WetlabPlan, WetlabResult } from "@/lib/api";
import MiniBars, { type MiniBar } from "@/components/MiniBars";
import { fmt } from "@/lib/research";

// Must match the backend host presets in app/services/wetlab_loop.py: an unknown host
// silently falls back to BL21 predictions.
const HOSTS = [
  "E. coli BL21(DE3)",
  "E. coli SHuffle T7",
  "E. coli periplasm (pelB)",
  "HEK293-F transient",
];

export default function WetlabLoop({
  commit,
  risk,
  plan,
  results,
  busy,
  unknownMetrics,
  onPlan,
  onSimulate,
  onSubmit,
  onContextChange,
}: {
  commit: Commit | null;
  risk: RiskReport | null;
  plan: WetlabPlan | null;
  results: WetlabResult[];
  busy: string | null;
  unknownMetrics: string[];
  onPlan: (host: string, maxAssays: number) => Promise<void>;
  onSimulate: (seed: number) => Promise<void>;
  onSubmit: (text: string, notes: string) => Promise<void>;
  /** The host/notes the scientist picked here travel with an explicit swarm handoff. */
  onContextChange?: (ctx: { host: string; notes: string }) => void;
}) {
  const [host, setHost] = useState(HOSTS[0]);
  const [maxAssays, setMaxAssays] = useState(4);
  const [seed, setSeed] = useState(7);
  const [paste, setPaste] = useState("");
  const [notes, setNotes] = useState("");

  useEffect(() => {
    onContextChange?.({ host, notes });
  }, [host, notes, onContextChange]);

  if (!commit) return <p className="muted">Select a version to run the wet-lab loop</p>;

  const latest = results[results.length - 1] || null;

  return (
    <div>
      <h3 className="subhead">
        <span
          className="tip"
          data-tip="Predict, propose the cheapest informative pack, record results (simulator or real); the residuals recalibrate the next proposal. Every metric comes from a tested skill."
          tabIndex={0}
        >
          Predicted risk
        </span>
      </h3>
      {risk ? (
        <table>
          <thead>
            <tr>
              <th>Risk</th>
              <th>Level</th>
              <th>Mitigation</th>
            </tr>
          </thead>
          <tbody>
            {risk.risk.risks.slice(0, 6).map((r) => (
              <tr key={r.risk}>
                <td>
                  <span className="tip" data-tip={r.detail} tabIndex={0}>
                    {r.risk}
                  </span>
                </td>
                <td>
                  <span className={`pill ${r.level === "high" ? "no" : r.level === "low" ? "ok" : ""}`}>
                    {r.level}
                  </span>
                </td>
                <td className="muted">{r.mitigation}</td>
              </tr>
            ))}
            {risk.risk.risks.length === 0 && (
              <tr>
                <td colSpan={3} className="muted">
                  No flagged liabilities for this version
                </td>
              </tr>
            )}
          </tbody>
        </table>
      ) : (
        <p className="muted">Loading predictions…</p>
      )}

      <div className="row" style={{ marginTop: 10 }}>
        <select value={host} onChange={(e) => setHost(e.target.value)} style={{ maxWidth: 220 }}>
          {HOSTS.map((h) => (
            <option key={h} value={h}>
              {h}
            </option>
          ))}
        </select>
        <select
          value={maxAssays}
          onChange={(e) => setMaxAssays(Number(e.target.value))}
          style={{ maxWidth: 130 }}
        >
          {[1, 2, 3, 4, 5, 6].map((n) => (
            <option key={n} value={n}>
              {n} assays
            </option>
          ))}
        </select>
        <button
          type="button"
          data-testid="wetlab-plan"
          onClick={() => onPlan(host, maxAssays)}
          disabled={busy === "plan"}
        >
          {busy === "plan" ? "Planning…" : "Propose wet-lab pack"}
        </button>
      </div>

      {plan && (
        <div style={{ marginTop: 12 }}>
          <h3 className="subhead">
            <span className="tip" data-tip={plan.rationale} tabIndex={0}>
              Proposed pack
            </span>
          </h3>
          <div className="kpis">
            <div className="kpi">
              <span className="k">Cost</span>
              <span className="v">${fmt(plan.total_cost_usd)}</span>
            </div>
            <div className="kpi">
              <span className="k">Info/$</span>
              <span className="v">{fmt(plan.information_per_usd, 4)}</span>
            </div>
            <div className="kpi">
              <span className="k">Assays</span>
              <span className="v">{plan.assays.length}</span>
            </div>
            <div className="kpi">
              <span className="k">Days</span>
              <span className="v">{Math.max(0, ...plan.assays.map((a) => a.days))}</span>
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th>Construct</th>
                <th>Vector / host</th>
                <th>Build route</th>
              </tr>
            </thead>
            <tbody>
              {plan.constructs.map((c) => (
                <tr key={c.label}>
                  <td>
                    <span className="tip mono" data-tip={c.orf} tabIndex={0}>
                      {c.label}
                    </span>
                  </td>
                  <td className="muted">
                    {c.vector} · {c.expression_host}
                  </td>
                  <td className="muted">
                    <span className="tip" data-tip={c.notes} tabIndex={0}>
                      {c.route} · ${fmt(c.build_cost_usd)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <h3 className="subhead">Assay spend</h3>
          <MiniBars
            bars={plan.assays.map((a) => ({
              key: a.assay,
              name: a.assay,
              value: a.cost_usd,
              readout: `$${fmt(a.cost_usd)} · ${a.days}d`,
              tip: `Measures ${a.measures.join(", ")} · $${fmt(a.cost_usd)} over ${a.days} day(s)`,
            }))}
          />
          <div className="row" style={{ marginTop: 10 }}>
            <input
              type="text"
              value={String(seed)}
              onChange={(e) => setSeed(Number(e.target.value) || 0)}
              style={{ maxWidth: 110 }}
              aria-label="simulator seed"
            />
            <button
              type="button"
              data-testid="wetlab-simulate"
              onClick={() => onSimulate(seed)}
              disabled={busy === "simulate"}
            >
              {busy === "simulate" ? "Running simulator…" : "Register simulated results"}
            </button>
            <span className="badge sim">Simulator, not a real lab</span>
          </div>
        </div>
      )}

      <div style={{ marginTop: 12 }}>
        <h3 className="subhead">Paste real results (CSV or JSON)</h3>
        <textarea
          style={{ minHeight: 70 }}
          placeholder={"metric,value,unit\nTm,58.4,C\nyield,12.1,mg/L\nexpressed,1,"}
          value={paste}
          onChange={(e) => setPaste(e.target.value)}
        />
        <div className="row" style={{ marginTop: 8 }}>
          <input
            type="text"
            placeholder="Notes (operator, buffer, date)"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            style={{ maxWidth: 300 }}
          />
          <button
            type="button"
            className="secondary"
            onClick={() => onSubmit(paste, notes).then(() => setPaste(""))}
            disabled={!paste.trim() || busy === "ingest"}
          >
            {busy === "ingest" ? "Ingesting…" : "Register real results"}
          </button>
        </div>
        {unknownMetrics.length > 0 && (
          <p className="err">unrecognised metrics ignored: {unknownMetrics.join(", ")}</p>
        )}
      </div>

      {latest && (
        <div style={{ marginTop: 12 }}>
          <h3 className="subhead">
            <span
              className="tip"
              data-tip={
                latest.error_model?.description ||
                "Bar length is |measured - predicted|; the readout carries both numbers."
              }
              tabIndex={0}
            >
              Measured vs predicted
            </span>{" "}
            <span className={`pill ${latest.source === "simulator" ? "" : "ok"}`}>{latest.source}</span>
          </h3>
          <MiniBars
            bars={latest.measurements.map((m): MiniBar => {
              const r = latest.residuals[m.metric];
              return {
                key: m.metric,
                name: m.metric,
                value: r ? Math.abs(r.residual) : 0,
                tone: r && Math.abs(r.residual) > 0 ? "warn" : "ok",
                readout: `${fmt(m.value)} vs ${r ? fmt(r.predicted) : "—"}${m.unit ? ` ${m.unit}` : ""}`,
                tip: r
                  ? `measured ${fmt(m.value)}${m.unit ? ` ${m.unit}` : ""}, predicted ${fmt(
                      r.predicted,
                    )} · residual ${fmt(r.residual)}`
                  : `measured ${fmt(m.value)}${m.unit ? ` ${m.unit}` : ""} · no prediction to compare`,
              };
            })}
          />
        </div>
      )}
    </div>
  );
}
