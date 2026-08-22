"use client";

export type MiniBar = {
  key: string;
  name: string;
  /** bar length, in the same units as `max` */
  value: number;
  /** optional hairline marker at an earlier value, e.g. the first residual */
  mark?: number;
  tone?: "accent" | "ok" | "warn" | "bad";
  readout: string;
  tip: string;
};

/**
 * The chart primitive the research lab uses instead of a numbers table: one row per series,
 * the number on the right, the sentence in the hover tooltip.
 */
export default function MiniBars({ bars, max }: { bars: MiniBar[]; max?: number }) {
  const ceiling = Math.max(
    max ?? 0,
    ...bars.map((b) => Math.max(Math.abs(b.value), Math.abs(b.mark ?? 0))),
  );
  const pct = (v: number) => (ceiling > 0 ? Math.min(100, (Math.abs(v) / ceiling) * 100) : 0);

  return (
    <div className="barchart">
      {bars.map((b) => (
        <div className="barrow" key={b.key}>
          <span className="name tip" data-tip={b.tip} tabIndex={0}>
            <span className="clip">{b.name}</span>
          </span>
          <span className="bartrack">
            <span
              className={`barfill${b.tone && b.tone !== "accent" ? ` ${b.tone}` : ""}`}
              style={{ width: `${pct(b.value)}%` }}
            />
            {b.mark !== undefined && (
              <span className="barmark" style={{ left: `${pct(b.mark)}%` }} />
            )}
          </span>
          <span className="readout">{b.readout}</span>
        </div>
      ))}
    </div>
  );
}
