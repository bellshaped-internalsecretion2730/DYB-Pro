"use client";

import MiniBars, { type MiniBar } from "@/components/MiniBars";
import type { GraphNode } from "@/lib/api";

const TONE: Record<string, MiniBar["tone"]> = {
  composite: "accent",
  binding: "ok",
  stability: "ok",
  aggregation: "warn",
  immunogenicity: "bad",
};

function tone(name: string): MiniBar["tone"] {
  const key = Object.keys(TONE).find((k) => name.includes(k));
  return key ? TONE[key] : "accent";
}

/** Minimal graph row beneath the viewer: scores of this version, composite across versions. */
export default function MetricStrip({
  node,
  compareNode,
  versions,
}: {
  node: GraphNode | null;
  compareNode: GraphNode | null;
  versions: GraphNode[];
}) {
  if (!node) {
    return (
      <div className="metric-strip" data-testid="metric-strip">
        <span className="hint">metrics appear with the selected version</span>
      </div>
    );
  }

  const scores: MiniBar[] = Object.entries(node.scores)
    .filter(([, v]) => Number.isFinite(v))
    .slice(0, 8)
    .map(([name, value]) => ({
      key: name,
      name,
      value,
      mark: compareNode?.scores[name],
      tone: tone(name),
      readout: value.toFixed(3),
      tip: compareNode
        ? `${name} ${value.toFixed(3)} · ${compareNode.label ?? compareNode.short_id} ${
            compareNode.scores[name]?.toFixed(3) ?? "n/a"
          } (hairline). Deterministic toolkit proxies, uncalibrated against measurements.`
        : `${name} ${value.toFixed(3)} — deterministic toolkit proxy, uncalibrated against measurements`,
    }));

  const trend: MiniBar[] = versions
    .filter((v) => Number.isFinite(v.scores.composite_score))
    .slice(0, 6)
    .map((v) => ({
      key: v.id,
      name: v.label ?? v.short_id,
      value: v.scores.composite_score,
      tone: v.passed_filters ? "accent" : "bad",
      readout: v.scores.composite_score.toFixed(3),
      tip: v.passed_filters
        ? `${v.label ?? v.short_id}: composite ${v.scores.composite_score.toFixed(3)}, passed every filter`
        : `${v.label ?? v.short_id}: composite ${v.scores.composite_score.toFixed(
            3,
          )}, failed ${v.failed_filters.join(", ") || "a filter"}`,
    }));

  return (
    <div className="metric-strip" data-testid="metric-strip">
      <div>
        <span
          className="label tip"
          data-tip="scores of the selected version, straight from the commit record"
          tabIndex={0}
        >
          this version
        </span>
        {scores.length > 0 ? <MiniBars bars={scores} max={1} /> : <span className="hint">no scores</span>}
      </div>
      <div>
        <span
          className="label tip"
          data-tip="composite score per version, newest first — red bars failed a hard filter"
          tabIndex={0}
        >
          composite across versions
        </span>
        {trend.length > 0 ? <MiniBars bars={trend} max={1} /> : <span className="hint">no versions scored</span>}
      </div>
    </div>
  );
}
