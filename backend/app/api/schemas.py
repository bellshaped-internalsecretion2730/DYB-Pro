"""Pydantic request/response models (also the OpenAPI contract)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    goal: str = ""
    target_name: str = ""
    target_sequence: str = ""


class ProjectOut(BaseModel):
    id: str
    name: str
    goal: str
    target_name: str
    target_sequence: str
    is_demo: bool
    created_at: datetime
    commit_count: int = 0
    cycle_count: int = 0
    branches: list[str] = []
    head_commit_id: str | None = None


class CycleCreate(BaseModel):
    brief: str = Field(min_length=1, description="natural-language research brief for this cycle")
    branch: str = "main"
    acu_limit: int = Field(default=20, ge=1, le=200)


class CycleOut(BaseModel):
    id: str
    project_id: str
    brief: str
    round: int
    status: str
    provider: str
    branch: str
    summary: str
    error: str | None = None
    acu_limit: int
    acus_used: float
    orchestrator_session_url: str | None = None
    plan: dict = {}
    created_at: datetime
    finished_at: datetime | None = None


class AgentRunOut(BaseModel):
    id: str
    role: str
    task: str
    provider: str
    status: str
    devin_status: str | None = None
    devin_session_url: str | None = None
    playbook_id: str | None = None
    tags: list[str] = []
    attempts: int
    acu_limit: int
    acus: float
    structured_output: dict = {}
    log: list = []
    error: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class CommitOut(BaseModel):
    id: str
    short_id: str
    project_id: str
    branch: str
    label: str
    message: str
    sequence: str
    structure_key: str | None = None
    structure_source: str
    parent_ids: list[str] = []
    mutations: list = []
    scores: dict = {}
    uncertainty: dict = {}
    filters: dict = {}
    rationale: str
    agent_role: str
    provider: str
    devin_session_url: str | None = None
    prompt: str = ""
    citations: list = []
    cycle_round: int
    created_at: datetime


class BranchCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    from_commit: str


class MergeRequest(BaseModel):
    ours: str
    theirs: str
    message: str | None = None


class ObservationOut(BaseModel):
    id: str
    cycle_id: str | None = None
    role: str
    kind: str
    summary: str
    payload: dict = {}
    created_at: datetime


class ProviderStatus(BaseModel):
    provider: str | None = None
    devin_configured: bool
    devin_api_flavor: str
    devin_reachable: bool | None = None
    devin_error: str | None = None
    local_simulation_allowed: bool
    openai_configured: bool
    error: str | None = None
