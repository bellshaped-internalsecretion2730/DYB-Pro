"use client";

import { useMemo, useState } from "react";
import type { Graph, GraphNode } from "@/lib/api";

const COL_W = 190;
const ROW_H = 74;
const R = 9;

type Placed = GraphNode & { x: number; y: number };

function layout(graph: Graph): { placed: Placed[]; width: number; height: number } {
  const depth = new Map<string, number>();
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const parents = new Map<string, string[]>();
  graph.edges.forEach((e) => {
    parents.set(e.target, [...(parents.get(e.target) || []), e.source]);
  });

  const depthOf = (id: string, seen = new Set<string>()): number => {
    if (depth.has(id)) return depth.get(id)!;
    if (seen.has(id)) return 0;
    seen.add(id);
    const ps = (parents.get(id) || []).filter((p) => byId.has(p));
    const d = ps.length === 0 ? 0 : Math.max(...ps.map((p) => depthOf(p, seen))) + 1;
    depth.set(id, d);
    return d;
  };
  graph.nodes.forEach((n) => depthOf(n.id));

  const rows = new Map<number, number>();
  const placed = graph.nodes.map((n) => {
    const d = depth.get(n.id) ?? 0;
    const row = rows.get(d) ?? 0;
    rows.set(d, row + 1);
    return { ...n, x: 40 + d * COL_W, y: 44 + row * ROW_H };
  });
  const width = 80 + (Math.max(0, ...placed.map((p) => p.x)) || 0);
  const height = 90 + (Math.max(0, ...placed.map((p) => p.y)) || 0);
  return { placed, width, height };
}

export default function VersionDag({ graph }: { graph: Graph | null }) {
  const [selected, setSelected] = useState<GraphNode | null>(null);
  const { placed, width, height } = useMemo(
    () => (graph ? layout(graph) : { placed: [], width: 320, height: 160 }),
    [graph],
  );
  const pos = new Map(placed.map((p) => [p.id, p]));

  if (!graph || graph.nodes.length === 0) {
    return <p className="hint">The version DAG fills in as designs are committed.</p>;
  }

  return (
    <div>
      <div className="dag">
        <svg width={Math.max(width, 320)} height={Math.max(height, 140)}>
          {graph.edges.map((e, i) => {
            const a = pos.get(e.source);
            const b = pos.get(e.target);
            if (!a || !b) return null;
            const mid = (a.x + b.x) / 2;
            return (
              <path
                key={i}
                d={`M ${a.x} ${a.y} C ${mid} ${a.y}, ${mid} ${b.y}, ${b.x} ${b.y}`}
                fill="none"
                stroke="var(--line-strong)"
                strokeWidth={1.6}
              />
            );
          })}
          {placed.map((n) => (
            <g
              key={n.id}
              onClick={() => setSelected(n)}
              style={{ cursor: "pointer" }}
              aria-label={`commit ${n.short_id}`}
            >
              <circle
                cx={n.x}
                cy={n.y}
                r={n.is_head ? R + 3 : R}
                fill={n.passed_filters ? "var(--ok)" : "var(--warn)"}
                stroke={selected?.id === n.id ? "var(--text)" : "var(--raised)"}
                strokeWidth={2}
              />
              <text x={n.x + 16} y={n.y - 2} fill="var(--text)" fontSize={11.5}>
                {n.label || n.short_id}
              </text>
              <text x={n.x + 16} y={n.y + 12} fill="var(--muted)" fontSize={10.5}>
                {n.branch}
                {n.cycle_round ? ` · r${n.cycle_round}` : ""}
                {n.scores?.composite_score !== undefined
                  ? ` · ${n.scores.composite_score.toFixed(3)}`
                  : ""}
              </text>
            </g>
          ))}
        </svg>
      </div>
      {selected ? (
        <div style={{ marginTop: 10 }}>
          <div className="row">
            <span className="badge">{selected.label}</span>
            <span className="mono muted">{selected.short_id}</span>
            <span className="badge">{selected.provider}</span>
            <span className="badge">{selected.agent_role}</span>
            {selected.devin_session_url && (
              <a className="badge" href={selected.devin_session_url} target="_blank" rel="noreferrer">
                Session ↗
              </a>
            )}
          </div>
          <p className="muted" style={{ marginBottom: 4 }}>
            {selected.mutations.filter(Boolean).join(", ") || "Root commit"}
            {selected.failed_filters.length > 0 && ` · failed: ${selected.failed_filters.join(", ")}`}
          </p>
          <p style={{ margin: 0 }}>{selected.rationale || selected.message}</p>
        </div>
      ) : (
        <p className="hint" style={{ marginTop: 8 }}>
          Click a node for provenance: mutations, scores, agent, provider and “why”.
        </p>
      )}
    </div>
  );
}
