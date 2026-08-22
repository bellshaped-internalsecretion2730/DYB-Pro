# DYB Pro — a pre-wetlab protein design OS

DYB Pro is a workspace for protein-design scientists. You type a research goal, drop in
sequences/structures, and DYB Pro runs an autonomous in-silico design cycle that ends in a
**ranked, ready-to-order wet-lab shortlist** plus a **git-like version history** of every design
it ever proposed.

Devin is the research engine: one **orchestrator** session plans the cycle and fans out to
specialized **child agents** (sequence, structure, docking/MD, literature, ranking) through the
real Devin API. Every observation those agents make is committed to a protein version graph, and
the next cycle reads that graph before proposing anything.

```
NL brief + FASTA/PDB  ->  orchestrator plans  ->  child agents run toolkit
        ^                                                    |
        |                                                    v
  next cycle reads history  <-  immutable commits in version DAG  ->  wet-lab pack
```

## 90-second demo

```bash
cp .env.example .env          # add DEVIN_API_KEY / OPENAI_API_KEY if you have them
docker compose up --build     # one command
open http://localhost:3000
```

Three clicks: **Load demo project** → **Run design cycle** → **Export wet-lab shortlist**.

See [DEMO.md](DEMO.md) for the narrated script, [REQUIREMENTS.md](REQUIREMENTS.md) for scope,
[ARCHITECTURE.md](ARCHITECTURE.md) for the system design and
[DEVIN_INTEGRATION.md](DEVIN_INTEGRATION.md) for exactly how the Devin API is used.

## What's in the box

| Surface | Where |
| --- | --- |
| Workspace: brief + uploads, agent swarm, version DAG, observation log, shortlist | `frontend/` (Next.js) |
| REST API + OpenAPI (`/docs`) | `backend/app/api` |
| Protein version control (commits, diffs, branch/merge, lineage) | `backend/app/versioning` |
| In-silico toolkit (open source only, cited methods) | `backend/app/toolkit` |
| Real Devin API client + orchestrator/child fan-out | `backend/app/devin` |
| Ranking, wet-lab pack, history digest, OpenAI analysis | `backend/app/services` |
| Celery experiment queue with logs + retries | `backend/app/worker.py` |
| Seeded demo project (GB1 + IgG1 CH3) | `backend/app/seed.py` |
| Backend tests | `backend/tests` |

## Modes

DYB Pro never pretends to be Devin. The execution provider of every agent run is recorded and
displayed:

* `devin` — real Devin sessions (requires `DEVIN_API_KEY`). Orchestrator + children, tags,
  playbooks, ACU limits, structured handoff, polling, cancellation.
* `local-simulation` — only when `ALLOW_LOCAL_SIMULATION=true` and no API key is present. The
  same toolkit runs in-process so the demo is still end-to-end, and every run/commit is labelled
  `local-simulation` in the API and the UI.

## Development

```bash
# backend: no network and no credentials needed
cd backend && pip install -r requirements.txt && pytest && ruff check .

# api only (SQLite + local artifact storage fallback)
uvicorn app.main:app --reload

# frontend
cd frontend && npm install && npm run typecheck && npm run build
```

The compose stack runs Postgres, Redis, MinIO, the API, a Celery worker and the Next.js web app.
Running the API alone falls back to SQLite, local artifact storage and inline (eager) cycle
execution, so nothing extra is required for development.

## What Foldsmith does not know

Every in-silico number in Foldsmith is an **uncalibrated proxy**, reported in arbitrary units, and
the product refuses to dress them up:

* Folded structures are **coarse CA-only models** (`model:` provenance), not experimental
  structures. Burial, contacts, compactness and docking read off that model, and each design
  carries a `geometry_usable` flag when its own compactness/clash check fails.
* The stability score is a directional, antisymmetric **risk proxy** — not kcal/mol, not a Tm
  shift; epistasis between sites is not modelled.
* Docking scores order candidates against one fixed receptor. They are not affinities and cannot
  be converted to a KD. `minimize_geometry` is steepest descent on a soft potential, not MD.
* The wet-lab pack says what to build and what to **measure**. It predicts no assay outcome and
  reports no probability that a design validates.
* Cost figures are indicative list prices for consumables/services, excluding labour and
  overheads. Avoided spend is the cost of builds you did not order — not a validated saving.
* Hit rates and proxy/measurement agreement (Kendall tau) appear only after you ingest measured
  results (`POST /api/projects/{id}/results` or a results CSV), and stay per-project and
  small-sample. Measurements never overwrite a commit's scores; drift stays inspectable at
  `GET /api/projects/{id}/calibration`.

Not yet built: the always-live research daemon (cached-literature reuse, debounced research events
per diff) described in `REQUIREMENTS.md`. Research currently runs inside a design cycle only.

Licensed under the repository's LICENSE. No proprietary third-party code, UI, text or data is
used; all scoring methods are re-implemented from published, cited literature and are documented
in `backend/app/toolkit/CITATIONS.md`.
