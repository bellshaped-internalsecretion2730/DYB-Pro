"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import ProviderBadge from "@/components/ProviderBadge";
import {
  DriftPanel,
  EconomicsPanel,
  ExperimentTable,
  GatePanel,
  MoleculeTable,
  ResearchLog,
  StageLadder,
} from "@/components/ProgramBoard";
import {
  api,
  apiKey,
  setApiKey,
  type DrugProgram,
  type ProgramDetail,
  type ProgramExperiment,
  type ProgramMolecule,
  type Provider,
  type ProgramResearchEvent,
  type RoundSummary,
} from "@/lib/api";

const AUTONOMY = [0, 1, 2, 3, 4];

export default function PharmakonBoard() {
  const [provider, setProvider] = useState<Provider | null>(null);
  const [programs, setPrograms] = useState<DrugProgram[]>([]);
  const [programId, setProgramId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ProgramDetail | null>(null);
  const [molecules, setMolecules] = useState<ProgramMolecule[]>([]);
  const [experiments, setExperiments] = useState<ProgramExperiment[]>([]);
  const [research, setResearch] = useState<ProgramResearchEvent[]>([]);
  const [rounds, setRounds] = useState<RoundSummary[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [claim, setClaim] = useState("");
  const [citation, setCitation] = useState("");
  const [metric, setMetric] = useState("");
  const [metricValue, setMetricValue] = useState("");

  const fail = (e: unknown) => setError(e instanceof Error ? e.message : String(e));

  const loadPrograms = useCallback(async () => {
    const rows = await api.get<DrugProgram[]>("/pharma/programs");
    setPrograms(rows);
    return rows;
  }, []);

  const refresh = useCallback(async (id: string) => {
    const [d, m, x, r, rounds] = await Promise.all([
      api.get<ProgramDetail>(`/pharma/programs/${id}`),
      api.get<ProgramMolecule[]>(`/pharma/programs/${id}/molecules`),
      api.get<ProgramExperiment[]>(`/pharma/programs/${id}/experiments`),
      api.get<ProgramResearchEvent[]>(`/pharma/programs/${id}/research?limit=40`),
      api.get<RoundSummary[]>(`/pharma/programs/${id}/rounds`),
    ]);
    setDetail(d);
    setMolecules(m);
    setExperiments(x);
    setResearch(r);
    setRounds(rounds);
  }, []);

  useEffect(() => {
    setKeyInput(apiKey());
    api.get<Provider>("/providers").then(setProvider).catch(fail);
    loadPrograms()
      .then((rows) => {
        if (rows[0]) {
          setProgramId(rows[0].id);
          return refresh(rows[0].id);
        }
      })
      .catch(fail);
  }, [loadPrograms, refresh]);

  async function act(label: string, fn: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    try {
      await fn();
      if (programId) await refresh(programId);
    } catch (e) {
      fail(e);
    } finally {
      setBusy(null);
    }
  }

  async function seedDemo() {
    await act("seed", async () => {
      await api.post("/demo/seed");
      const rows = await loadPrograms();
      if (rows[0]) {
        setProgramId(rows[0].id);
        // `act` refreshes the programId from its own closure, which is still null on a first seed.
        await refresh(rows[0].id);
      }
    });
  }

  async function ingestAssay(molecule: ProgramMolecule) {
    const metricName = window.prompt(
      "assay metric (pkd, half_life_h, selectivity_fold, noael_mg_kg …)",
      "pkd",
    );
    if (!metricName) return;
    const raw = window.prompt(`measured ${metricName} for ${molecule.short_id}`, "");
    if (raw === null) return;
    const value = Number(raw);
    if (Number.isNaN(value)) {
      setError(`"${raw}" is not a number`);
      return;
    }
    const source = window.prompt("source (lab, CRO report, publication)", "") || "";
    await act("assay", () =>
      api.post(`/pharma/programs/${programId}/assays`, {
        molecule_hash: molecule.id,
        metric: metricName,
        value,
        source,
      }),
    );
  }

  const program = detail?.program;
  const pendingGate =
    detail?.latest_gate && detail.latest_gate.requires_approval &&
    detail.latest_gate.approval_status === "pending"
      ? detail.latest_gate
      : null;

  return (
    <>
      <header className="top">
        <div>
          <div className="brand">
            PHARMA<span>KON</span>
          </div>
          <div className="tagline">autonomous drug-discovery program board</div>
        </div>
        <ProviderBadge provider={provider} />
        <div className="grow" />
        <Link href="/">← DYB Pro protein workspace</Link>
        <input
          type="text"
          style={{ width: 220 }}
          value={keyInput}
          onChange={(e) => setKeyInput(e.target.value)}
          aria-label="API key"
        />
        <button
          className="secondary"
          type="button"
          onClick={() => {
            setApiKey(keyInput);
            window.location.reload();
          }}
        >
          use key
        </button>
      </header>

      <main>
        {error && (
          <div className="panel wide">
            <p className="err" style={{ margin: 0 }}>
              {error}
            </p>
          </div>
        )}

        <section className="panel wide">
          <h2>Program</h2>
          <div className="row" style={{ marginBottom: 8 }}>
            <select
              value={programId ?? ""}
              onChange={(e) => {
                setProgramId(e.target.value);
                refresh(e.target.value).catch(fail);
              }}
              style={{ maxWidth: 360 }}
            >
              <option value="">— select program —</option>
              {programs.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.stage_name}
                </option>
              ))}
            </select>
            <button className="secondary" type="button" onClick={seedDemo} disabled={busy === "seed"}>
              load demo program
            </button>
            <button
              type="button"
              disabled={!programId || busy !== null || pendingGate !== null}
              onClick={() => act("advance", () => api.post(`/pharma/programs/${programId}/advance`))}
            >
              {busy === "advance" ? "running round…" : "run next round"}
            </button>
            {pendingGate && <span className="badge bad">a human must sign the open gate first</span>}
          </div>
          {program && detail && (
            <>
              <p style={{ marginTop: 0 }}>
                <b>{program.target_name}</b> · {program.indication || "indication not set"} ·{" "}
                {program.objective}
              </p>
              <StageLadder detail={detail} />
              <div className="row" style={{ marginTop: 8 }}>
                <span className="pill">{program.molecule_count} molecules</span>
                <span className="pill">{program.rounds_run} rounds</span>
                <span className="pill">{program.assays_ingested} assay results</span>
                <span className="pill">{program.acus_used} ACUs</span>
                <span className="pill">{program.status}</span>
                <span className="pill">{detail.daemon.mode} daemon</span>
              </div>
              <div className="row" style={{ marginTop: 8 }}>
                <label htmlFor="autonomy">autonomy</label>
                <select
                  id="autonomy"
                  value={program.autonomy_level}
                  onChange={(e) =>
                    act("autonomy", () =>
                      api.post(`/pharma/programs/${programId}/autonomy`, {
                        autonomy_level: Number(e.target.value),
                      }),
                    )
                  }
                  style={{ maxWidth: 480 }}
                >
                  {AUTONOMY.map((level) => (
                    <option key={level} value={level}>
                      L{level}
                    </option>
                  ))}
                </select>
                <span className="muted">{program.autonomy_label}</span>
              </div>
              <p className="hint">
                next: {detail.next_action.action} — {detail.next_action.reason}
              </p>
              {detail.daemon.note && <p className="hint">{detail.daemon.note}</p>}
            </>
          )}
        </section>

        <section className="panel wide">
          <h2>Stage gate</h2>
          <p className="hint">
            Deterministic Python decides; the agents only supply numbers and citations. Safety,
            regulatory and human-facing gates always need a signature.
          </p>
          <GatePanel
            gate={detail?.latest_gate ?? null}
            busy={busy !== null}
            onDecide={(approve) =>
              act("gate", () =>
                api.post(`/pharma/gates/${detail?.latest_gate?.id}/approval`, {
                  approve,
                  note: approve ? "approved from the program board" : "rejected from the program board",
                }),
              )
            }
          />
        </section>

        <section className="panel wide">
          <h2>Molecule portfolio</h2>
          <p className="hint">
            Every molecule is an immutable, content-addressed commit. pKd is a sequence-derived
            complementarity proxy — not docking, not a free-energy calculation.
          </p>
          <MoleculeTable
            molecules={molecules}
            candidateId={program?.candidate_molecule_id}
            onIngest={ingestAssay}
          />
          {programId && (
            <p className="hint">
              <a href={api.downloadUrl(`/pharma/programs/${programId}/export/molecules`)}>
                export molecules as CSV
              </a>
              {" · "}
              <a href={api.downloadUrl(`/pharma/programs/${programId}/dossier`)}>draft dossier JSON</a>
            </p>
          )}
        </section>

        <section className="panel">
          <h2>Experiments that would change the decision</h2>
          <ExperimentTable experiments={experiments} />
          {rounds[0]?.experiment_plan?.evidence_tasks?.length ? (
            <ul className="steps">
              {rounds[0].experiment_plan.evidence_tasks.map((t) => (
                <li key={t.metric}>
                  <b>{t.label}</b> needs {t.requirement} — {t.settled_by}
                </li>
              ))}
            </ul>
          ) : null}
        </section>

        <section className="panel">
          <h2>Prediction vs measurement</h2>
          {detail ? <DriftPanel detail={detail} /> : <p className="muted">no program selected</p>}
        </section>

        <section className="panel">
          <h2>Program economics</h2>
          {detail ? <EconomicsPanel detail={detail} /> : <p className="muted">no program selected</p>}
        </section>

        <section className="panel">
          <h2>Evidence in</h2>
          <p className="hint">
            A number that moves a gate needs a citation; without one the API rejects it.
          </p>
          <div className="row" style={{ marginBottom: 6 }}>
            <input
              type="text"
              placeholder="claim"
              value={claim}
              onChange={(e) => setClaim(e.target.value)}
              style={{ flex: 1 }}
            />
          </div>
          <div className="row" style={{ marginBottom: 6 }}>
            <input
              type="text"
              placeholder="citation (DOI, patent, trial id)"
              value={citation}
              onChange={(e) => setCitation(e.target.value)}
              style={{ flex: 1 }}
            />
          </div>
          <div className="row">
            <select value={metric} onChange={(e) => setMetric(e.target.value)}>
              <option value="">no gate metric</option>
              <option value="target_evidence_score">target_evidence_score</option>
              <option value="druggability_score">druggability_score</option>
              <option value="freedom_to_operate">freedom_to_operate</option>
              <option value="citation_count">citation_count</option>
              <option value="scale_up_feasibility">scale_up_feasibility</option>
            </select>
            <input
              type="text"
              placeholder="value"
              value={metricValue}
              onChange={(e) => setMetricValue(e.target.value)}
              style={{ width: 100 }}
            />
            <button
              type="button"
              disabled={!programId || !claim || busy !== null}
              onClick={() =>
                act("research", async () => {
                  await api.post(`/pharma/programs/${programId}/research`, {
                    claim,
                    citation,
                    metric,
                    value: metric ? Number(metricValue) : null,
                  });
                  setClaim("");
                  setCitation("");
                  setMetric("");
                  setMetricValue("");
                })
              }
            >
              record evidence
            </button>
          </div>
        </section>

        <section className="panel wide">
          <h2>Research log</h2>
          <ResearchLog events={research} />
        </section>
      </main>
    </>
  );
}
