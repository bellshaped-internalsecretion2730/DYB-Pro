"""Pydantic contract for the Pharmakon drug-discovery API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ProgramCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_name: str = ""
    target_sequence: str = ""
    pocket_residues: list[int] = []
    indication: str = ""
    objective: str = ""
    autonomy_level: int = Field(default=2, ge=0, le=4)
    seed_smiles: list[str] = Field(
        default=[], description="known actives or fragments to start the portfolio from"
    )


class ProgramOut(BaseModel):
    id: str
    project_id: str
    name: str
    target_name: str
    indication: str
    objective: str
    current_stage: str
    stage_order: int
    stage_name: str
    autonomy_level: int
    autonomy_label: str
    status: str
    provider: str
    rounds_run: int
    stage_cycles: int
    acus_used: float
    assays_ingested: int
    molecule_count: int
    daemon_enabled: bool
    knowledge_note_id: str | None = None
    candidate_molecule_id: str | None = None
    backup_molecule_id: str | None = None
    created_at: datetime
    last_research_at: datetime | None = None


class MoleculeOut(BaseModel):
    id: str
    short_id: str
    program_id: str
    label: str
    smiles: str
    formula: str
    scaffold_key: str
    parent_ids: list[str] = []
    stage: str
    composite_score: float
    verdict: str
    rationale: str
    agent_role: str
    provider: str
    devin_session_url: str | None = None
    citations: list = []
    evaluation: dict = {}
    created_at: datetime


class RoundOut(BaseModel):
    id: str
    program_id: str
    stage: str
    number: int
    status: str
    provider: str
    summary: str
    error: str | None = None
    acus_used: float
    plan: dict = {}
    metrics: dict = {}
    findings: dict = {}
    gate: dict = {}
    experiment_plan: dict = {}
    orchestrator_session_url: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class GateOut(BaseModel):
    id: str
    program_id: str
    round_id: str | None = None
    stage: str
    decision: str
    score: float
    criteria: list = []
    blocking_failures: list = []
    rationale: str
    recommended_actions: list = []
    next_stage: str | None = None
    autonomy_level: int
    requires_approval: bool
    approval_reason: str
    approval_status: str
    approved_by: str | None = None
    approval_note: str
    applied: bool
    created_at: datetime
    decided_at: datetime | None = None


class ApprovalRequest(BaseModel):
    approve: bool
    note: str = ""


class AssayIngest(BaseModel):
    molecule_hash: str = Field(min_length=4, description="molecule commit id (content hash)")
    metric: str = Field(
        min_length=1, description="pkd | half_life_h | hia | tox_margin | selectivity_fold ..."
    )
    value: float
    assay: str = ""
    unit: str = ""
    operator: str = Field(default="=", pattern="^(=|<|>)$")
    source: str = ""
    notes: str = ""


class ResearchIngest(BaseModel):
    claim: str = Field(min_length=1)
    citation: str = ""
    implication: str = ""
    kind: str = "literature"
    role: str = "literature"
    # Optional world-evidence number the deterministic gate consumes (target validation, freedom to
    # operate, ...). Only these keys are accepted, and only together with a citation.
    metric: Literal[
        "",
        "target_evidence_score",
        "druggability_score",
        "freedom_to_operate",
        "citation_count",
        "scale_up_feasibility",
    ] = ""
    value: float | None = None


class ResearchEventOut(BaseModel):
    id: str
    round_id: str | None = None
    role: str
    kind: str
    claim: str
    citation: str
    implication: str
    payload: dict = {}
    provider: str
    created_at: datetime


class ExperimentOut(BaseModel):
    id: str
    program_id: str
    stage: str
    assay: str
    endpoint: str
    unit: str
    gate_metric: str
    molecules: list = []
    predicted_value: float | None = None
    falsification: str
    cost_usd: float
    turnaround_days: int
    blocking: bool
    priority: int
    status: str
    created_at: datetime


class AutonomyUpdate(BaseModel):
    autonomy_level: int = Field(ge=0, le=4)


class DaemonUpdate(BaseModel):
    enabled: bool
    frequency: str = Field(default="daily", pattern="^(hourly|daily|weekly)$")
