"use client";

import { useState } from "react";
import { api, type Shortlist } from "@/lib/api";

function money(v: number) {
  return `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

export default function ShortlistPanel({
  shortlist,
  cycleId,
}: {
  shortlist: Shortlist | null;
  cycleId: string | null;
}) {
  const [open, setOpen] = useState<string | null>(null);

  if (!shortlist) {
    return (
      <p className="hint">
        The wet-lab shortlist appears once a cycle commits designs — ranked, with uncertainty and
        “why this, not that”.
      </p>
    );
  }
  const { pack, ranked, narrative } = shortlist;
  const econ = pack.economics;

  return (
    <div>
      <p style={{ marginTop: 0 }}>
        <b>{narrative.headline}</b>
      </p>
      <div className="row" style={{ marginBottom: 10 }}>
        <span className="badge">
          {econ.shortlist_size} of {econ.candidate_pool} candidates ordered
        </span>
        <span className="badge devin">
          {money(econ.savings_usd)} avoided ({econ.savings_pct}%)
        </span>
        <span className="badge">shortlist {money(econ.shortlist_cost_usd)}</span>
        <span className="badge">test-everything {money(econ.test_everything_cost_usd)}</span>
        <span className="badge">~{econ.expected_hits} expected hits</span>
      </div>
      {cycleId && (
        <div className="row" style={{ marginBottom: 12 }}>
          {(["csv", "json", "fasta"] as const).map((fmt) => (
            <a key={fmt} href={api.downloadUrl(`/cycles/${cycleId}/export?fmt=${fmt}`)} download>
              <button className="secondary" type="button">
                export {fmt.toUpperCase()}
              </button>
            </a>
          ))}
        </div>
      )}
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Design</th>
            <th>Score ± unc.</th>
            <th>Build</th>
            <th>Why this</th>
          </tr>
        </thead>
        <tbody>
          {pack.shortlist.map((item) => (
            <tr key={item.label}>
              <td className="mono">{item.rank}</td>
              <td>
                <b>{item.label}</b>
                <div className="muted">{item.mutations.filter(Boolean).join(", ") || "parent"}</div>
                <button
                  className="secondary"
                  type="button"
                  style={{ marginTop: 4, padding: "3px 8px", fontSize: 11 }}
                  onClick={() => setOpen(open === item.label ? null : item.label)}
                >
                  {open === item.label ? "hide" : "construct / primers / assays"}
                </button>
                {open === item.label && (
                  <div style={{ marginTop: 6 }}>
                    <div className="muted">
                      {item.construct.vector} · {item.construct.expression_host} ·{" "}
                      {item.construct.orf_length_bp} bp ORF · {item.cost.route}
                    </div>
                    <div className="mono" style={{ wordBreak: "break-all", margin: "4px 0" }}>
                      {item.sequence}
                    </div>
                    {item.primers.map((p) => (
                      <div key={p.mutation} className="mono muted">
                        {p.mutation}: F {p.forward} / R {p.reverse} (Tm {p.tm_c}°C)
                      </div>
                    ))}
                    {item.assay_plan.map((a) => (
                      <div key={a.assay} className="muted">
                        {a.assay} → {a.predicted_signal} ({money(a.estimated_cost_usd)})
                      </div>
                    ))}
                    <div className="muted" style={{ marginTop: 4 }}>
                      {item.citations.join(" · ")}
                    </div>
                  </div>
                )}
              </td>
              <td className="mono">
                {item.composite_score.toFixed(3)}
                <div className="muted">conf {item.confidence.toFixed(2)}</div>
                {item.pareto_optimal && <span className="pill ok">pareto</span>}
              </td>
              <td className="mono">{money(item.cost.total_usd)}</td>
              <td>
                {item.why}
                {item.why_not_next && (
                  <div className="muted" style={{ marginTop: 4 }}>
                    not next: {item.why_not_next}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2 style={{ marginTop: 16 }}>Rejected / deprioritized</h2>
      <table>
        <tbody>
          {ranked
            .filter((r) => !pack.shortlist.some((s) => s.label === r.label))
            .slice(0, 8)
            .map((r) => (
              <tr key={r.label}>
                <td>
                  <b>{r.label}</b>
                  <div className="muted">{r.composite_score.toFixed(3)}</div>
                </td>
                <td className="muted">
                  {r.excluded_reason ||
                    (r.failed_filters.length ? `failed ${r.failed_filters.join(", ")}` : r.why)}
                </td>
              </tr>
            ))}
        </tbody>
      </table>
      {pack.risks.length > 0 && (
        <>
          <h2 style={{ marginTop: 16 }}>Risks</h2>
          {pack.risks.map((r) => (
            <p key={r.label} className="muted" style={{ margin: "2px 0" }}>
              <b>{r.label}</b>: {r.notes.join("; ")}
            </p>
          ))}
        </>
      )}
    </div>
  );
}
