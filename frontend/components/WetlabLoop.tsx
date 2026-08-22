"use client";

import { useState } from "react";
import type { Commit, RiskReport, WetlabPlan, WetlabResult } from "@/lib/api";
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
}) {
  const [host, setHost] = useState(HOSTS[0]);
  const [maxAssays, setMaxAssays] = useState(4);
  const [seed, setSeed] = useState(7);
  const [paste, setPaste] = useState("");
  const [notes, setNotes] = useState("");

  if (!commit) return <p className="muted">select a version to run the wet-lab loop</p>;

  const latest = results[results.length - 1] || null;

  return (
    <div>
      <p className="hint">
        Predict → propose the cheapest informative pack → record results (simulator or real) →
        residuals recalibrate the next proposal. Every metric comes from a tested skill.
      </p>

      <h3 className="subhead">predicted risk</h3>
      {risk ? (
        <table>
          <thead>
            <tr>
              <th>risk</th>
              <th>level</th>
              <th>mitigation</th>
            </tr>
          </thead>
          <tbody>
            {risk.risk.risks.slice(0, 6).map((r) => (
              <tr key={r.risk}>
                <td>
                  {r.risk}
                  <div className="muted">{r.detail}</div>
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
                  no flagged liabilities for this version
                </td>
              </tr>
            )}
          </tbody>
        </table>
      ) : (
        <p className="muted">loading predictions…</p>
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
          {busy === "plan" ? "planning…" : "propose wet-lab pack"}
        </button>
      </div>

      {plan && (
        <div style={{ marginTop: 12 }}>
          <h3 className="subhead">
            pack · ${fmt(plan.total_cost_usd)} · {fmt(plan.information_per_usd, 4)} info/$
          </h3>
          <p className="muted">{plan.rationale}</p>
          <table>
            <thead>
              <tr>
                <th>construct</th>
                <th>vector / host</th>
                <th>build route</th>
              </tr>
            </thead>
            <tbody>
              {plan.constructs.map((c) => (
                <tr key={c.label}>
                  <td>
                    {c.label}
                    <div className="mono muted">{c.orf.slice(0, 48)}…</div>
                  </td>
                  <td className="muted">
                    {c.vector} · {c.expression_host}
                  </td>
                  <td className="muted">
                    {c.route} · ${fmt(c.build_cost_usd)}
                    <div>{c.notes}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <table style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>assay</th>
                <th>measures</th>
                <th>cost</th>
                <th>days</th>
              </tr>
            </thead>
            <tbody>
              {plan.assays.map((a) => (
                <tr key={a.assay}>
                  <td>{a.assay}</td>
                  <td className="muted">{a.measures.join(", ")}</td>
                  <td>${fmt(a.cost_usd)}</td>
                  <td>{a.days}</td>
                </tr>
              ))}
            </tbody>
          </table>
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
              {busy === "simulate" ? "running simulator…" : "register simulated results"}
            </button>
            <span className="badge sim">simulator, not a real lab</span>
          </div>
        </div>
      )}

      <div style={{ marginTop: 12 }}>
        <h3 className="subhead">paste real results (CSV or JSON)</h3>
        <textarea
          style={{ minHeight: 70 }}
          placeholder={"metric,value,unit\nTm,58.4,C\nyield,12.1,mg/L\nexpressed,1,"}
          value={paste}
          onChange={(e) => setPaste(e.target.value)}
        />
        <div className="row" style={{ marginTop: 8 }}>
          <input
            type="text"
            placeholder="notes (operator, buffer, date)"
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
            {busy === "ingest" ? "ingesting…" : "register real results"}
          </button>
        </div>
        {unknownMetrics.length > 0 && (
          <p className="err">unrecognised metrics ignored: {unknownMetrics.join(", ")}</p>
        )}
      </div>

      {latest && (
        <div style={{ marginTop: 12 }}>
          <h3 className="subhead">
            measured vs predicted · source{" "}
            <span className={`pill ${latest.source === "simulator" ? "" : "ok"}`}>{latest.source}</span>
          </h3>
          <table>
            <thead>
              <tr>
                <th>metric</th>
                <th>measured</th>
                <th>predicted</th>
                <th>residual</th>
              </tr>
            </thead>
            <tbody>
              {latest.measurements.map((m) => {
                const r = latest.residuals[m.metric];
                return (
                  <tr key={m.metric}>
                    <td>
                      {m.metric}
                      <span className="muted"> {m.unit}</span>
                    </td>
                    <td>{fmt(m.value)}</td>
                    <td>{r ? fmt(r.predicted) : "—"}</td>
                    <td className={r && Math.abs(r.residual) > 0 ? "muted" : ""}>
                      {r ? fmt(r.residual) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="muted">{latest.error_model?.description || ""}</p>
        </div>
      )}
    </div>
  );
}
