# Requirements

## Problem

Wet-lab protein engineering is expensive: each construct costs real money and weeks of bench
time. Scientists therefore need to arrive at the bench with a *small*, *justified*, *traceable*
shortlist. Today that pre-wetlab work is scattered across notebooks, ad-hoc scripts, and lost
Slack threads: there is no version history of designs, no record of *why* a variant was dropped,
and no reuse of what previous rounds learned.

## Product goal

A scientist types a research goal, drops sequences/structures, and gets:

1. a continuously versioned design history (git-like, immutable, fully attributed), and
2. a ranked, ready-to-order wet-lab shortlist with cost/risk vs. testing everything.

Devin is the autonomous research engine that produces both.

## Functional requirements

### FR1 — Brief intake
* FR1.1 Free-text research brief (natural language) per design cycle.
* FR1.2 Upload FASTA (`.fa/.fasta`), PDB (`.pdb`), mmCIF (`.cif`), CSV (assay/variant tables).
* FR1.3 Uploads are stored as immutable artifacts (S3/MinIO) with sha256 content addressing.
* FR1.4 Each uploaded sequence becomes a root commit in the project's version graph.

### FR2 — Devin orchestration (real API, never faked)
* FR2.1 One orchestrator Devin session per design cycle; it plans the cycle and returns a
  structured plan (which child agents to spawn, with what task and ACU budget).
* FR2.2 The orchestrator fans out to child sessions for the roles: `sequence`, `structure`,
  `docking`, `literature`, `ranking`.
* FR2.3 Every session is created with tags (`dyb-pro`, `project:<id>`, `cycle:<n>`,
  `role:<role>`), a role playbook, a `max_acu_limit`, and a JSON-Schema
  `structured_output_schema` for machine-readable handoff.
* FR2.4 Sessions are polled to completion; status, messages, ACU usage and structured output are
  persisted as observations. Failed/blocked sessions are retried with bounded attempts.
* FR2.5 A cycle can be cancelled; all live child sessions are terminated via the API.
* FR2.6 Playbooks are created/reconciled through the Devin playbooks API and reused by id.
* FR2.7 The execution provider of every agent run is recorded (`devin` or `local-simulation`)
  and surfaced in API + UI. Simulation requires an explicit env opt-in.

### FR3 — In-silico toolkit (open source only)
* FR3.1 Homology/similarity search over the project corpus + seeded reference set.
* FR3.2 Structure handling: parse PDB/mmCIF, geometry (Rg, contacts, SASA proxy, secondary
  structure fractions), coarse fold proxy when no structure is supplied.
* FR3.3 Developability heuristics: stability (ΔΔG proxy), solubility, aggregation propensity,
  immunogenicity (MHC-II-like), plus liability motifs (N-glycosylation, deamidation, free Cys,
  protease sites) and charge/pI/GRAVY descriptors.
* FR3.4 Coarse docking / MD proxy: rigid-body contact scoring with an ensemble of perturbations
  giving a binding score *and* an uncertainty estimate.
* FR3.5 Developability filters (hard gates) separate from soft scores.
* FR3.6 Every score is deterministic given the same input and carries a `method` + citation.

### FR4 — Ranking and explanation
* FR4.1 Multi-objective ranking with per-objective normalization and configurable weights.
* FR4.2 Uncertainty per candidate (ensemble spread) and a Pareto-front flag.
* FR4.3 "Why this, not that": pairwise explanation against the next-best and the parent design.

### FR5 — Protein version control
* FR5.1 A commit is immutable and content-addressed over
  (sequence, structure ref, scores, parents, agent, prompt, citations, timestamp).
* FR5.2 Full lineage query (ancestors/descendants) and DAG export.
* FR5.3 Diff between any two commits: mutation list, score deltas, rationale delta.
* FR5.4 Branches (`main` plus named branches) and merge of two branch heads, producing a merge
  commit with two parents and a recombined sequence where mutations are compatible; conflicts are
  reported, never silently resolved.
* FR5.5 "What changed and why" is answerable from the commit alone (rationale + prompt +
  citations + producing agent + session URL).

### FR6 — Continuous learning
* FR6.1 Every agent observation is persisted (kind, payload, agent, cycle).
* FR6.2 Before planning, the orchestrator prompt includes a *history digest*: prior cycles, the
  best/worst commits, which mutations succeeded or failed a filter and why, and open questions.
* FR6.3 Child agents receive the same digest plus their parent commit's lineage.
* FR6.4 Cycle N+1 must not re-propose a mutation already rejected in cycle ≤N unless it states a
  reason (enforced by the ranking agent's exclusion list).

### FR7 — Wet-lab pack
* FR7.1 Shortlist export with sequences, scores, uncertainty, rationale.
* FR7.2 Mutagenesis primers (forward/reverse, Tm, GC%) and construct definition (vector, tags,
  codon-optimized ORF).
* FR7.3 Predicted assay plan per candidate (assay, readout, expected direction).
* FR7.4 Cost/risk model: cost of the shortlist vs. testing every candidate, expected hit
  probability, and risk notes.
* FR7.5 Citations for every method used.
* FR7.6 Export as CSV, JSON and FASTA.

### FR8 — Product surface
* FR8.1 Chat workspace (brief in, agent narration out).
* FR8.2 Agent swarm view: live child sessions, role, status, ACUs, session link.
* FR8.3 Protein version DAG visualization with commit inspector.
* FR8.4 Structure viewer (3D backbone) and score viewer (bar/radar).
* FR8.5 Experiment queue with logs, attempts and retry.
* FR8.6 Provenance panel per commit.
* FR8.7 RBAC (`admin`, `scientist`, `viewer`) via API keys; usage quotas (ACU + cycle count).
* FR8.8 OpenAPI schema at `/openapi.json`, docs at `/docs`.

### FR9 — Demoability
* FR9.1 `docker compose up --build` is the only command needed.
* FR9.2 Seeded demo project with reference proteins loads in one click.
* FR9.3 Three-click happy path: load demo → run cycle → export shortlist.
* FR9.4 A stranger understands the value in 90 seconds (DEMO.md script).

## Non-functional

* NFR1 Secrets only from environment (`DEVIN_API_KEY`, `OPENAI_API_KEY`); never persisted,
  never logged, never returned by the API.
* NFR2 OpenAI is used *only* for multimodal/structured analysis and narration; it never
  substitutes for Devin orchestration, and the system degrades to deterministic heuristics
  without it.
* NFR3 Deterministic core: toolkit and versioning are pure functions, unit tested offline.
* NFR4 All third-party scientific components are open source (BioPython, NumPy, RDKit optional).
  No proprietary code, UI, text or data.
* NFR5 Every long-running action is a queued task with logs, bounded retries and cancellation.

## Out of scope (explicitly)

Real folding networks (AlphaFold/ESMFold weights), real MD engines, LIMS integration, ordering
APIs, and multi-tenant billing. Interfaces are stubbed at the boundary so they can be swapped in
(`toolkit/folding.py::FoldingBackend`).
