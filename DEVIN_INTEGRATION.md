# Devin integration

Foldsmith treats Devin as the **research supervisor**, not as a chat sidebar. Every design cycle is
one orchestrator session that fans out to specialized child sessions, and every artifact Foldsmith
stores carries the session that produced it.

Implementation: [`backend/app/devin/`](backend/app/devin) —
`client.py` (HTTP), `runner.py` (lifecycle), `playbooks.py`, `prompts.py`, `schemas.py`,
`simulation.py` (offline fallback).

## 1. Credentials and provider resolution

| env | meaning |
| --- | --- |
| `DEVIN_API_KEY` | organization or personal API key |
| `DEVIN_ORG_ID` | required for the v3 organization API (`cog_…` keys) |
| `DEVIN_API_FLAVOR` | `v3` (default) or `v1` |
| `DEVIN_API_BASE` | `https://api.devin.ai` |
| `ALLOW_LOCAL_SIMULATION` | allow the offline fallback when no key is present |

`runner.resolve_provider()` decides, per cycle:

1. `DEVIN_API_KEY` present (plus `DEVIN_ORG_ID` for v3) → provider `devin`, real sessions.
2. otherwise, if `ALLOW_LOCAL_SIMULATION=true` → provider `local-simulation`.
3. otherwise the cycle fails loudly rather than pretending.

The provider is persisted on the cycle, on every agent run and on every protein commit, surfaced in
`GET /api/providers` and rendered as a badge in the UI. **A simulated run is never labelled Devin.**
Keys are read from the environment only — never logged, never written into commits or exports.

## 2. Endpoints used

v3 (default, organization-scoped):

| purpose | call |
| --- | --- |
| create session | `POST /v3/organizations/{org_id}/sessions` |
| poll session | `GET /v3/organizations/{org_id}/sessions/{devin_id}` |
| message session | `POST /v3/enterprise/sessions/{devin_id}/messages` |
| cancel / archive | `POST /v3/organizations/{org_id}/sessions/{devin_id}/archive` |
| list playbooks | `GET /v3/organizations/{org_id}/playbooks` |
| create playbook | `POST /v3/organizations/{org_id}/playbooks` |

v1 (legacy personal keys, `DEVIN_API_FLAVOR=v1`): `POST /v1/sessions`,
`GET /v1/session/{id}`, `POST /v1/session/{id}/message`.

## 3. Session topology per cycle

```
orchestrator (1 session, ACU limit DEVIN_ORCHESTRATOR_ACU_LIMIT)
├── sequence-designer   → mutation proposals with rationale
├── structure-analyst   → fold/geometry critique of proposals
├── docking-md          → interface + coarse-dynamics read-out
├── literature-scout    → precedent, citations, known liabilities
└── ranking             → orthogonal shortlist triage
```

The orchestrator receives the brief, the deterministic evidence pack for the parent design and the
history digest, and must return a **plan**: strategy plus the child agents to spawn. Foldsmith then
launches exactly those children as separate sessions parented to the orchestrator.

## 4. Playbooks, tags, ACU limits, structured output

* **Playbooks** — one per role in `playbooks.py`. On the first cycle the runner reconciles them with
  the org (`list` → `create` if missing) and caches the ids in `playbook_refs`, so child sessions are
  started with a stable, reviewable procedure rather than an ad-hoc prompt.
* **Tags** — every session is tagged `foldsmith`, `foldsmith:role:<role>`,
  `foldsmith:project:<id>`, `foldsmith:cycle:<id>`, `foldsmith:round:<n>`, which makes a whole design
  round filterable in the Devin UI.
* **ACU limits** — the orchestrator and each child get explicit ceilings
  (`DEVIN_ORCHESTRATOR_ACU_LIMIT`, `DEVIN_CHILD_ACU_LIMIT`) and consumed ACUs are recorded per run
  and aggregated onto the cycle for the usage quota.
* **Structured handoff** — each role has a JSON schema (`schemas.py`) requested as required
  structured output. Only schema-valid output becomes a candidate; a session that ends without valid
  structured output is a run failure, re-launched up to `AGENT_MAX_ATTEMPTS`, never silently coerced.

## 5. Lifecycle: poll, retry, cancel

`runner.AgentSupervisor` owns the loop: `launch → poll (DEVIN_POLL_INTERVAL_SECONDS) → collect
structured output → finish`, with a `DEVIN_SESSION_TIMEOUT_SECONDS` ceiling, re-launch of failed runs
within the attempt budget, `timeout` status when the budget is exhausted, and cancellation that
archives the live sessions. Every transition is appended to the run's log, which is what the
agent-swarm view streams.

## 6. Provenance and continuous learning

Each agent run stores `devin_session_id`, `devin_session_url`, `devin_status`, `playbook_id`, `tags`,
`attempts`, `acus`, prompt and structured output. Each protein commit stores the agent role, the
provider, the session URL and the prompt that produced it, so any design in the DAG is traceable back
to the exact session.

Before planning, the next cycle reads `services/learning.history_digest()`: completed rounds, best and
failed designs, the mutation ledger, exclusions (mutations already tried and rejected, with the
reason) and open questions raised by earlier agents. That digest goes into the orchestrator prompt, so
round *n+1* is constrained by what rounds *1…n* actually observed.

## 7. Offline fallback (`local-simulation`)

`simulation.py` runs the same deterministic toolkit in-process so the demo works without credentials.
It is labelled `local-simulation` on the cycle, on every run, on every commit, in the API and in the
UI badge. It exists for reproducible tests and offline demos — it is not a Devin substitute.
