# Architecture

```
                       ┌────────────────────────── web (Next.js) ──────────────────────────┐
                       │  chat workspace · agent swarm · version DAG · viewers · shortlist  │
                       └───────────────────────────────┬───────────────────────────────────┘
                                                       │ REST (X-API-Key, RBAC)
┌──────────────────────────────────────────────────────▼────────────────────────────────────┐
│ backend/app/api  (FastAPI, OpenAPI at /docs)                                              │
│   projects · uploads · cycles · commits/graph/diff/branch/merge · shortlist · export       │
├───────────────────────────────────────────────────────────────────────────────────────────┤
│ services                                                                                  │
│  versioning/  content-addressed commits, lineage, diff, branch & merge                     │
│  toolkit/     sequence · structure · developability · docking · folding backend            │
│  ranking/     multi-objective score, uncertainty, Pareto, "why this not that"              │
│  wetlab/      primers, constructs, assays, cost/risk, citations                            │
│  learning/    history digest fed back into every prompt                                    │
│  devin/       real Devin API client, playbooks, orchestrator fan-out, structured handoff    │
│  openai/      structured + multimodal analysis (optional, degrades to heuristics)           │
├───────────────────────────────────────────────────────────────────────────────────────────┤
│ worker (Celery)  cycle pipeline · session polling · retries · cancellation                  │
└───────┬───────────────────────┬───────────────────────┬───────────────────────┬───────────┘
        │ Postgres              │ Redis                 │ MinIO/S3              │ api.devin.ai
        │ (metadata + DAG)      │ (broker + result)     │ (artifacts)           │ (agents)
```

## Components

| Component | Tech | Responsibility |
| --- | --- | --- |
| `api` | FastAPI + SQLAlchemy 2 | HTTP surface, auth/RBAC, quotas, OpenAPI |
| `worker` | Celery + Redis | long-running design cycles, polling Devin, retries |
| `db` | Postgres 16 | projects, commits (DAG), branches, agent runs, observations, artifacts, users, usage |
| `objectstore` | MinIO (S3 API) | uploaded FASTA/PDB/CIF/CSV, generated structures, plots |
| `web` | Next.js 14 (app router) | scientist workspace |

SQLite is used automatically when `DATABASE_URL` points at sqlite (tests, `make dev`), Postgres in
Compose. Celery runs eager in-process when `CELERY_TASK_ALWAYS_EAGER=true` so the API alone is a
complete demo.

## Data model

```
User(id, email, role[admin|scientist|viewer], api_key_hash, acu_quota, cycle_quota)
Project(id, name, goal, owner_id, created_at)
Artifact(id, project_id, kind, key, filename, sha256, size, content_type)
Branch(project_id, name, head_commit_id)                        -- "main" created with project
ProteinCommit(id=sha256, project_id, branch, parent_ids[], message, sequence, structure_key,
              scores{}, uncertainty{}, filters{}, rationale, mutations[], agent_role,
              provider, devin_session_id, devin_session_url, prompt, citations[], cycle, created_at)
DesignCycle(id, project_id, brief, status, round, plan{}, orchestrator_session_id, provider,
            acu_limit, error, created_at, finished_at)
AgentRun(id, cycle_id, role, task, provider, devin_session_id, devin_session_url, playbook_id,
         tags[], status, attempts, max_attempts, acus, structured_output{}, log[], timings)
Observation(id, project_id, cycle_id, agent_run_id, kind, payload{}, created_at)
UsageRecord(id, user_id, kind, amount, created_at)
```

`ProteinCommit.id` is `sha256` over a canonical JSON of the commit's semantic content
(sequence, structure hash, parents, scores, agent, prompt, citations, timestamp), so a commit is
immutable and de-duplicated by construction. Commits are never updated or deleted; branch heads
move, commits do not.

## The design cycle (worker pipeline)

```
run_cycle(cycle_id)
 1. digest   = learning.history_digest(project)          # prior cycles, failures, exclusions
 2. plan     = devin.orchestrator.plan(brief, digest)    # orchestrator session, structured output
 3. runs     = devin.orchestrator.fan_out(plan)          # child sessions: sequence/structure/...
 4. poll     = devin.orchestrator.await_runs(runs)       # status + structured_output + ACUs
 5. props    = handoff.parse(runs)                       # validated candidate proposals
 6. scored   = toolkit.evaluate(props)                   # deterministic in-silico scoring
 7. ranked   = ranking.rank(scored, digest)              # score, uncertainty, why-this-not-that
 8. commits  = versioning.commit_many(ranked)            # immutable commits on cycle branch
 9. pack     = wetlab.build_pack(commits)                # shortlist, primers, assays, cost/risk
10. observations persisted at every step (audit + next-cycle learning)
```

Each step is idempotent per cycle: re-running a failed cycle resumes from persisted observations.
Child failures are retried up to `AGENT_MAX_ATTEMPTS`; a role that keeps failing degrades the
cycle to "partial" instead of failing it, and the reason is recorded.

## Scoring pipeline (deterministic, open source)

1. **Sequence layer** (`toolkit/sequence.py`): composition, MW, pI, GRAVY, instability index,
   pairwise alignment/homology, mutation application, codon optimization.
2. **Structure layer** (`toolkit/structure.py`): PDB/mmCIF parsing, Rg, contact map, SASA proxy,
   secondary-structure fractions; `toolkit/folding.py` provides a `FoldingBackend` interface with
   a coarse geometric fallback so a design without a structure is still evaluable.
3. **Developability** (`toolkit/developability.py`): ΔΔG proxy from substitution matrices and
   burial, solubility, aggregation windows, MHC-II-like immunogenicity, liability motifs; hard
   filters are separate from soft scores.
4. **Interaction** (`toolkit/docking.py`): rigid-body coarse docking with a perturbation ensemble
   → binding score + spread (uncertainty).
5. **Ranking** (`services/ranking.py`): objective normalization, weighted aggregate, Pareto
   front, uncertainty-aware ordering, explanations.

All methods and their literature sources are listed in `backend/app/toolkit/CITATIONS.md`.
Nothing here reads or reproduces proprietary software: the heuristics are independent
implementations of published methods.

## Security & governance

* API keys hashed (sha256) at rest; `X-API-Key` header; roles enforced per route dependency.
* Quotas: ACU budget and cycles/day per user, enforced before a cycle is queued and after Devin
  reports ACU usage.
* Provider secrets read from environment only (`app/config.py`), never echoed by the API.
* Every mutation of the graph carries the acting user, agent role, provider, and session URL.

## Failure modes

| Failure | Behaviour |
| --- | --- |
| No `DEVIN_API_KEY` | cycles are rejected unless `ALLOW_LOCAL_SIMULATION=true`, in which case runs are labelled `local-simulation` |
| Devin session blocked/expired | retried (bounded), then role marked failed, cycle continues partial |
| No `OPENAI_API_KEY` | narration + analysis fall back to deterministic summaries (`provider: heuristic`) |
| MinIO down | artifact writes fall back to local volume path, flagged in the artifact record |
| Worker down | API exposes queue depth; `CELERY_TASK_ALWAYS_EAGER` runs cycles inline |
