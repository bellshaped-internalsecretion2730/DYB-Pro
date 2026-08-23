"use client";

import type { GraphNode } from "@/lib/api";

function glyphPath(node: GraphNode): string {
  let h = 0;
  const src = node.id || node.short_id;
  for (let i = 0; i < src.length; i += 1) h = (h * 31 + src.charCodeAt(i)) % 1000;
  const a = 6 + (h % 12);
  const b = 6 + ((h >> 2) % 14);
  const c = 6 + ((h >> 4) % 12);
  return `M 2 ${20 - a} C 20 ${2 + b}, 40 ${24 - c}, 58 ${8 + (h % 8)} S 84 ${20 - b}, 98 ${10 + c}`;
}

export default function FoldStrip({
  nodes,
  selectedId,
  compareId,
  onSelect,
  onCompare,
}: {
  nodes: GraphNode[];
  selectedId: string | null;
  compareId: string | null;
  onSelect: (node: GraphNode) => void;
  onCompare: (node: GraphNode) => void;
}) {
  return (
    <section className="fold-strip" data-testid="fold-strip">
      <div className="pane-header" style={{ position: "static" }}>
        <span className="label">Folded versions</span>
        <span className="meta">
          {nodes.length} commits · click to load · drag one onto another to compare
        </span>
      </div>
      <div className="thumbs">
        {nodes.length === 0 && (
          <p className="hint" style={{ margin: 0 }}>
            Versions appear here as designs are committed
          </p>
        )}
        {nodes.map((n) => (
          <button
            type="button"
            className={compareId === n.id ? "thumb compare" : "thumb"}
            key={n.id}
            aria-current={selectedId === n.id}
            aria-label={`version ${n.label || n.short_id}`}
            onClick={() => onSelect(n)}
            draggable
            onDragStart={(e) => e.dataTransfer.setData("text/plain", n.id)}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              const dragged = nodes.find((x) => x.id === e.dataTransfer.getData("text/plain"));
              if (dragged && dragged.id !== n.id) {
                onSelect(n);
                onCompare(dragged);
              }
            }}
          >
            <svg viewBox="0 0 100 26" aria-hidden="true">
              <path
                d={glyphPath(n)}
                fill="none"
                stroke={n.passed_filters ? "var(--ok)" : "var(--warn)"}
                strokeWidth={1.4}
              />
            </svg>
            <span className="id">{n.label || n.short_id}</span>
            <span className="metric">
              {n.scores?.composite_score !== undefined ? n.scores.composite_score.toFixed(3) : "—"}
              {n.is_head ? " · head" : ""}
            </span>
            <span className="metric">{n.branch}</span>
          </button>
        ))}
      </div>
    </section>
  );
}
