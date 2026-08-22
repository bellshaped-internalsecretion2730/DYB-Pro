# Foldsmith demo — 90 seconds

## Start (one command)

```bash
cp .env.example .env      # optionally paste DEVIN_API_KEY + DEVIN_ORG_ID
docker compose up --build
open http://localhost:3000
```

Everything comes up seeded: a demo project (GB1 as the design target, human IgG1 CH3 as the binding
partner), root commits for the sequence and structure, and demo API keys.

The header badge tells you which engine is live:

* **`real Devin agents · v3`** — `DEVIN_API_KEY` + `DEVIN_ORG_ID` are set; cycles create real Devin
  sessions and every card links to them.
* **`local-simulation (no Devin key)`** — no credentials; the same deterministic toolkit runs
  in-process and *everything* produced is labelled `local-simulation`. Never presented as Devin work.

## The three clicks

| # | Click | What a stranger sees |
| - | ----- | -------------------- |
| 1 | **load demo project** | Brief pre-filled: *improve GB1 thermal stability and solubility without losing predicted Fc binding.* Version DAG already holds the parent commit. Drag in your own FASTA/PDB/CIF/CSV instead if you prefer. |
| 2 | **run design cycle** | Orchestrator session appears, plans the round, then fans out to sequence / structure / docking-MD / literature / ranking children. Each card shows status, ACUs against its limit, live log, and a link to its session. The observation log fills as evidence lands. |
| 3 | **export CSV / JSON / FASTA** | The wet-lab shortlist: ranked designs with composite score, confidence, Pareto flag, mutations, "why this" and "why not next", plus vector + codon-optimized ORF, QuikChange primers with Tm, predicted assay read-outs, per-design cost, and the money avoided versus testing the whole pool. |

## The 90-second narration

1. **0:00 — the problem.** Wet-lab validation is the expensive step. Every design you *don't* need to
   order is money and weeks saved. (Point at the economics badges: shortlist cost vs
   test-everything cost, and the savings percentage.)
2. **0:15 — the brief.** Type the goal in English, drop a FASTA/PDB. No pipeline configuration.
3. **0:30 — Devin as supervisor.** One orchestrator plans the round and spawns specialist children
   with role playbooks, tags and ACU ceilings; each returns a schema-validated handoff. Click through
   to a session — the provenance is real, not a progress bar.
4. **0:50 — the version graph.** Each design is an immutable commit: sequence, structure, scores,
   parent, agent, prompt, citations. Click a node: mutations, filters, "why". Branch it, diff it,
   merge it — conflicts are reported, never silently resolved.
5. **1:05 — the shortlist.** Ranked with uncertainty, hard developability filters kept separate from
   soft scores, and an explicit "why this, not that" for every rejection. Constructs, primers and
   assays are ready to order.
6. **1:20 — the loop closes.** Hit **run design cycle** again. Round 2 reads the graph, the mutation
   ledger and the exclusions from round 1, so it never re-proposes what already failed. That is the
   compounding asset.

## Second cycle (the part people remember)

Run a second cycle without changing anything. Then check:

* `GET /api/projects/{id}/memory` → the digest the next orchestrator actually reads: completed rounds,
  best and failed designs, mutation ledger, exclusions with reasons, open questions.
* The DAG gains a second generation branching off round 1's winner.
* Round 2's rationale references round 1's failures.

## Useful endpoints during the demo

```bash
KEY=foldsmith-demo-scientist
curl -s localhost:8000/api/providers            | jq          # engine + reachability
curl -s -H "X-API-Key: $KEY" localhost:8000/api/projects | jq
curl -s -H "X-API-Key: $KEY" "localhost:8000/api/projects/$PID/graph" | jq '.nodes|length'
curl -s -H "X-API-Key: $KEY" "localhost:8000/api/cycles/$CID/shortlist" | jq '.pack.economics'
curl -s -H "X-API-Key: $KEY" "localhost:8000/api/cycles/$CID/export?fmt=csv"
open http://localhost:8000/docs                              # OpenAPI
```

Demo keys (rotate for anything real): `foldsmith-demo-admin`, `foldsmith-demo-scientist`,
`foldsmith-demo-viewer` — RBAC is enforced, so the viewer key can read the graph but cannot start a
cycle.

## If something is off

* Badge shows *Devin unreachable* → key or `DEVIN_ORG_ID` wrong for the chosen `DEVIN_API_FLAVOR`;
  `GET /api/providers` returns the API error verbatim.
* `run design cycle` refuses with *upload a sequence or structure first* → the selected project has no
  root commit; click **load demo project** or upload a FASTA.
* Cycle status `partial` → designs were produced but none passed the hard developability filters; the
  shortlist panel shows exactly which filter rejected what.
