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


class ProblemSpec(Base):
    """Append-only, versioned machine-checkable objectives for a project."""

    __tablename__ = "problem_specs"
    __table_args__ = (
        UniqueConstraint("project_id", "version", name="uq_spec_project_version"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="active")
    objectives: Mapped[list] = mapped_column(JSON, default=list)
    hard_constraints: Mapped[list] = mapped_column(JSON, default=list)
    deciding_objective: Mapped[str] = mapped_column(String(64))
    target_readout: Mapped[str] = mapped_column(String(64))
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    superseded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


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


class AutonomyDecision(Base):
    """Append-only record of a machine decision and the basis that produced it."""

    __tablename__ = "autonomy_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    cycle_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    commit_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    step: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(255))
    actor: Mapped[str] = mapped_column(String(64))
    autonomy: Mapped[str] = mapped_column(String(16))
    basis: Mapped[dict] = mapped_column(JSON, default=dict)
    reversible: Mapped[bool] = mapped_column(Boolean)
    confidence_basis: Mapped[str] = mapped_column(Text)
    overridden_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    overridden_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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


class PlaybookRef(Base):
    """Devin playbook ids reconciled by role, so we reuse rather than recreate."""

    __tablename__ = "playbook_refs"

    role: Mapped[str] = mapped_column(String(64), primary_key=True)
    playbook_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(255))
    body_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
