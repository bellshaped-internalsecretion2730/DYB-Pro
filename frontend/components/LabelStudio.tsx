"use client";

import { useState } from "react";
import type { Commit, LabelNode, LabelsResponse } from "@/lib/api";

const KINDS = [
  { kind: "active_site", hue: "var(--accent)" },
  { kind: "liability", hue: "var(--bad)" },
  { kind: "epitope", hue: "var(--accent-2)" },
  { kind: "mutation_intent", hue: "var(--warn)" },
  { kind: "note", hue: "var(--muted)" },
];

function colourFor(kind: string): string {
  return KINDS.find((k) => k.kind === kind)?.hue || "var(--muted)";
}

function LabelTree({ nodes, depth = 0 }: { nodes: LabelNode[]; depth?: number }) {
  return (
    <>
      {nodes.map((n) => (
        <div key={n.id} style={{ paddingLeft: depth * 14 }} title={n.note || undefined}>
          <span className="pill" style={{ color: colourFor(n.kind), borderColor: colourFor(n.kind) }}>
            {n.kind}
          </span>{" "}
          <b>{n.name || "(unnamed)"}</b>{" "}
          <span className="mono muted">[{n.residues.join(", ") || "no residues"}]</span>
          {n.superseded ? <span className="muted"> · refined</span> : null}
          {n.refinements.length > 0 && <LabelTree nodes={n.refinements} depth={depth + 1} />}
        </div>
      ))}
    </>
  );
}

export default function LabelStudio({
  commit,
  labels,
  busy,
  onCreate,
}: {
  commit: Commit | null;
  labels: LabelsResponse | null;
  busy: string | null;
  onCreate: (body: {
    kind: string;
    name: string;
    residues: number[];
    note: string;
    parent_label_id?: string;
  }) => Promise<void>;
}) {
  const [selected, setSelected] = useState<number[]>([]);
  const [kind, setKind] = useState("liability");
  const [name, setName] = useState("");
  const [note, setNote] = useState("");
  const [parent, setParent] = useState("");

  if (!commit) return <p className="muted">Select a version to label residues</p>;

  const byResidue = new Map<number, string>();
  for (const lb of labels?.labels || []) {
    if (lb.superseded_by) continue;
    for (const r of lb.residues) byResidue.set(r, lb.kind);
  }

  function toggle(pos: number) {
    setSelected((prev) => (prev.includes(pos) ? prev.filter((p) => p !== pos) : [...prev, pos].sort((a, b) => a - b)));
  }

  async function submit() {
    await onCreate({
      kind,
      name,
      residues: selected,
      note,
      parent_label_id: parent || undefined,
    });
    setSelected([]);
    setName("");
    setNote("");
    setParent("");
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 6 }}>
        <span
          className="hint tip"
          style={{ margin: 0 }}
          data-tip="Click residues, then write a label. Labels are versioned research objects — saving one wakes the daemon immediately."
          tabIndex={0}
        >
          {commit.label} · {commit.sequence.length} aa
        </span>
        <span className="legend">
          {KINDS.map((k) => (
            <span key={k.kind} style={{ color: k.hue }}>
              <i />
              {k.kind.replace("_", " ")}
            </span>
          ))}
        </span>
      </div>
      <div className="seqgrid" aria-label="sequence residue selector">
        {commit.sequence.split("").map((aa, idx) => {
          const pos = idx + 1;
          const existing = byResidue.get(pos);
          const isSel = selected.includes(pos);
          return (
            <button
              key={pos}
              type="button"
              title={`Residue ${pos} ${aa}${existing ? ` · ${existing}` : ""}`}
              onClick={() => toggle(pos)}
              className={`residue${isSel ? " sel" : ""}`}
              style={existing ? { borderColor: colourFor(existing), color: colourFor(existing) } : undefined}
            >
              {aa}
            </button>
          );
        })}
      </div>
      <div className="row" style={{ marginTop: 10 }}>
        <select value={kind} onChange={(e) => setKind(e.target.value)} style={{ maxWidth: 190 }}>
          {KINDS.map((k) => (
            <option key={k.kind} value={k.kind}>
              {k.kind}
            </option>
          ))}
        </select>
        <input
          type="text"
          placeholder="Label name (e.g. hydrophobic patch)"
          value={name}
          onChange={(e) => setName(e.target.value)}
          style={{ maxWidth: 260 }}
        />
        <select value={parent} onChange={(e) => setParent(e.target.value)} style={{ maxWidth: 220 }}>
          <option value="">— new label —</option>
          {(labels?.labels || []).map((lb) => (
            <option key={lb.id} value={lb.id}>
              refine: {lb.name || lb.kind}
            </option>
          ))}
        </select>
      </div>
      <textarea
        style={{ marginTop: 8, minHeight: 60 }}
        placeholder="Free-text scientific note the daemon will read on the next pass"
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="row" style={{ marginTop: 8 }}>
        <button
          type="button"
          data-testid="label-create"
          onClick={submit}
          disabled={selected.length === 0 || busy === "label"}
        >
          {busy === "label" ? "Saving…" : `Save label (${selected.length} residues)`}
        </button>
        {selected.length > 0 && (
          <button type="button" className="secondary" onClick={() => setSelected([])}>
            Clear selection
          </button>
        )}
        <span className="muted mono">{selected.join(", ")}</span>
      </div>
      <div style={{ marginTop: 12 }}>
        <h3 className="subhead">Labels on this version</h3>
        {labels && labels.tree.length > 0 ? (
          <LabelTree nodes={labels.tree} />
        ) : (
          <p className="muted">None yet</p>
        )}
      </div>
    </div>
  );
}
