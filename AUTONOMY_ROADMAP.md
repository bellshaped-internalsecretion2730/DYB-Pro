# Foldsmith autonomy & wet-lab-cost roadmap

Evidence base: a 100-agent literature survey of autonomous R&D in protein design and drug
discovery (~4,000 unique sources, primary literature and vendor price lists), plus a file-level
inventory of this repository and the standing logical review of the science stack.

This document is a plan, not a claim about the current build. Every item is scoped to be additive
to the existing architecture: new modules plus narrow edits at named call sites, no rewrite of the
cycle engine, the version DAG, the Devin runner or the API surface.

---

## 1. Where the field actually is (and what that means for us)

Three findings from the survey drive everything below.

1. **Generation is cheap and solved-enough; the oracle is the bottleneck.** De novo binder design
   now reports 10–46% experimental success on tractable targets, but in blinded third-party
   evaluation the correlation between computational leaderboard rank and measured affinity is
   ~0.2, and zero within the top half of a shortlist. First-party hit rates degrade ~2x on
   independent remeasurement. Improving the generator does not reduce wet-lab spend; improving
   (or honestly quantifying) the *selector* does.
2. **Nobody measures filter recall.** Essentially no published campaign synthesises a random
   sample of the designs its filters *rejected*, so only enrichment is observable and the
   false-negative rate is unknown everywhere in the field. This is a cheap, differentiating thing
   for a pre-wet-lab system to measure.
3. **Autonomy tracks readout arity, not model quality.** Every near-autonomous loop in the
   literature inherited a single-scalar, machine-readable, human-authored assay. Our objective
   field is free text; that is the actual gate on autonomy here, not the models.

Consequence for Foldsmith: the highest-value work is not a better fold. It is the **decision
layer** — a machine-checkable objective, a real price model, an expected-cost stop rule, and a
measured confusion matrix — plus honest labelling of everything that is not yet calibrated.

---

## 2. Where the 10x can honestly come from

Cost per *validated* hit, not cost per candidate, is the metric. Decompose it:

```
cost_per_validated_hit = (batch_cost / n_tested) / (PPV * P(independent))
```

| Lever | Mechanism | Realistic factor | Depends on |
|---|---|---|---|
| **Assay-modality routing** | Route round 1 through pooled enrichment / outsourced express expression+BLI instead of in-house purification+SPR for every candidate. The current model charges purification + SPR per candidate; outsourced per-protein characterisation is roughly an order of magnitude below that, and pooled display screens are cents per variant at the enrichment stage. | 3–10x | price list + modality tiers (§3.1) |
| **Diversity-aware shortlisting** | Today a shortlist can spend N assays on N near-identical variants of one design. Cluster by sequence identity and cap per-cluster picks: the same budget buys more *independent* bets. | 1.3–2x | clustering (§3.3) |
| **Sequential stop rule** | Stop at the expected number of hits instead of a fixed shortlist size; batch to vendor minimum-charge boundaries. | 1.2–2x | expected-cost engine (§3.2) |
| **Calibrated triage** | Rank by measured PPV per filter/threshold instead of an uncalibrated composite. | unknown until measured | recall probe + calibration (§3.4, §4.5) |

The first three levers are **price-list and set-theory arithmetic that happen entirely pre-wet-lab**;
their product already spans 5–40x, which is where a defensible 10x lives. The fourth lever — the one
everybody advertises — cannot be quantified until we have a confusion matrix, and must not be
claimed before then.

Two rules this imposes on the product:

