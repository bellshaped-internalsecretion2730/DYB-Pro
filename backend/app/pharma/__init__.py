"""Pharmakon: the autonomous drug-discovery program layer on top of DYB Pro.

Pure decision logic only — no database, no network, no agent calls. Everything in this package
is deterministic and replayable from stored evidence, which is what makes a gate decision
auditable after the fact.
"""

from app.pharma.dossier import build_dossier, dossier_markdown
from app.pharma.economics import portfolio_view, program_economics
from app.pharma.experiments import experiment_plan, propose_experiments
from app.pharma.gates import GateResult, evaluate_gate
from app.pharma.metrics import molecule_metrics, prediction_drift, program_metrics
from app.pharma.stages import (
    AUTONOMY_LEVELS,
    DECISIONS,
    FIRST_STAGE,
    STAGE_KEYS,
    STAGES,
    TERMINAL_STAGE,
    Stage,
    ladder,
    next_stage,
    stage,
)

__all__ = [
    "AUTONOMY_LEVELS",
    "DECISIONS",
    "FIRST_STAGE",
    "STAGES",
    "STAGE_KEYS",
    "TERMINAL_STAGE",
    "GateResult",
    "Stage",
    "build_dossier",
    "dossier_markdown",
    "evaluate_gate",
    "experiment_plan",
    "ladder",
    "molecule_metrics",
    "next_stage",
    "portfolio_view",
    "prediction_drift",
    "program_economics",
    "program_metrics",
    "propose_experiments",
    "stage",
]
