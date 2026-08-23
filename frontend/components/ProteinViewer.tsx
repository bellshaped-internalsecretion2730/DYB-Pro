"use client";

import { useMemo, useState } from "react";
import type { GraphNode } from "@/lib/api";

/** Deterministic pseudo-random generator so a commit always renders the same trace. */
function seeded(seed: string) {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i += 1) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return () => {
    h ^= h << 13;
    h ^= h >>> 17;
    h ^= h << 5;
    return ((h >>> 0) % 10000) / 10000;
  };
}

export function residueIndex(mutation: string): number | null {
  const m = /(\d+)/.exec(mutation);
  return m ? Number(m[1]) : null;
}

type Point = { x: number; y: number };

function trace(seed: string, width: number, height: number, points = 14): Point[] {
  const rand = seeded(seed);
  const pad = 60;
  const step = (width - pad * 2) / (points - 1);
  return Array.from({ length: points }, (_, i) => ({
    x: pad + i * step,
    y: height / 2 + (rand() - 0.5) * (height - pad * 2),
  }));
}

function pathOf(pts: Point[]): string {
  if (pts.length === 0) return "";
  return pts
    .map((p, i) => {
      if (i === 0) return `M ${p.x.toFixed(1)} ${p.y.toFixed(1)}`;
      const prev = pts[i - 1];
      const cx = (prev.x + p.x) / 2;
      return `C ${cx.toFixed(1)} ${prev.y.toFixed(1)}, ${cx.toFixed(1)} ${p.y.toFixed(1)}, ${p.x.toFixed(1)} ${p.y.toFixed(1)}`;
    })
    .join(" ");
}

function Trace({
  node,
  color,
  showLabels,
  width,
  height,
}: {
  node: GraphNode;
  color: string;
  showLabels: boolean;
  width: number;
  height: number;
}) {
  const pts = useMemo(() => trace(node.id || node.short_id, width, height), [node.id, node.short_id, width, height]);
  const muts = node.mutations.filter(Boolean) as string[];
  return (
    <g>
      <path d={pathOf(pts)} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" opacity={0.9} />
      {muts.map((m, i) => {
        const p = pts[(residueIndex(m) ?? i * 3) % pts.length];
        return (
          <g key={`${m}-${i}`}>
            <circle cx={p.x} cy={p.y} r={3.5} fill="var(--accent)" />
            {showLabels && (
              <text className="res-label" x={p.x + 8} y={p.y - 6}>
                {m}
              </text>
            )}
          </g>
        );
      })}
    </g>
  );
}

export default function ProteinViewer({
  node,
  compareNode,
  sequence,
  onClearCompare,
}: {
  node: GraphNode | null;
  compareNode: GraphNode | null;
  sequence: string | null;
  onClearCompare: () => void;
}) {
  const [showLabels, setShowLabels] = useState(true);
  const [zoom, setZoom] = useState(1);
  const W = 900;
  const H = 520;
  const mutated = new Set(
    (node?.mutations.filter(Boolean) as string[] | undefined)?.map((m) => residueIndex(m)).filter(Boolean) as number[],
  );

  return (
    <div className="viewer" data-testid="protein-viewer">
      <div className="pane-header">
        <div className="row">
          <strong style={{ fontSize: 12.5 }}>{node ? node.label || node.short_id : "no version selected"}</strong>
          {node && <span className="mono" style={{ color: "var(--faint)" }}>{node.short_id}</span>}
          {node && <span className="badge">{node.branch}</span>}
          {node?.is_head && <span className="badge devin">head</span>}
          {node && !node.passed_filters && <span className="badge sim">filtered</span>}
          {compareNode && (
            <span className="badge" style={{ borderColor: "var(--accent-2)", color: "var(--accent-2)" }}>
              vs {compareNode.label || compareNode.short_id}
            </span>
          )}
        </div>
        <div className="row">
          <button
            className="tab"
            type="button"
            aria-selected={showLabels}
            onClick={() => setShowLabels((v) => !v)}
          >
            labels
          </button>
          {compareNode && (
            <button className="tab" type="button" onClick={onClearCompare}>
              exit compare
            </button>
          )}
        </div>
      </div>

      <div className="viewer-stage">
        {node ? (
          <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" aria-label="protein backbone schematic">
            <g transform={`translate(${(W * (1 - zoom)) / 2} ${(H * (1 - zoom)) / 2}) scale(${zoom})`}>
              {compareNode && (
                <Trace node={compareNode} color="var(--accent-2)" showLabels={showLabels} width={W} height={H} />
              )}
              <Trace node={node} color="var(--ok)" showLabels={showLabels} width={W} height={H} />
            </g>
          </svg>
        ) : (
          <div className="viewer-empty">
            <span>Run a design cycle or pick a version below.</span>
            <span className="mono" style={{ fontSize: 10 }}>
              the viewer renders the selected commit
            </span>
          </div>
        )}
      </div>

      {sequence && (
        <div className="seq-track" data-testid="sequence-track">
          {sequence.split("").map((ch, i) => (
            <span className={mutated.has(i + 1) ? "res mut" : "res"} key={i}>
              {ch}
            </span>
          ))}
        </div>
      )}

      <div className="camera-bar">
        <div className="row">
          <button className="tab" type="button" onClick={() => setZoom((z) => Math.min(2.4, z + 0.2))}>
            zoom in
          </button>
          <button className="tab" type="button" onClick={() => setZoom((z) => Math.max(0.6, z - 0.2))}>
            zoom out
          </button>
          <button className="tab" type="button" onClick={() => setZoom(1)}>
            reset
          </button>
          <span
            className="tip"
            data-tip="a synthetic layout of the backbone, not coordinates — the 3D view renders the commit's coarse Cα trace"
            tabIndex={0}
            style={{ color: "var(--faint)" }}
          >
            2D schematic · switch to 3D for coordinates
          </span>
        </div>
        <span>
          {node?.scores?.composite_score !== undefined
            ? `composite ${node.scores.composite_score.toFixed(3)}`
            : "no score"}
          {node?.agent_role ? ` · ${node.agent_role}` : ""}
          {node?.cycle_round ? ` · r${node.cycle_round}` : ""}
        </span>
      </div>
    </div>
  );
}