- **Baselines must be external.** `spend_avoided_usd = cost_per_candidate * not_shortlisted`
  (`backend/app/services/wetlab.py:187-290`) compares the shortlist to our own candidate pool, so
  the percentage is a function of how many candidates we happened to generate. Replace it with a
  declared baseline (naive-N-at-Tier-2, or the campaign's own previous round) and report the
  comparison as an interval with the price-list version attached.
- **Point estimates of hit rate are not available to us.** Report bands, and apply an explicit
  cross-lab shrinkage factor to any literature-derived prior.

---

## 3. P0 — the decision layer (highest value, lowest blast radius)

All four items are new modules plus narrow call-site edits; none of them touch folding, docking or
the DAG.

### 3.1 Versioned price list with modality tiers
`backend/app/services/wetlab.py:16-27` is a flat dict of scalars with no vendor, no date, no minimum
charge and no batching. Replace with a versioned catalogue: `{vendor, sku, url, quoted_on, unit,
unit_cost, min_charge, batch_size}`, grouped into tiers — `T0` pooled/display enrichment, `T1`
outsourced express expression + binding characterisation, `T2` in-house purification + SPR/nanoDSF.
Store `price_list_version` on the wet-lab pack and in the commit hash inputs so historical costs are
reproducible. `_candidate_cost` (`:161-184`) takes a tier argument; `build_pack` picks the cheapest
tier that can actually decide the project's success criterion (§5).

### 3.2 Expected-cost and stop-rule engine (new `backend/app/services/economics.py`)
Inputs: shortlist, tier, price list, per-target designability prior (band, not a point, shrunk for
cross-lab degradation), budget. Outputs: `batch_cost`, `expected_hits` interval,
`cost_per_validated_hit` interval, recommended `n_tested`, and the stop rule that produced it
(`test until P(>=1 hit) >= target or budget exhausted`). This replaces `spend_avoided_usd` as the
headline number, and every field carries its provenance.

### 3.3 Diversity-aware shortlist
Cluster candidates with the existing global aligner (`backend/app/toolkit/sequence.py:315-373`) at a
configurable identity threshold, cap picks per cluster, and expose `independent_bets` on the pack.
Also fixes the 64-character dedup key noted in the logical review. Pure gain: strictly more
information per dollar, no new external dependency.

### 3.4 Recall probe
Add k randomly sampled *filter-rejected* candidates to the pack, tagged `probe=true` and excluded
from the shortlist ranking. When their results land in `MeasuredResult`
(`backend/app/models.py:195-218`), we can compute a real confusion matrix per filter per threshold
and publish false-negative rate — a number that does not currently exist anywhere in this field.
Costs ~10% of round-1 budget and is the thing that licenses every later precision claim.

---

## 4. P1 — oracle honesty and calibration

### 4.1 Oracle registry and oracle cards
Each score gets a record: `oracle_id`, version, what it is (physical quantity vs. proxy), benchmark
set, N, metric value, calibration status, OOD status. Exposed at `/api/oracles` and rendered next to
every number in the UI. **A score with no benchmark renders as a rank, never as a value with units.**

### 4.2 Fail-closed structure input
`fold_sequence` (`backend/app/toolkit/folding.py:251-259`) defaults to a deterministic CA trace
labelled `model:coarse-geometric`. Keep it as a *picture*: mark it `visual_only` and make the
burial/exposure (`toolkit/structure.py:167-186`), ddG burial term
(`toolkit/developability.py:140-220`), compactness and docking (`toolkit/docking.py:111-176`) paths
refuse to consume it. Add an `ExternalPredictorBackend` behind the existing backend protocol
(ESMFold/Boltz/Chai via API, or an uploaded experimental structure) so structure-derived scores exist
only when a real structure does.

### 4.3 ddG proxy: enforce antisymmetry or rename it
The proxy currently returns the same value for A→W and W→A. Either enforce
`ddg(wt→mt) == -ddg(mt→wt)` and benchmark on a balanced set (S_sym / ThermoMutDB) reporting
Pearson/RMSE and the destabilising bias, or rename to `substitution_risk` with no kcal/mol unit and
drop the `max_ddg` filter (`developability.py:264-356`). Add a unit test asserting antisymmetry.

### 4.4 Out-of-distribution guard
Score every input against the reference panel by sequence identity; flag OOD, widen uncertainty, and
show it in the viewer. The survey's clearest failure mode is oracles that look accurate in-domain and
collapse on novel inputs — a protein-blind, ligand-only baseline reaching r=0.66 on a flagship
affinity benchmark and 0.14 on novel ligands.

### 4.5 Real calibration jobs
Extend `backend/app/services/calibration.py` from rank correlation to per-objective proxy→assay
fits (isotonic or linear) with n and confidence intervals, plus drift per objective per round. Gate
all calibrated-sounding language on `n >= n_min`; below that the UI says "uncalibrated".

---

## 5. P2 — machine-checkable objectives, closed loop, autonomy metrics

### 5.1 Problem spec (the actual autonomy gate)
`Project.goal` is free text. Add a `ProblemSpec`: target, epitope/site, required readout, assay that
decides it, success threshold, kill criterion. Then "test the fold against the given problem" becomes
a computable predicate: every shortlist entry is scored against the declared threshold, and the pack
states which single assay adjudicates. Without this, no round can run unattended, regardless of model
quality.

### 5.2 Round policy and autonomy levels
Store a policy on `DesignCycle`: budget, objective, stop rule, autonomy level L1–L4 (L1 = human
approves each design; L4 = agent runs a full round inside a frozen budget and threshold policy).
Agents may act unattended only within the policy; anything outside it becomes a human-decision event.

### 5.3 Autonomy ledger
Log `decided_by` on every consequential decision and expose `/api/autonomy`: override rate (fraction
of computationally proposed designs that a human changed before ordering), interventions per round,
$ and ACU per round, wall-clock per round. 71% of surveyed self-driving-lab papers report no
quantitative autonomy metric at all; publishing these makes our autonomy claims falsifiable, and
they are nearly free to collect.

### 5.4 Biosecurity gate
Screen every construct/primer emission (`services/wetlab.py:134-158`) against a hazard list before
it can be presented as orderable; block and log. Mandatory before any real synthesis order leaves
the system.

### 5.5 Research cache with DOIs, and agent abstention
Commit citations are free-text strings today. Adopt the research-daemon branch's
`ResearchPaper`/`ResearchEvent` tables as an immutable cache keyed by DOI, require agents to cite
cache entries (no free-text citations), and allow explicit abstention — the best retrieval agents in
the literature abstain on ~22% of questions, and abstention is what keeps precision high.

---

## 6. P3 — engineering hygiene that blocks the above

- Alembic migrations: `backend/app/db.py:28-32` uses `Base.metadata.create_all`; every schema change
  above needs real migrations.
- Remove the synchronous fallback in `enqueue_cycle` (`backend/app/worker.py:64-72`), which can run a
  full cycle inside an HTTP request when the broker is down.
- Content-address artifacts by `sha256` of bytes, not by path key, and include the semantically
  relevant fields in the commit hash.
- Adopt the research-daemon branch's CI workflow (Ruff + pytest + typecheck + build) on `main`.
- Frontend tests: there are none. Add snapshot tests per UI zone (§7) and a Playwright demo-path E2E.

---

## 7. UI alignment (three-zone redesign)

Mapping to the redesign's zones. Every element below is backed by a real endpoint; no zone shows
synthetic activity.

**LEFT — Ask & Agent Control.** Adds the policy surface: budget, target hits, autonomy level L1–L4,
stop rule, price-list version, recall-probe toggle. Recent commands, cycle queue, session history.
Keyboard-first; a policy edit is an explicit, logged event.

**CENTER — Protein Viewer.** Renders only real structures at full fidelity; a coarse trace carries a
persistent `visual only — not a prediction` badge and its derived metrics are suppressed rather than
greyed. OOD banner when the input is outside the reference panel. Hovering any metric opens its
oracle card (§4.1).

**CENTER-BELOW — Folded Protein Reference Strip.** Per-version chips: calibration status, cost-to-test
at the chosen tier, independent-bet cluster id, and — once results land — the measured-vs-predicted
drift delta. Drag-to-compare shows the decision-relevant diff (which objective moved, whether it moved
beyond its calibrated resolution), not just score deltas.

**RIGHT — Pixel Agent Swarm.** Roles extended with `oracle-runner`, `calibrator`, `recall-prober`,
`biosec-gate`. Sprites bind to real `AgentRun.status`; an idle agent is visibly idle (no ambient
motion), and each sprite shows its $ and ACU spend. A human takeover control writes to the autonomy
ledger (§5.3), which is what makes the override rate measurable.

---

## 8. Branch integration order (non-destructive)

1. **`devin/…-foldsmith-science-honesty-fixes` first.** It is the authoritative science/honesty branch
   (tenancy, `MeasuredResult`, calibration, fixed ranking windows, avoided-spend caveats) and
   everything here assumes it. Land before anything else.
2. **`devin/…-foldsmith-research-daemon` second**, rebased: it adds the research cache, daemon, CI and
   Playwright E2E, and it collides with Pharmakon on `models.py`, `config.py`, `worker.py`,
   `devin/playbooks.py`, `devin/schemas.py`, `seed.py`, `frontend/app/page.tsx` and `frontend/lib/api.ts`.
3. **`devin/…-pharmakon-drug-discovery` third, behind a feature flag** — it is a parallel product
   surface (chem/ADMET/PK) on shared ORM and worker files; flag it so protein-design regressions are
   impossible.
4. **`bati-1` / `bati-2`: do not merge.** `bati-1` has no common merge base with `main` (unrelated
   history) and both replace the application architecture. Cherry-pick the Mol* viewer components
   into `frontend/components/` for the CENTER zone and leave the rest.

---

## 9. Ordered TODO list

**P0 — decision layer**
1. Versioned price list + modality tiers T0/T1/T2; `price_list_version` on the pack (§3.1).
2. `services/economics.py`: expected hits, `cost_per_validated_hit` interval, stop rule (§3.2).
3. Retire `spend_avoided_usd` as a headline; report against a declared external baseline (§2).
4. Diversity-aware shortlist clustering + `independent_bets`; fix the 64-char dedup key (§3.3).
5. Recall probe in the pack + confusion-matrix endpoint fed by `MeasuredResult` (§3.4).

**P1 — oracle honesty**
6. Oracle registry, `/api/oracles`, oracle cards; unbenchmarked scores render as ranks (§4.1).
7. `visual_only` on coarse folds; structure-derived scores fail closed (§4.2).
8. `ExternalPredictorBackend` behind the folding protocol (§4.2).
9. ddG antisymmetry test, or rename to `substitution_risk` and drop `max_ddg` (§4.3).
10. OOD guard with uncertainty widening (§4.4).
11. Per-objective proxy→assay calibration fits with n/CI and drift per round (§4.5).

**P2 — autonomy**
12. `ProblemSpec` + machine-checkable success predicate per shortlist entry (§5.1).
13. Round policy with autonomy levels L1–L4 on `DesignCycle` (§5.2).
14. Autonomy ledger + `/api/autonomy` (override rate, interventions, $/ACU, wall-clock) (§5.3).
15. Biosecurity gate before any orderable construct (§5.4).
16. DOI-keyed research cache; agents cite cache entries and may abstain (§5.5).

**P3 — hygiene**
17. Alembic migrations; drop `create_all` at startup.
18. Remove the synchronous `enqueue_cycle` fallback.
19. Content-address artifacts by byte hash; extend commit-hash inputs.
20. CI on `main`; frontend zone snapshot tests + Playwright demo path.

**Order matters:** 1–5 change what the system spends money on, and are the only items that reduce
wet-lab cost by arithmetic rather than by hope. 6–11 stop the system from making claims it cannot
support. 12–16 are what "more autonomous" actually means, measurably. 17–20 unblock the rest.
