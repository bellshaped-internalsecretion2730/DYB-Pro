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

Every threshold and error bar in these skills is traced to a published measurement (see the
citations in each contract), not to intuition. When the literature reports a benchmark accuracy, the
skill carries it as the `sd` on its prediction, so downstream ranking and the drift model start from
honest uncertainty instead of false precision.
