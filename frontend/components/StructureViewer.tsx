"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { detectWebGL } from "@/lib/webgl";
import {
  COLOR_LABELS,
  REPR_LABELS,
  addStructure,
  clearMeasurements,
  clearPaint,
  clearSelection,
  createViewer,
  focusResidues,
  highlightResidues,
  clearHighlight,
  labelResidue,
  loadStructure,
  measureDistance,
  paintResidues,
  residueCount,
  resetCamera,
  screenshot,
  selectResidues,
  setColorTheme,
  setRepresentation,
  structureSource,
  superposeLoaded,
  type ColorTheme,
  type Repr,
} from "@/lib/molstar";
import type { PluginUIContext } from "molstar/lib/mol-plugin-ui/context";

/** Capabilities a Cα-only coarse model cannot honestly support, shown rather than hidden. */
const UNAVAILABLE: [string, string][] = [
  ["molecular surface / SASA", "needs side-chain heavy atoms; any Å² here would be invented"],
  ["pockets & druggability", "pocket detection needs atomic voids, not one point per residue"],
  ["side chains, H-bonds, salt bridges", "those atoms do not exist in the file"],
  ["DSSP secondary structure", "needs backbone N/C/O hydrogen bonds"],
  ["per-residue confidence", "the generator is deterministic, not a predictor — B-factors are 0.00"],
  ["conservation", "no alignment is computed anywhere in the pipeline"],
];

const REPRS: Repr[] = ["cartoon", "backbone", "spacefill"];
const COLORS: ColorTheme[] = ["sequence-id", "chain-id", "residue-name", "hydrophobicity", "uniform"];

const COLOR_TIPS: Record<ColorTheme, string> = {
  "sequence-id": "colour ramps along the chain from N- to C-terminus",
  "chain-id": "one colour per chain — the model emits a single chain A",
  "residue-name": "one colour per amino-acid identity",
  hydrophobicity: "residue hydrophobicity scale — a per-identity lookup, not a surface patch",
  uniform: "single colour, geometry only",
};

export function residueOf(mutation: string): number | null {
  const m = /(\d+)/.exec(mutation);
  return m ? Number(m[1]) : null;
}

