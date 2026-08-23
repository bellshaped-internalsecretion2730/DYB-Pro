"use client";

import { useMemo, useState } from "react";
import { asFile, diffPositions, parseInput, type ParsedInput } from "@/lib/seqinput";
import type { Project } from "@/lib/api";

function summary(parsed: ParsedInput): string {
  if (parsed.kind === "sequence") return `${parsed.residues} aa · ${parsed.name}`;
  if (parsed.kind === "structure")
    return `${parsed.format.toUpperCase()} · ${parsed.atoms} atoms · ${parsed.name}`;
  return parsed.message;
}

/**
 * Top-right input for the target protein and the working protein.
 * Honest contract, matching what the backend actually offers:
 *  - pasted PDB/mmCIF renders in the viewer immediately (client-side, no provenance) and can be
 *    committed through the existing upload endpoint;
 *  - a pasted sequence is validated and compared against the selected version locally — nothing
 *    folds it on demand, because no endpoint does that;
 *  - a target sequence is attached to the selected project and becomes docking/folding context.
 */
export default function SequenceLoader({
  project,
  referenceSequence,
  referenceLabel,
  busy,
  onRenderStructure,
  onCommitFile,
  onSetTarget,
  onCompare,
  inline = false,
}: {
  project: Project | null;
  referenceSequence: string | null;
  referenceLabel: string | null;
  busy: boolean;
  onRenderStructure: (text: string, name: string) => void;
  onCommitFile: (file: File) => void;
  onSetTarget: (targetName: string, targetSequence: string) => void;
  onCompare: (positions: number[]) => void;
  inline?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [targetName, setTargetName] = useState("");
  const [target, setTarget] = useState("");
  const [working, setWorking] = useState("");

  const parsedTarget = useMemo(
    () => (target.trim() ? parseInput(target, targetName || "target") : null),
    [target, targetName],
  );
  const parsedWorking = useMemo(
    () => (working.trim() ? parseInput(working, "working") : null),
    [working],
  );

  const diff = useMemo(() => {
    if (!referenceSequence || parsedWorking?.kind !== "sequence") return null;
    return diffPositions(referenceSequence, parsedWorking.sequence);
  }, [referenceSequence, parsedWorking]);

  return (
    <div
      className={`seq-loader${inline ? " inline" : ""}${open ? " open" : ""}`}
      data-testid="sequence-loader"
    >
      <button
        className="tab tip"
        type="button"
        aria-expanded={open}
        data-tip="load a target protein and a working protein: pasted PDB/mmCIF renders here, sequences are validated and compared"
        onClick={() => setOpen((v) => !v)}
      >
        Protein + target
      </button>
      {open && (
        <div className={`seq-panel glass${inline ? " inline" : ""}`}>
          <label className="field">
            <span className="label">target protein</span>
            <input
              className="mini-input"
              value={targetName}
              placeholder="name, e.g. Human IgG1 CH3"
              aria-label="target name"
              onChange={(e) => setTargetName(e.target.value)}
            />
            <textarea
              value={target}
              aria-label="target sequence or structure"
              placeholder="paste target sequence, FASTA, PDB or mmCIF"
              onChange={(e) => setTarget(e.target.value)}
            />
          </label>
          {parsedTarget && (
            <div className="row">
              <span className={parsedTarget.kind === "error" ? "pill no" : "pill"}>
                {summary(parsedTarget)}
              </span>
              {parsedTarget.kind === "structure" && (
                <button
                  className="tab tip"
                  type="button"
                  data-tip="render this file in the viewer — client-side only, nothing is stored"
                  onClick={() => onRenderStructure(parsedTarget.text, parsedTarget.name)}
                >
                  render
                </button>
              )}
              {parsedTarget.kind === "sequence" && (
                <button
                  className="tab tip"
                  type="button"
                  disabled={busy || !project || !targetName.trim()}
                  data-tip="attaches this target to the selected project for structure prediction and docking"
                  onClick={() => onSetTarget(targetName.trim(), parsedTarget.sequence)}
                >
                  set project target
                </button>
              )}
            </div>
          )}
          <p className="hint tip" data-tip="the target is stored on this project; AlphaFold can add target coordinates when the cycle starts">
            target sequence feeds AlphaFold and docking when enabled
          </p>

          <label className="field">
            <span className="label">working protein</span>
            <textarea
              value={working}
              aria-label="working sequence or structure"
              placeholder="paste working sequence, FASTA, PDB or mmCIF"
              onChange={(e) => setWorking(e.target.value)}
            />
          </label>
          {parsedWorking && (
            <div className="row">
              <span className={parsedWorking.kind === "error" ? "pill no" : "pill"}>
                {summary(parsedWorking)}
              </span>
              {parsedWorking.kind === "structure" && (
                <>
                  <button
                    className="tab tip"
                    type="button"
                    data-tip="render this file in the viewer — client-side only, nothing is stored"
                    onClick={() => onRenderStructure(parsedWorking.text, parsedWorking.name)}
                  >
                    render
                  </button>
                  <button
                    className="tab tip"
                    type="button"
                    disabled={busy || !project}
                    data-tip="uploads the file to the project, creating a root commit with these coordinates"
                    onClick={() => {
                      const file = asFile(parsedWorking);
                      if (file) onCommitFile(file);
                    }}
                  >
                    commit to project
                  </button>
                </>
              )}
              {parsedWorking.kind === "sequence" && (
                <>
                  <button
                    className="tab tip"
                    type="button"
                    disabled={!diff || diff.length === 0}
                    data-tip={
                      diff
                        ? `${diff.length} position(s) differ from ${referenceLabel ?? "the selected version"} — position-by-position, no alignment`
                        : "select a version with a known sequence to compare"
                    }
                    onClick={() => onCompare(diff ?? [])}
                  >
                    {diff ? `mark ${diff.length} diffs` : "compare"}
                  </button>
                  <button
                    className="tab tip"
                    type="button"
                    disabled={busy || !project}
                    data-tip="uploads as FASTA, creating a root commit; a design cycle has to run before it has coordinates"
                    onClick={() => {
                      const file = asFile(parsedWorking);
                      if (file) onCommitFile(file);
                    }}
                  >
                    commit as FASTA
                  </button>
                </>
              )}
            </div>
          )}
          <p className="hint tip" data-tip="no endpoint folds an arbitrary sequence: coordinates exist only for commits a design cycle evaluated, or for an uploaded PDB/mmCIF file">
            pasted sequences get no coordinates until a cycle runs
          </p>
        </div>
      )}
    </div>
  );
}
