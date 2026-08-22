"""ORM models. Protein commits are immutable; only branch heads move."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(32), default="scientist")  # admin|scientist|viewer
    api_key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    acu_quota: Mapped[int] = mapped_column(Integer, default=200)
    cycle_quota: Mapped[int] = mapped_column(Integer, default=25)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    name: Mapped[str] = mapped_column(String(255))
    goal: Mapped[str] = mapped_column(Text, default="")
    target_name: Mapped[str] = mapped_column(String(255), default="")
    target_sequence: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    commits: Mapped[list[ProteinCommit]] = relationship(back_populates="project")
    branches: Mapped[list[Branch]] = relationship(back_populates="project")


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(32))  # fasta|pdb|cif|csv|plot|structure
    filename: Mapped[str] = mapped_column(String(512))
    key: Mapped[str] = mapped_column(String(512))
    backend: Mapped[str] = mapped_column(String(16), default="s3")  # s3|local
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer, default=0)
    content_type: Mapped[str] = mapped_column(String(128), default="text/plain")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_branch_project_name"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(128))
    head_commit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="branches")


class ProteinCommit(Base):
    """Immutable design commit. `id` is a content hash — never mutate a row."""

    __tablename__ = "protein_commits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    branch: Mapped[str] = mapped_column(String(128), default="main")
    label: Mapped[str] = mapped_column(String(128), default="")
    message: Mapped[str] = mapped_column(Text, default="")
    sequence: Mapped[str] = mapped_column(Text)
    structure_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    structure_source: Mapped[str] = mapped_column(String(64), default="none")
    parent_ids: Mapped[list] = mapped_column(JSON, default=list)
    mutations: Mapped[list] = mapped_column(JSON, default=list)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    uncertainty: Mapped[dict] = mapped_column(JSON, default=dict)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    agent_role: Mapped[str] = mapped_column(String(64), default="human")
    provider: Mapped[str] = mapped_column(String(32), default="human")
    devin_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    devin_session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    prompt: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column(JSON, default=list)
    cycle_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cycle_round: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    project: Mapped[Project] = relationship(back_populates="commits")


class DesignCycle(Base):
    __tablename__ = "design_cycles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    brief: Mapped[str] = mapped_column(Text)
    round: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    # queued|planning|fanning_out|awaiting_agents|scoring|committed|partial|failed|cancelled
    provider: Mapped[str] = mapped_column(String(32), default="devin")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    orchestrator_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    orchestrator_session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    branch: Mapped[str] = mapped_column(String(128), default="main")
    acu_limit: Mapped[int] = mapped_column(Integer, default=20)
    acus_used: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    shortlist: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    agent_runs: Mapped[list[AgentRun]] = relationship(
        back_populates="cycle", order_by="AgentRun.created_at"
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    cycle_id: Mapped[str] = mapped_column(ForeignKey("design_cycles.id"), index=True)
    role: Mapped[str] = mapped_column(String(64))
    task: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(32), default="devin")
    devin_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    devin_session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    playbook_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    # pending|running|blocked|finished|failed|cancelled|timeout
    devin_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    acu_limit: Mapped[int] = mapped_column(Integer, default=5)
    acus: Mapped[float] = mapped_column(Float, default=0.0)
    structured_output: Mapped[dict] = mapped_column(JSON, default=dict)
    log: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cycle: Mapped[DesignCycle] = relationship(back_populates="agent_runs")


class Observation(Base):
    """Append-only research memory used for continuous learning."""

    __tablename__ = "observations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(64), default="system")
    kind: Mapped[str] = mapped_column(String(64))
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MeasuredResult(Base):
    """A real (or explicitly simulated) wet-lab measurement on one commit.

    This is the only place in the system where a number comes from an instrument rather than a
    heuristic, so it is kept separate from `ProteinCommit.scores` and never overwrites them: the
    commit stays immutable and the drift between proxy and measurement stays inspectable.
    """

    __tablename__ = "measured_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    commit_id: Mapped[str] = mapped_column(ForeignKey("protein_commits.id"), index=True)
    assay: Mapped[str] = mapped_column(String(128))
    objective: Mapped[str] = mapped_column(String(64), default="")  # proxy this assay tests
    readout: Mapped[str] = mapped_column(String(128), default="")
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(64), default="")
    higher_is_better: Mapped[bool] = mapped_column(Boolean, default=True)
    outcome: Mapped[str] = mapped_column(String(32), default="unknown")  # hit|miss|inconclusive
    origin: Mapped[str] = mapped_column(String(32), default="measured")  # measured|simulated
    notes: Mapped[str] = mapped_column(Text, default="")
    reported_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchNote(Base):
    """Cached research on one topic, reused across every later version of a project.

    The cache is append-only: a note is never deleted or overwritten, because a claim that informed
    v3 must still be readable when v9 contradicts it. `topic_key` is what makes reuse possible —
    the daemon derives it from what changed (a mutation token, a liability motif, an objective) so
    the same question is never researched twice.
    """

    __tablename__ = "research_notes"
    __table_args__ = (UniqueConstraint("project_id", "topic_key", name="uq_note_project_topic"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    topic_key: Mapped[str] = mapped_column(String(128), index=True)
    topic: Mapped[str] = mapped_column(String(255), default="")
    question: Mapped[str] = mapped_column(Text, default="")
    findings: Mapped[list] = mapped_column(JSON, default=list)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(32), default="local-simulation")
    devin_session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reuse_count: Mapped[int] = mapped_column(Integer, default=0)
    first_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchEvent(Base):
    """One unit of daemon work: something changed, so research ran and drift was re-estimated.

    Rows are created `queued` by whatever changed the project and processed by the daemon tick, so
    a trigger is durable even if no worker is alive. Triggers arriving inside the debounce window
    are merged into the queued row (`triggers`, `coalesced`) rather than dropped or fanned out into
    one session per commit.
    """

    __tablename__ = "research_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")  # queued|running|done|failed
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    # commit|measured_result|cycle|branch_head|upload|manual
    triggers: Mapped[list] = mapped_column(JSON, default=list)
    coalesced: Mapped[int] = mapped_column(Integer, default=0)
    changed: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    findings: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    drift: Mapped[dict] = mapped_column(JSON, default=dict)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    cache_writes: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(32), default="local-simulation")
    devin_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    devin_session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    acus: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    head_commit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResearchDaemonSession(Base):
    """The daemon's own long-lived Devin session per project.

    Each research event sends a message into this session instead of starting a new one, so the
    daemon accumulates context about the lineage the way a scientist following one project would.
    """

    __tablename__ = "research_daemon_sessions"

    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128))
    session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(64), default="running")
    messages_sent: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # acu|cycle
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DrugProgram(Base):
    """A small-molecule discovery program: one target, one indication, one stage ladder."""

    __tablename__ = "drug_programs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    target_name: Mapped[str] = mapped_column(String(255), default="")
    target_sequence: Mapped[str] = mapped_column(Text, default="")
    pocket_residues: Mapped[list] = mapped_column(JSON, default=list)
    indication: Mapped[str] = mapped_column(String(255), default="")
    objective: Mapped[str] = mapped_column(Text, default="")
    current_stage: Mapped[str] = mapped_column(String(64), default="target_assessment")
    stage_cycles: Mapped[int] = mapped_column(Integer, default=0)
    rounds_run: Mapped[int] = mapped_column(Integer, default=0)
    autonomy_level: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(32), default="active")
    # active|awaiting_approval|running|killed|completed
    provider: Mapped[str] = mapped_column(String(32), default="devin")
    acus_used: Mapped[float] = mapped_column(Float, default=0.0)
    assays_ingested: Mapped[int] = mapped_column(Integer, default=0)
    daemon_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    daemon_schedule_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    knowledge_note_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidate_molecule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    backup_molecule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_research_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class MoleculeCommit(Base):
    """Immutable small-molecule commit.

    `id` is the program-scoped content address (program id + molecule graph hash) and
    `molecule_hash` is the program-independent graph hash, so the same chemistry can be found
    across programs without two programs sharing one row of evidence.
    """

    __tablename__ = "molecule_commits"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    molecule_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    program_id: Mapped[str] = mapped_column(ForeignKey("drug_programs.id"), index=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    smiles: Mapped[str] = mapped_column(Text)
    formula: Mapped[str] = mapped_column(String(128), default="")
    scaffold_key: Mapped[str] = mapped_column(String(64), default="", index=True)
    parent_ids: Mapped[list] = mapped_column(JSON, default=list)
    transform: Mapped[str] = mapped_column(Text, default="")
    stage: Mapped[str] = mapped_column(String(64), default="")
    evaluation: Mapped[dict] = mapped_column(JSON, default=dict)
    composite_score: Mapped[float] = mapped_column(Float, default=0.0)
    verdict: Mapped[str] = mapped_column(String(64), default="")
    rationale: Mapped[str] = mapped_column(Text, default="")
    agent_role: Mapped[str] = mapped_column(String(64), default="human")
    provider: Mapped[str] = mapped_column(String(32), default="human")
    devin_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    devin_session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    round_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProgramRound(Base):
    """One orchestrated round of work inside a stage: plan, fan-out, evidence, gate."""

    __tablename__ = "program_rounds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    program_id: Mapped[str] = mapped_column(ForeignKey("drug_programs.id"), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    # queued|planning|fanning_out|awaiting_agents|scoring|gated|failed|cancelled
    provider: Mapped[str] = mapped_column(String(32), default="devin")
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    orchestrator_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    orchestrator_session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    findings: Mapped[dict] = mapped_column(JSON, default=dict)
    gate: Mapped[dict] = mapped_column(JSON, default=dict)
    experiment_plan: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    acus_used: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PharmaAgentRun(Base):
    """A Pharmakon agent run. Same provenance contract as `AgentRun`, keyed to a program round."""

    __tablename__ = "pharma_agent_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    program_id: Mapped[str] = mapped_column(ForeignKey("drug_programs.id"), index=True)
    round_id: Mapped[str] = mapped_column(ForeignKey("program_rounds.id"), index=True)
    role: Mapped[str] = mapped_column(String(64))
    task: Mapped[str] = mapped_column(Text, default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(32), default="devin")
    devin_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    devin_session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    playbook_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    devin_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    acu_limit: Mapped[int] = mapped_column(Integer, default=5)
    acus: Mapped[float] = mapped_column(Float, default=0.0)
    structured_output: Mapped[dict] = mapped_column(JSON, default=dict)
    log: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GateDecision(Base):
    """Append-only record of every stage-gate evaluation and its approval trail."""

    __tablename__ = "gate_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    program_id: Mapped[str] = mapped_column(ForeignKey("drug_programs.id"), index=True)
    round_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))  # go|no_go|recycle|kill
    score: Mapped[float] = mapped_column(Float, default=0.0)
    criteria: Mapped[list] = mapped_column(JSON, default=list)
    blocking_failures: Mapped[list] = mapped_column(JSON, default=list)
    rationale: Mapped[str] = mapped_column(Text, default="")
    recommended_actions: Mapped[list] = mapped_column(JSON, default=list)
    next_stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    autonomy_level: Mapped[int] = mapped_column(Integer, default=2)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_reason: Mapped[str] = mapped_column(Text, default="")
    approval_status: Mapped[str] = mapped_column(String(16), default="not_required")
    # not_required|pending|approved|rejected
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approval_note: Mapped[str] = mapped_column(Text, default="")
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str] = mapped_column(String(32), default="deterministic")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Experiment(Base):
    """A proposed wet-lab experiment: what would falsify the prediction, and what it costs."""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    program_id: Mapped[str] = mapped_column(ForeignKey("drug_programs.id"), index=True)
    round_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stage: Mapped[str] = mapped_column(String(64), default="")
    assay: Mapped[str] = mapped_column(String(128))
    endpoint: Mapped[str] = mapped_column(String(64), default="")
    unit: Mapped[str] = mapped_column(String(32), default="")
    gate_metric: Mapped[str] = mapped_column(String(64), default="")
    molecules: Mapped[list] = mapped_column(JSON, default=list)
    predicted_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    falsification: Mapped[str] = mapped_column(Text, default="")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    turnaround_days: Mapped[int] = mapped_column(Integer, default=0)
    blocking: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="proposed")
    # proposed|approved|rejected|running|complete
    approved_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AssayResult(Base):
    """Measured data ingested from a wet lab. Outranks every in-silico prediction."""

    __tablename__ = "assay_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    program_id: Mapped[str] = mapped_column(ForeignKey("drug_programs.id"), index=True)
    experiment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    molecule_hash: Mapped[str] = mapped_column(String(64), index=True)
    assay: Mapped[str] = mapped_column(String(128), default="")
    metric: Mapped[str] = mapped_column(String(64))
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(32), default="")
    operator: Mapped[str] = mapped_column(String(8), default="=")  # =|<|>
    source: Mapped[str] = mapped_column(String(255), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    ingested_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProgramResearchEvent(Base):
    """Append-only research memory for a program: findings, citations, drift, daemon activity."""

    __tablename__ = "program_research_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    program_id: Mapped[str] = mapped_column(ForeignKey("drug_programs.id"), index=True)
    round_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(64), default="system")
    kind: Mapped[str] = mapped_column(String(64))
    claim: Mapped[str] = mapped_column(Text, default="")
    citation: Mapped[str] = mapped_column(String(512), default="")
    implication: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    provider: Mapped[str] = mapped_column(String(32), default="deterministic")
    devin_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlaybookRef(Base):
    """Devin playbook ids reconciled by role, so we reuse rather than recreate."""

    __tablename__ = "playbook_refs"

    role: Mapped[str] = mapped_column(String(64), primary_key=True)
    playbook_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(255))
    body_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --------------------------------------------------------- research lab loop
# One ResearchProject per project drives the continuous wet-lab loop: an append-only event
# log, a cached paper corpus, residue labels, hypotheses, wet-lab plans and results, the
# per-metric drift models they calibrate, and the debounced daemon queue behind all of it.


class ResearchProject(Base):
    """One research campaign. Every version of the protein hangs off exactly one of these.

    This row is the single source of truth for the continuous loop: it carries the daemon's
    liveness, the merged knowledge version, and the per-metric drift calibration state.
    """

    __tablename__ = "research_projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    question: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="live")  # live|paused
    daemon_status: Mapped[str] = mapped_column(String(32), default="idle")
    # idle|working|queued|degraded (degraded = Devin unreachable, work is queued not faked)
    daemon_detail: Mapped[str] = mapped_column(Text, default="")
    daemon_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    daemon_session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    daemon_provider: Mapped[str] = mapped_column(String(32), default="local-simulation")
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    knowledge_version: Mapped[int] = mapped_column(Integer, default=0)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LabResearchEvent(Base):
    """Append-only research record. Rows are never updated or deleted: cache is never discarded."""

    __tablename__ = "lab_research_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    research_project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    commit_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sequence_no: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(48))
    # version_created|label_added|diff_detected|literature|metrics|wetlab_plan
    # |results|learn|insight|proposal|queued|degraded
    trigger: Mapped[str] = mapped_column(String(64), default="daemon")
    role: Mapped[str] = mapped_column(String(48), default="research-daemon")
    summary: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    skills_used: Mapped[list] = mapped_column(JSON, default=list)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(32), default="local-simulation")
    devin_session_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    devin_session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    parent_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchPaper(Base):
    """Immutable paper cache. Keyed by DOI/title hash so repeats never duplicate or overwrite."""

    __tablename__ = "research_papers"
    __table_args__ = (
        UniqueConstraint("research_project_id", "paper_key", name="uq_paper_project_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    research_project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id"), index=True
    )
    paper_key: Mapped[str] = mapped_column(String(64), index=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(Text, default="")
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue: Mapped[str] = mapped_column(String(255), default="")
    authors: Mapped[list] = mapped_column(JSON, default=list)
    abstract: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source: Mapped[str] = mapped_column(String(48), default="openalex")
    citation_count: Mapped[int] = mapped_column(Integer, default=0)
    relevance: Mapped[float] = mapped_column(Float, default=0.0)
    extracted_metrics: Mapped[list] = mapped_column(JSON, default=list)
    query: Mapped[str] = mapped_column(Text, default="")
    citation: Mapped[str] = mapped_column(Text, default="")
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    commit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MetricDefinition(Base):
    """Canonical metric vocabulary, published from skill.wetlab_metrics so the UI can explain units."""

    __tablename__ = "metric_definitions"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    unit: Mapped[str] = mapped_column(String(32), default="")
    higher_is_better: Mapped[bool] = mapped_column(Boolean, default=True)
    assay: Mapped[str] = mapped_column(Text, default="")
    assay_sd: Mapped[float] = mapped_column(Float, default=0.0)
    sd_kind: Mapped[str] = mapped_column(String(24), default="absolute")
    pass_rule: Mapped[dict] = mapped_column(JSON, default=dict)
    skill: Mapped[str] = mapped_column(String(64), default="skill.wetlab_metrics")
    citations: Mapped[list] = mapped_column(JSON, default=list)


class Hypothesis(Base):
    """A falsifiable prediction attached to a version, resolved by wet-lab results."""

    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    research_project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id"), index=True
    )
    commit_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    statement: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text, default="")
    metric: Mapped[str] = mapped_column(String(64), default="")
    predicted_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    predicted_sd: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="open")  # open|supported|refuted
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ResidueLabel(Base):
    """Scientist annotation on residues. Versioned: refining a label writes a new row."""

    __tablename__ = "residue_labels"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    commit_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="note")
    # active_site|liability|epitope|mutation_intent|note
    name: Mapped[str] = mapped_column(String(128), default="")
    residues: Mapped[list] = mapped_column(JSON, default=list)  # 1-based positions
    note: Mapped[str] = mapped_column(Text, default="")
    parent_label_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    superseded_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WetlabPlan(Base):
    """A minimal, ranked experiment pack proposed for one version."""

    __tablename__ = "wetlab_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    research_project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    commit_id: Mapped[str] = mapped_column(String(64), index=True)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="proposed")  # proposed|complete
    risk: Mapped[dict] = mapped_column(JSON, default=dict)
    predictions: Mapped[dict] = mapped_column(JSON, default=dict)  # metric -> {value, sd, source}
    constructs: Mapped[list] = mapped_column(JSON, default=list)
    assays: Mapped[list] = mapped_column(JSON, default=list)
    controls: Mapped[list] = mapped_column(JSON, default=list)
    thresholds: Mapped[list] = mapped_column(JSON, default=list)
    failure_modes: Mapped[list] = mapped_column(JSON, default=list)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    information_per_usd: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    skills_used: Mapped[list] = mapped_column(JSON, default=list)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(32), default="local-simulation")
    devin_session_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WetlabResult(Base):
    """Measured (or honestly simulated) results committed back onto a version."""

    __tablename__ = "wetlab_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    research_project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    commit_id: Mapped[str] = mapped_column(String(64), index=True)
    plan_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="simulator")
    # simulator|scientist|csv|json — a simulated row is never presented as a real measurement
    construct_label: Mapped[str] = mapped_column(String(128), default="")
    measurements: Mapped[list] = mapped_column(JSON, default=list)  # normalised by skill
    residuals: Mapped[dict] = mapped_column(JSON, default=dict)  # metric -> {predicted, measured, z}
    error_model: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str] = mapped_column(Text, default="")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    artifact_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator: Mapped[str] = mapped_column(String(128), default="")
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DriftModel(Base):
    """Per-metric in-silico vs wet-lab calibration, updated after every result."""

    __tablename__ = "drift_models"
    __table_args__ = (
        UniqueConstraint("research_project_id", "metric", name="uq_drift_project_metric"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    research_project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id"), index=True
    )
    metric: Mapped[str] = mapped_column(String(64), index=True)
    n_observations: Mapped[int] = mapped_column(Integer, default=0)
    bias: Mapped[float] = mapped_column(Float, default=0.0)
    bias_sd: Mapped[float] = mapped_column(Float, default=0.0)
    prior_sd: Mapped[float] = mapped_column(Float, default=0.0)
    slope: Mapped[float] = mapped_column(Float, default=1.0)
    intercept: Mapped[float] = mapped_column(Float, default=0.0)
    residual_sd: Mapped[float] = mapped_column(Float, default=0.0)
    rmse: Mapped[float] = mapped_column(Float, default=0.0)
    history: Mapped[list] = mapped_column(JSON, default=list)
    method: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DaemonTask(Base):
    """Queued daemon work. Debounced by key, never dropped: a superseded task is marked, not deleted."""

    __tablename__ = "daemon_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    research_project_id: Mapped[str] = mapped_column(
        ForeignKey("research_projects.id"), index=True
    )
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str] = mapped_column(String(48))  # version|label|results|manual
    debounce_key: Mapped[str] = mapped_column(String(128), index=True)
    commit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    # queued|coalesced|processing|done|failed
    coalesced_into: Mapped[str | None] = mapped_column(String(64), nullable=True)
    coalesced_count: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