export default function StructureViewer({
  commitId,
  compareCommitId,
  label,
  sequence,
  mutations,
  pasted,
  marked,
}: {
  commitId: string | null;
  compareCommitId?: string | null;
  label?: string | null;
  sequence?: string | null;
  mutations?: string[];
  pasted?: { text: string; name: string } | null;
  marked?: number[];
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const pluginRef = useRef<PluginUIContext | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "empty" | "error" | "nogl">(
    "idle",
  );
  const [detail, setDetail] = useState("");
  const [pdb, setPdb] = useState<string | null>(null);
  const [repr, setRepr] = useState<Repr>("cartoon");
  const [color, setColor] = useState<ColorTheme>("sequence-id");
  const [showMutations, setShowMutations] = useState(true);
  const [range, setRange] = useState("");
  const [pair, setPair] = useState("");
  const [rmsd, setRmsd] = useState<number | null>(null);

  const mutationResidues = useMemo(
    () =>
      (mutations ?? [])
        .map((m) => residueOf(m))
        .filter((n): n is number => n !== null && n > 0),
    [mutations],
  );

  const parseRange = useCallback((value: string): [number, number] | null => {
    const m = /^\s*(\d+)\s*(?:[-–:]\s*(\d+))?\s*$/.exec(value);
    if (!m) return null;
    const from = Number(m[1]);
    const to = m[2] ? Number(m[2]) : from;
    return from <= to ? [from, to] : [to, from];
  }, []);

  // Plugin lifecycle: create once, dispose on unmount.
  useEffect(() => {
    const gl = detectWebGL();
    if (!gl.ok) {
      setStatus("nogl");
      setDetail(gl.error ?? "WebGL unavailable");
      return;
    }
    let disposed = false;
    const container = containerRef.current;
    if (!container) return;
    void createViewer(container).then((plugin) => {
      if (disposed) {
        plugin.dispose();
        return;
      }
      pluginRef.current = plugin;
    });
    return () => {
      disposed = true;
      pluginRef.current?.dispose();
      pluginRef.current = null;
    };
  }, []);

  // Structure loading for the selected commit.
  useEffect(() => {
    if (!detectWebGL().ok) return;
    let cancelled = false;
    async function load() {
      if (!commitId && !pasted) {
        setStatus("empty");
        setPdb(null);
        return;
      }
      setStatus("loading");
      setRmsd(null);
      try {
        const text = pasted ? pasted.text : await api.text(`/commits/${commitId}/structure`);
        if (cancelled) return;
        if (!text.trim().includes("ATOM")) throw new Error("no coordinates in response");
        setPdb(text);
        // The plugin may still be initialising on the first pass; retry briefly.
        for (let i = 0; i < 40 && !pluginRef.current && !cancelled; i += 1) {
          await new Promise((r) => setTimeout(r, 50));
        }
        const plugin = pluginRef.current;
        if (!plugin || cancelled) return;
        await loadStructure(plugin, text, pasted?.name || label || commitId || "structure", repr, color);
        if (cancelled) return;
        setStatus("ready");
      } catch (err) {
        if (cancelled) return;
        setStatus("error");
        setDetail(err instanceof Error ? err.message : String(err));
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
    // repr/color are applied by their own effects; re-loading on them would reset the camera.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commitId, label, pasted]);

  useEffect(() => {
    const plugin = pluginRef.current;
    if (plugin && status === "ready") void setRepresentation(plugin, repr);
  }, [repr, status]);

  useEffect(() => {
    const plugin = pluginRef.current;
    if (plugin && status === "ready") void setColorTheme(plugin, color);
  }, [color, status]);

  // Mutation layer: paints the residues this version changed, straight from `mutations`.
  useEffect(() => {
    const plugin = pluginRef.current;
    if (!plugin || status !== "ready") return;
    const residues = marked && marked.length > 0 ? marked : mutationResidues;
    if (showMutations && residues.length > 0) {
      void paintResidues(
        plugin,
        residues.map((r) => [r, r] as [number, number]),
        marked && marked.length > 0 ? 0x7c3aed : 0xd92d20,
      );
    } else {
      void clearPaint(plugin);
    }
  }, [showMutations, mutationResidues, marked, status, color, repr]);

  const source = pdb ? structureSource(pdb) : null;
  const residues = pdb ? residueCount(pdb) : 0;

  const focusRange = useCallback(() => {
    const plugin = pluginRef.current;
    const parsed = parseRange(range);
    if (!plugin || !parsed) return;
    void selectResidues(plugin, parsed[0], parsed[1]);
    void focusResidues(plugin, parsed[0], parsed[1]);
  }, [parseRange, range]);

  const measurePair = useCallback(() => {
    const plugin = pluginRef.current;
    const m = /^\s*(\d+)\s*[,-]\s*(\d+)\s*$/.exec(pair);
    if (!plugin || !m) return;
    void measureDistance(plugin, Number(m[1]), Number(m[2]));
  }, [pair]);

  const compare = useCallback(async () => {
    const plugin = pluginRef.current;
    if (!plugin || !compareCommitId) return;
    try {
      const text = await api.text(`/commits/${compareCommitId}/structure`);
      await addStructure(plugin, text, `compare-${compareCommitId}`, repr);
      const value = await superposeLoaded(plugin);
      setRmsd(value);
      resetCamera(plugin);
    } catch (err) {
      setDetail(err instanceof Error ? err.message : String(err));
    }
  }, [compareCommitId, repr]);

  const shoot = useCallback(async () => {
    const plugin = pluginRef.current;
    if (!plugin) return;
    const uri = await screenshot(plugin);
    if (!uri) return;
    const a = document.createElement("a");
    a.href = uri;
    a.download = `${label || commitId || "structure"}-${source ?? "model"}.png`;
    a.click();
  }, [commitId, label, source]);

  const downloadPdb = useCallback(async () => {
    if (!pdb) return;
    const url = URL.createObjectURL(new Blob([pdb], { type: "chemical/x-pdb" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${label || commitId || "structure"}.pdb`;
    a.click();
    URL.revokeObjectURL(url);
  }, [commitId, label, pdb]);

  // Keyboard: F focus selection, Esc clear, R reset, 1/2/3 representation.
  const onKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLDivElement>) => {
      const plugin = pluginRef.current;
      if (!plugin) return;
      const target = event.target as HTMLElement;
      if (target.tagName === "INPUT" || target.tagName === "SELECT") return;
      const key = event.key.toLowerCase();
      if (key === "f") focusRange();
      else if (key === "escape") {
        clearSelection(plugin);
        clearMeasurements(plugin);
      } else if (key === "r") resetCamera(plugin);
      else if (key === "1") setRepr("cartoon");
      else if (key === "2") setRepr("backbone");
      else if (key === "3") setRepr("spacefill");
      else return;
      event.preventDefault();
    },
    [focusRange],
  );

  const mutated = new Set(mutationResidues);

  return (
    <div className="viewer" data-testid="structure-3d" onKeyDown={onKeyDown} tabIndex={-1}>
      <div className="pane-header">
        <div className="row">
          <strong style={{ fontSize: 12.5 }}>{pasted?.name || label || "no version selected"}</strong>
          {pasted && (
            <span
              className="badge sim tip"
              data-tip="pasted file rendered client-side — not stored, not scored, no provenance recorded"
              tabIndex={0}
            >
              pasted
            </span>
          )}
          {source && (
            <span className="badge sim tip" data-tip="deterministic coarse Cα model — not a prediction and not experimental" tabIndex={0}>
              {source}
            </span>
          )}
          {residues > 0 && (
            <span className="mono" style={{ color: "var(--faint)" }}>
              {residues} Cα
            </span>
          )}
          {rmsd !== null && (
            <span
              className="badge tip"
              data-tip="Cα-trace RMSD between two coarse models — measures the generator's response to the mutations, not a physical conformational change"
              tabIndex={0}
            >
              RMSD {rmsd.toFixed(2)} Å
            </span>
          )}
        </div>
        <div className="row">
          {REPRS.map((r, i) => (
            <button
              className="tab tip"
              type="button"
              key={r}
              aria-selected={repr === r}
              data-tip={`${REPR_LABELS[r]} · key ${i + 1}`}
              onClick={() => setRepr(r)}
            >
              {REPR_LABELS[r]}
            </button>
          ))}
          <label className="tip" data-tip="colour scheme — only schemes with real information for a Cα trace are offered">
            <span className="sr-only">colour by</span>
            <select
              className="mini-select"
              value={color}
              aria-label="colour by"
              onChange={(e) => setColor(e.target.value as ColorTheme)}
            >
              {COLORS.map((c) => (
                <option value={c} key={c}>
                  {COLOR_LABELS[c]}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="viewer-stage">
        <div
          ref={containerRef}
          className="structure-viewer"
          aria-label="3D molecular structure viewer"
          style={{ visibility: status === "ready" ? "visible" : "hidden" }}
        />
        {status !== "ready" && (
          <div className="viewer-empty" data-testid="viewer-state">
            {status === "nogl" && (
              <>
                <span>3D rendering is unavailable in this browser session.</span>
                <span className="mono" style={{ fontSize: 10 }}>
                  {detail} · the sequence, mutations and metrics below still apply
                </span>
              </>
            )}
            {status === "empty" && (
              <>
                <span>Select a version to load its coordinates.</span>
                <span className="mono" style={{ fontSize: 10 }}>
                  every commit carries a coarse Cα trace
                </span>
              </>
            )}
            {status === "loading" && <span>loading coordinates…</span>}
            {status === "error" && (
              <>
                <span>No coordinates for this version.</span>
                <span className="mono" style={{ fontSize: 10 }}>
                  {detail}
                </span>
              </>
            )}
          </div>
        )}
      </div>

      {sequence && (
        <div className="seq-track" data-testid="sequence-track">
          {sequence.split("").map((ch, i) => (
            <span
              className={mutated.has(i + 1) ? "res mut" : "res"}
              key={i}
              title={`${ch}${i + 1}`}
              onMouseEnter={() => {
                const plugin = pluginRef.current;
                if (plugin && status === "ready") void highlightResidues(plugin, i + 1, i + 1);
              }}
              onMouseLeave={() => {
                const plugin = pluginRef.current;
                if (plugin && status === "ready") clearHighlight(plugin);
              }}
              onClick={() => {
                const plugin = pluginRef.current;
                if (!plugin || status !== "ready") return;
                void selectResidues(plugin, i + 1, i + 1);
                void focusResidues(plugin, i + 1, i + 1);
                void labelResidue(plugin, i + 1);
              }}
            >
              {ch}
            </span>
          ))}
        </div>
      )}

      <div className="camera-bar">
        <div className="row">
          <input
            className="mini-input"
            value={range}
            placeholder="residue 12-24"
            aria-label="residue range"
            onChange={(e) => setRange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") focusRange();
            }}
          />
          <button className="tab tip" type="button" data-tip="select and orbit to the range · key F" onClick={focusRange}>
            focus
          </button>
          <input
            className="mini-input"
            value={pair}
            placeholder="dist 5,50"
            aria-label="distance between residues"
            onChange={(e) => setPair(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") measurePair();
            }}
          />
          <button
            className="tab tip"
            type="button"
            data-tip="Cα–Cα distance in the model geometry"
            onClick={measurePair}
          >
            measure
          </button>
          <button
            className="tab tip"
            type="button"
            aria-selected={showMutations}
            data-tip={
              mutationResidues.length > 0
                ? `paint the ${mutationResidues.length} residue(s) this version mutated`
                : "this version records no mutations"
            }
            disabled={mutationResidues.length === 0}
            onClick={() => setShowMutations((v) => !v)}
          >
            mutations
          </button>
        </div>
        <div className="row">
          {compareCommitId && (
            <button
              className="tab tip"
              type="button"
              data-tip="load the compared version and superpose it over paired Cα atoms"
              onClick={() => void compare()}
            >
              superpose
            </button>
          )}
          <button
            className="tab tip"
            type="button"
            data-tip="reset camera · key R"
            onClick={() => {
              const plugin = pluginRef.current;
              if (plugin) resetCamera(plugin);
            }}
          >
            reset
          </button>
          <button className="tab tip" type="button" data-tip="export the viewport as PNG" onClick={() => void shoot()}>
            snapshot
          </button>
          <button
            className="tab tip"
            type="button"
            data-tip="download the raw PDB with its provenance REMARK"
            disabled={!pdb}
            onClick={() => void downloadPdb()}
          >
            PDB
          </button>
          <span
            className="tip"
            data-tip={UNAVAILABLE.map(([name, why]) => `${name}: ${why}`).join(" · ")}
            tabIndex={0}
            style={{ color: "var(--faint)" }}
          >
            {UNAVAILABLE.length} capabilities unavailable
          </span>
        </div>
      </div>
      <p className="sr-only">{COLOR_TIPS[color]}</p>
    </div>
  );
}
