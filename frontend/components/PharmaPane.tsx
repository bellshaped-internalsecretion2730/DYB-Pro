"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type BindingInput, type DrugProgram, type Project } from "@/lib/api";

export default function PharmaPane({ project }: { project: Project | null }) {
  const [programs, setPrograms] = useState<DrugProgram[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [inputs, setInputs] = useState<BindingInput[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [objective, setObjective] = useState("");
  const targetRef = useRef<HTMLInputElement>(null);
  const ligandRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async (projectId: string) => {
    const [programRows, inputRows] = await Promise.all([
      api.get<DrugProgram[]>(`/projects/${projectId}/programs`),
      api.get<BindingInput[]>(`/projects/${projectId}/binding-inputs`),
    ]);
    setPrograms(programRows);
    setInputs(inputRows);
    setSelectedId((current) => current || programRows[0]?.id || "");
  }, []);

  useEffect(() => {
    setSelectedId("");
    setPrograms([]);
    setInputs([]);
    setError(null);
    let active = true;
    if (project) {
      refresh(project.id).catch((reason) => {
        if (active) setError(String(reason));
      });
    }
    return () => {
      active = false;
    };
  }, [project?.id, refresh]);

  async function upload(role: "target" | "ligand", files: FileList | null) {
    const file = files?.[0];
    if (!project || !file) return;
    setBusy(role);
    setError(null);
    try {
      await api.upload(`/projects/${project.id}/binding-inputs/${role}`, file);
      await refresh(project.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
      const ref = role === "target" ? targetRef : ligandRef;
      if (ref.current) ref.current.value = "";
    }
  }

  async function createProgram() {
    if (!project || !name.trim()) return;
    setBusy("create");
    setError(null);
    try {
      const created = await api.post<DrugProgram>(`/projects/${project.id}/programs`, {
        name: name.trim(),
        target_name: target.trim() || project.target_name || "",
        objective: objective.trim() || project.goal,
        target_sequence: "",
        seed_smiles: [],
        autonomy_level: 2,
      });
      setName("");
      setTarget("");
      setObjective("");
      await refresh(project.id);
      setSelectedId(created.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function advance() {
    if (!selectedId || !project) return;
    setBusy("advance");
    setError(null);
    try {
      await api.post(`/pharma/programs/${selectedId}/advance`);
      await refresh(project.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  const selected = programs.find((program) => program.id === selectedId) ?? null;
  const inputFor = (role: "target" | "ligand") => inputs.find((item) => item.role === role);

  return (
    <div className="pharma-pane" data-testid="pharma-pane">
      <div className="section compact">
        <select aria-label="drug program" value={selectedId} onChange={(event) => setSelectedId(event.target.value)}>
          <option value="">No program</option>
          {programs.map((program) => <option value={program.id} key={program.id}>{program.name}</option>)}
        </select>
        <div className="row">
          <button type="button" disabled={!selected || busy !== null} onClick={() => void advance()}>
            {busy === "advance" ? "Running…" : "Run round"}
          </button>
          {selected ? <span className="pill">{selected.stage_name}</span> : null}
        </div>
      </div>

      <div className="section compact binding-inputs">
        {(["target", "ligand"] as const).map((role) => {
          const input = inputFor(role);
          return (
            <label
              className="binding-upload tip"
              data-tip={role === "target" ? "Protein coordinates used as the program target. PDB/ENT only." : "Ligand coordinates stored separately from the protein. HETATM-only PDB files are supported."}
              key={role}
            >
              <input
                ref={role === "target" ? targetRef : ligandRef}
                type="file"
                accept=".pdb,.ent"
                aria-label={`${role} PDB`}
                disabled={!project || busy !== null}
                onChange={(event) => void upload(role, event.target.files)}
              />
              <strong>{role === "target" ? "Target PDB" : "Ligand PDB"}</strong>
              <span>{busy === role ? "Uploading…" : input?.filename ?? "Add"}</span>
            </label>
          );
        })}
      </div>

      <details className="compact-disclosure">
        <summary>New program</summary>
        <div className="section compact">
          <input aria-label="program name" placeholder="Name" value={name} onChange={(event) => setName(event.target.value)} />
          <input aria-label="program target" placeholder="Target" value={target} onChange={(event) => setTarget(event.target.value)} />
          <textarea aria-label="program objective" placeholder="Objective" value={objective} onChange={(event) => setObjective(event.target.value)} />
          <button type="button" disabled={!project || !name.trim() || busy !== null} onClick={() => void createProgram()}>
            Create
          </button>
        </div>
      </details>

      {selected ? (
        <div className="section compact program-stats">
          <span><b>{selected.molecule_count}</b> molecules</span>
          <span><b>{selected.rounds_run}</b> rounds</span>
          <span><b>{selected.assays_ingested}</b> assays</span>
          <span><b>L{selected.autonomy_level}</b> autonomy</span>
        </div>
      ) : null}
      {error ? <p className="err section compact">{error}</p> : null}
    </div>
  );
}
