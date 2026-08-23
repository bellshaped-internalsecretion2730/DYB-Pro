# Foldsmith skills

A **skill** is the only sanctioned way a number enters Foldsmith's research record. Each skill is an
executable module in `backend/app/skills/` with a pydantic input and output model (so its JSON Schema
is machine-readable), unit tests in `backend/tests/test_skills.py`, and a one-page contract here.

| Skill | Module | Contract |
| --- | --- | --- |
| `skill.literature` | `backend/app/skills/literature.py` | [literature.md](literature.md) |
| `skill.math` | `backend/app/skills/mathematics.py` | [math.md](math.md) |
| `skill.physics` | `backend/app/skills/physics.py` | [physics.md](physics.md) |
| `skill.chemistry` | `backend/app/skills/chemistry.py` | [chemistry.md](chemistry.md) |
| `skill.drug_discovery` | `backend/app/skills/drug_discovery.py` | [drug_discovery.md](drug_discovery.md) |
| `skill.wetlab_metrics` | `backend/app/skills/wetlab_metrics.py` | [wetlab_metrics.md](wetlab_metrics.md) |

## Calling a skill

```python
from app import skills

out = skills.run("skill.physics", {"sequence": seq, "structure_pdb": pdb})
```

`skills.run` validates the payload against the input model, runs the skill, validates the result and
stamps `skill` / `skill_version` onto the output. The live catalogue (name, version, summary,
citations, both JSON Schemas) is served at `GET /api/research/metrics` and injected into every Devin
child prompt.

## Provenance rule

Every metric a skill emits carries `value`, `unit`, `sd`, `method`, `skill` and `citations`.
`skills.require_skill_metrics()` rejects any metric without that stamp, which is how the daemon
enforces *agents may only claim a metric if a skill produced it* - a hallucinated Tm cannot reach a
research event, a wet-lab plan or a commit.

## Empirical grounding

Every number in these skills is labelled with where it came from, and *not every number comes from a
paper*. Each skill registers its heuristics in `app.skills.evidence` with a claim, an applicability
range, a known error magnitude and a calibration status; the same table appears in the skill's
contract here, and `backend/tests/test_evidence.py` enforces that the records exist, carry DOIs where
required and stay in sync with these documents.

| Status | Constant | Meaning |
| --- | --- | --- |
| calibrated | `evidence.CALIBRATED` | published method **and** published benchmark error for this quantity; the `sd` we emit *is* the literature error (e.g. pI, 0.87 pH units) |
| anchored | `evidence.ANCHORED` | the formula is published, but our constants/inputs are not the source's fitted values, so the error bar comes from a benchmark of comparable methods, not of this implementation |
| policy | `evidence.POLICY` | a Foldsmith decision: a pass/fail gate, a deliberately conservative sd, a safety margin. No paper says "45 C" - the platform does, and says so |
| proxy | `evidence.PROXY` | an uncalibrated screening heuristic: hand-chosen weights, motif counts, arbitrary units. Ranks designs of the same protein; never a physical claim |
| exact | `evidence.EXACT` | a closed-form mathematical result (error propagation, conjugate update, OLS). Nothing to calibrate; the assumptions are stated instead |

Consequences that matter when reading any output: a `policy` threshold must never be presented as a
literature acceptance criterion, and a `proxy` score has no unit and no probability interpretation.
Where an error bar is wider than the published one (assay sds in `skill.wetlab_metrics`, the Tm
uncertainty in `skill.physics`) the widening is deliberate and marked `policy`, so the drift model is
not triggered by an artefact.
