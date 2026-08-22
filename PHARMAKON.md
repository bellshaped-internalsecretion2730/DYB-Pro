# Pharmakon — the autonomous drug-discovery layer

DYB Pro versions proteins. **Pharmakon** runs *programs*: a target, a molecule portfolio, a
stage ladder with hard decision gates, wet-lab experiments it asks for, results it ingests, and a
draft IND-style dossier at the end. Devin is the research runtime — an orchestrator session fans
out to role-specialised children — but Devin never decides anything: agents supply numbers and
citations, deterministic Python evaluates the gate.

```
seed molecules
   -> round: medchem proposes analogs, ADMET/tox/DMPK/synthesis/IP/clinical agents evaluate
   -> immutable molecule commits (content-addressed, program-scoped)
   -> deterministic gate: go / no_go / recycle / kill  (+ human signature where required)
   -> experiment plan: the assays that would actually change the decision
   -> assay results ingested -> prediction drift measured -> evidence carried into the next round
```

## Stage ladder

| # | stage | the question the gate answers |
| --- | --- | --- |
| 1 | `target_assessment` | is this target worth a program at all? |
| 2 | `hit_finding` | is there a real starting point? |
| 3 | `hit_to_lead` | can the hit be made drug-like? |
| 4 | `lead_optimization` | is there a lead with potency, selectivity and ADMET together? |
| 5 | `dmpk_safety` | does exposure and margin survive contact with DMPK/tox? |
| 6 | `candidate_selection` | is one molecule (plus a backup) worth nominating? |
| 7 | `ind_enabling` | is the IND package coherent? *(always human-signed)* |
| 8 | `trial_design` | is the first-in-human design defensible? *(always human-signed)* |

Each gate has weighted criteria with explicit requirements. A criterion with **no evidence** is not
a failure: the program recycles or holds rather than killing itself on silence.

## Autonomy

| level | what Pharmakon may do alone |
| --- | --- |
| L0 | recommend only; a human executes every decision |
| L1 | re-run design rounds; never changes stage |
| L2 | advance itself up to lead optimisation |
| L3 | advance itself up to candidate selection |
| L4 | advance every stage that is not human-facing |

Regardless of level, `ind_enabling` and `trial_design`, kill decisions, and anything irreversible
or human-facing require a recorded human approval before they apply.

## Surfaces

| Surface | Where |
| --- | --- |
| Program board (ladder, gate + approvals, portfolio, experiments, drift, economics, evidence) | `frontend/app/pharmakon`, `frontend/components/ProgramBoard.tsx` |
| Program API (`/api/pharma/...`) | `backend/app/api/pharma_routes.py`, `pharma_schemas.py` |
| Round orchestration, commits, gates, memory | `backend/app/services/program.py` |
| Gates, metrics, experiments, drift, dossier, economics | `backend/app/pharma/` |
| Pure-Python cheminformatics (no RDKit) | `backend/app/chem/` (methods cited in `backend/app/chem/CITATIONS.md`) |
| Devin roles, prompts, playbooks, structured schemas, daemon, campaign memory | `backend/app/devin/pharma_*.py`, `daemon.py`, `campaign_memory.py` |
| Tests | `backend/tests/test_chem.py`, `test_pharma_engine.py`, `test_pharma_api.py` |

## What the numbers are — and are not

Pharmakon is calibrated to be useful for triage and honest about it. Every one of these appears in
the API payloads and in the UI, not just in this file.

* **Binding (`pkd`)** — a sequence-derived ligand/pocket complementarity proxy. Not docking, not
  free-energy perturbation, not a substitute for a binding assay.
* **PK** — a deterministic one-compartment projection from computed descriptors. Not validated
  human PK; species scaling is not modelled.
* **ADMET and toxicology** — heuristic risk hypotheses from descriptors and structural alerts.
  They rank molecules against each other; they do not predict outcomes in a person.
* **Synthesis** — a complexity/fragment heuristic with order-of-magnitude cost. Not
  reaction-database retrosynthesis; no route is validated.
* **Target, IP and clinical claims** — only count when accompanied by a citation. The API rejects
  a gate-moving metric submitted without one.
* **Experiment costs** — CRO list-price order-of-magnitude estimates for prioritisation.
* **Economics** — the human baseline is a published-average comparison, not a quote for your
  program.
* **Dossier** — a draft package assembled for human review. It is not a regulatory submission and
  no section implies GLP completion or agency agreement.
* **`local-simulation` mode** — cannot read literature, search patents or reach any external
  evidence. Its neutral placeholders are deliberately withheld from gate metrics so that offline
  runs cannot manufacture evidence. Real literature work requires `DEVIN_API_KEY`.

Nothing Pharmakon outputs establishes human safety, clinical validity or regulatory acceptability.
A qualified human remains accountable for every decision it proposes.

## Running it

```bash
cd backend && python -m pytest && python -m ruff check .   # chem + engine + API tests, offline
uvicorn app.main:app --reload                              # SQLite fallback, no credentials needed
cd frontend && npm run typecheck && npm run build
```

Then open <http://localhost:3000/pharmakon>: **load demo program** → **run next round** → sign the
gate when Pharmakon asks for a signature.
