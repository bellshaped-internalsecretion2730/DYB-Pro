"""Prompt construction for the Pharmakon program orchestrator and its specialist children.

Prompts are deterministic functions of the program record, the stage's gate state, the molecule
portfolio and the learning digest, so every stored prompt is a faithful record of what the agent
was told when it produced its output.
"""

from __future__ import annotations

import json

TAG_PREFIX = "pharmakon"

PORTFOLIO_FIELDS = (
    "smiles",
    "molecule_hash",
    "scaffold_key",
    "composite_score",
    "verdict",
)


def tags_for(program_id: str, round_id: str, stage: str, role: str) -> list[str]:
    return [
        TAG_PREFIX,
        f"{TAG_PREFIX}:role:{role}",
        f"{TAG_PREFIX}:program:{program_id[:12]}",
        f"{TAG_PREFIX}:stage:{stage}",
        f"{TAG_PREFIX}:round:{round_id[:12]}",
    ]


def _block(payload: object) -> str:
    return json.dumps(payload, indent=2, default=str, sort_keys=True)


def gate_state_block(gate: dict) -> str:
    """The gate as the agent should see it: what is required, what is observed, what fails."""
    rows = []
    for criterion in gate.get("criteria", []):
        rows.append(
            {
                "metric": criterion["key"],
                "requirement": criterion["requirement"],
                "observed": criterion["observed"],
                "passed": criterion["passed"],
                "blocking": criterion["blocking"],
                "kind": criterion["kind"],
            }
        )
    return _block(
        {
            "stage": gate.get("stage"),
            "current_decision_if_evaluated_now": gate.get("decision"),
            "score": gate.get("score"),
            "criteria": rows,
        }
    )


def portfolio_block(evaluations: list[dict], limit: int = 12) -> str:
    """Compact portfolio view: predictions only, explicitly labelled as predictions."""
    rows = []
    for ev in evaluations[:limit]:
        desc = ev.get("descriptors") or {}
        binding = ev.get("binding") or {}
        admet = ev.get("admet") or {}
        rows.append(
            {
                "smiles": ev.get("smiles"),
                "series": ev.get("scaffold_key"),
                "predicted_pkd": binding.get("pkd"),
                "predicted_pkd_uncertainty": binding.get("uncertainty_pkd"),
                "mw": desc.get("molecular_weight"),
                "clogp": desc.get("clogp"),
                "tpsa": desc.get("tpsa"),
                "qed_like": desc.get("qed_like"),
                "admet_score": admet.get("admet_score"),
                "blocking_alerts": [a.get("name") for a in (ev.get("liabilities") or {}).get("blocking", [])],
                "sa_score": (ev.get("synthesis") or {}).get("sa_score"),
                "composite_score": ev.get("composite_score"),
                "verdict": ev.get("verdict"),
            }
        )
    return _block(rows)


def program_orchestrator_prompt(
    *,
    program: dict,
    stage: dict,
    gate: dict,
    evaluations: list[dict],
    assays: list[dict],
    drift: dict,
    history: str,
    available_roles: list[str],
) -> str:
    return f"""\
You are the orchestrator of one round of an autonomous small-molecule drug-discovery program
running on the Foldsmith/Pharmakon platform. You plan the round and fan out to specialist child
agents. You do not modify code and you do not decide the gate: after your children report, the
gate is evaluated deterministically from the recorded evidence.

# Program
name: {program.get("name")}
target: {program.get("target_name")}
indication: {program.get("indication")}
objective: {program.get("objective")}
autonomy level: L{program.get("autonomy_level")}

# Current stage
{stage.get("key")} — {stage.get("name")}
objective: {stage.get("objective")}
exit deliverable: {stage.get("exit_deliverable")}

# Gate state (deterministic, computed from recorded evidence)
{gate_state_block(gate)}

# Molecule portfolio (PREDICTED values from the platform's deterministic engine, not measurements)
{portfolio_block(evaluations)}

# Ingested experimental results (measured data — outranks every prediction above)
{_block(assays)}

# Prediction drift so far (predicted vs measured potency)
{_block(drift)}

# Program history and what has already been learned
{history}

# Your job
Plan this round so it moves the failing gate criteria. Name those metric keys in `gate_focus`.
Choose 3-6 children from: {", ".join(available_roles)}. Each child gets one specific,
non-overlapping task written as an instruction to a specialist. If a blocking criterion cannot be
settled by prediction, request the measurement in `experiments_requested` instead of guessing.

Respond only with structured output matching the provided schema.
"""


def pharma_child_prompt(
    *,
    role: str,
    task: str,
    program: dict,
    stage: dict,
    strategy: str,
    gate: dict,
    evaluations: list[dict],
    assays: list[dict],
    history: str,
    must_avoid: list[str],
    target_sequence: str | None = None,
    pocket: dict | None = None,
) -> str:
    avoid = "; ".join(must_avoid) or "nothing beyond the program exclusion list"
    target_block = ""
    if target_sequence:
        target_block = f"""
# Target protein
sequence:
{target_sequence}
pocket used for the binding proxy:
{_block(pocket) if pocket else "not defined"}
"""
    return f"""\
You are the Pharmakon **{role}** agent working one round of stage `{stage.get("key")}`
({stage.get("name")}) of a small-molecule drug-discovery program. Work only on your assigned task.
Do not modify any code repository.

# Program
target: {program.get("target_name")} | indication: {program.get("indication")}
objective: {program.get("objective")}

# Round strategy (from the program orchestrator)
{strategy}

# Your task
{task}

# Must avoid
{avoid}
{target_block}
# Gate state this round must move
{gate_state_block(gate)}

# Molecule portfolio (PREDICTED values from a deterministic in-silico engine, not measurements)
{portfolio_block(evaluations)}

# Ingested experimental results (measured data — outranks every prediction above)
{_block(assays)}

# Program history, prior rounds and exclusion list
{history}

# Requirements
- Molecules are exchanged as SMILES. Any molecule you propose must be a valid, chemically sensible,
  synthesisable structure.
- Predictions in this prompt come from deterministic heuristics and a complementarity proxy, not
  from docking, force fields or experiments. Treat them as priors and say where you disagree.
- Every factual claim about the world needs a citation. Never claim clinical validation or human
  safety.
- State the trade-off you expect from each proposal.

Respond only with structured output matching the provided schema.
"""


def experiment_review_prompt(*, program: dict, plan: dict, gate: dict) -> str:
    """Prompt used when a human is asked to approve a proposed experiment package."""
    return f"""\
Experiment package proposed for program {program.get("name")} at gate {gate.get("stage")}.

Gate decision as computed: {gate.get("decision")} (score {gate.get("score")})
Rationale: {gate.get("rationale")}

Proposed experiments:
{_block(plan)}
"""
