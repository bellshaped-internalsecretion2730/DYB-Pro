# Foldsmith — a pre-wetlab protein design OS

Foldsmith is a workspace for protein-design scientists. You type a research goal, drop in
sequences/structures, and Foldsmith runs an autonomous in-silico design cycle that ends in a
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

Foldsmith never pretends to be Devin. The execution provider of every agent run is recorded and
displayed:

* `devin` — real Devin sessions (requires `DEVIN_API_KEY`). Orchestrator + children, tags,
  playbooks, ACU limits, structured handoff, polling, cancellation.
* `local-simulation` — only when `ALLOW_LOCAL_SIMULATION=true` and no API key is present. The
  same toolkit runs in-process so the demo is still end-to-end, and every run/commit is labelled
  `local-simulation` in the API and the UI.

## Development

```bash
# backend: 50 tests, no network and no credentials needed
cd backend && pip install -r requirements.txt && pytest && ruff check .

# api only (SQLite + local artifact storage fallback)
uvicorn app.main:app --reload

# frontend
cd frontend && npm install && npm run typecheck && npm run build
```

The compose stack runs Postgres, Redis, MinIO, the API, a Celery worker and the Next.js web app.
Running the API alone falls back to SQLite, local artifact storage and inline (eager) cycle
execution, so nothing extra is required for development.

Licensed under the repository's LICENSE. No proprietary third-party code, UI, text or data is
used; all scoring methods are re-implemented from published, cited literature and are documented
in `backend/app/toolkit/CITATIONS.md`.
