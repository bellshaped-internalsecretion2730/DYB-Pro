"""Seed a *complete* research campaign so the 90-second demo path has real history behind it.

What this builds, using exactly the same code paths a scientist's clicks would use:

* three protein versions on the demo project (uploaded wild type + two designed versions);
* a scientist label on the wild type, so the label pane is not empty;
* one daemon research pass per version (literature -> metrics -> merged path -> plan -> proposal);
* one simulated wet-lab result per version, fed back through the learn pass.

The simulator is given a documented systematic offset (``DEMO_LAB_BIAS``) so version 1 shows the
in-silico optimism a real campaign starts with. Nothing else is fabricated: the calibration then
removes that offset, which is why the drift table genuinely shrinks from v1 to v3 instead of being
hard-coded to shrink. The offset is written into every simulated row's ``error_model``.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Project, ProteinCommit, ResearchProject, User, utcnow
from app.services import cycle as cycle_svc
from app.services import daemon as daemon_svc
from app.services import lab_research as research_svc
from app.services import labels as label_svc
from app.services import wetlab_loop
from app.services.evaluation import Candidate, evaluate_candidate
from app.versioning import commit_design

logger = logging.getLogger(__name__)

# In-silico optimism the virtual lab exhibits, in each metric's canonical unit. Documented in
# DEMO.md and recorded on every simulated result row.
DEMO_LAB_BIAS = {
    "melting_temperature": -3.4,
    "soluble_fraction": -8.5,
    "aggregation_hmw": 2.2,
    "purification_recovery": -6.0,
    "activity": -7.0,
}

# Two designed versions on top of the uploaded GB1 wild type. Positions are checked against the
# parent sequence before use, so a different root sequence simply skips the round instead of
# committing a bogus mutation.
DESIGN_ROUNDS = [
    {
        "label": "v2-core-packing",
        "mutations": ["T18V"],
        "rationale": "Fill the hydrophobic core cavity next to the beta-sheet face; core packing "
        "is the cheapest stability lever that does not touch the Fc-binding surface.",
        "agent_role": "structure",
    },
    {
        "label": "v3-core-packing-stacked",
        "mutations": ["A24V"],
        "rationale": "Stack a second buried substitution on top of v2's core packing gain, the "
        "mechanism the v2 wet-lab result supported. Mutations are relative to the parent version.",
        "agent_role": "sequence",
    },
]

DEMO_LABEL = {
    "kind": "liability",
    "name": "hydrophobic patch (aggregation risk)",
    "residues": [5, 6, 7],
    "note": "Contiguous Leu/Ile patch; watch SEC HMW% before scaling up expression.",
}


def _mutations_apply(sequence: str, mutations: list[str]) -> bool:
    for token in mutations:
        wt, pos, _mt = token[0], int(token[1:-1]), token[-1]
        if pos < 1 or pos > len(sequence) or sequence[pos - 1] != wt:
            return False
    return True


def _commit_round(
    db: Session,
    project: Project,
    parent: ProteinCommit,
    spec: dict,
    round_index: int,
) -> ProteinCommit | None:
    """Score a designed variant with the real toolkit and commit it onto the lineage."""
    if not _mutations_apply(parent.sequence, spec["mutations"]):
        logger.warning("demo round %s does not apply to %s", spec["label"], parent.label)
        return None
    target = cycle_svc.target_structure(project)
    parent_structure = cycle_svc.load_structure(db, parent)
    evaluation = evaluate_candidate(
        Candidate(
            label=spec["label"],
            parent_sequence=parent.sequence,
            mutations=list(spec["mutations"]),
            rationale=spec["rationale"],
            agent_role=spec["agent_role"],
        ),
        target_structure=target,
        parent_structure=parent_structure,
    )
    structure_key = cycle_svc.store_structure(
        project.id, f"demo-{spec['label']}", evaluation.structure_pdb, db
    )
    return commit_design(
        db,
        project_id=project.id,
        sequence=evaluation.sequence,
        message=f"seeded demo round {round_index}: {spec['label']}",
        label=spec["label"],
        parent_ids=[parent.id],
        mutations=evaluation.mutations,
        scores=evaluation.scores,
        uncertainty=evaluation.uncertainty,
        filters=evaluation.filters,
        rationale=spec["rationale"],
        agent_role=spec["agent_role"],
        provider="local-simulation",
        prompt="seeded demo campaign (no agent session; deterministic toolkit scoring)",
        citations=evaluation.citations,
        structure_key=structure_key,
        structure_source=evaluation.structure_source,
        cycle_round=round_index,
    )


def _drain(db: Session, rp: ResearchProject, limit: int = 4) -> list[dict]:
    """Run whatever the daemon has queued, ignoring the debounce window."""
    return daemon_svc.process_pending(
        db, rp, limit=limit, now=utcnow() + timedelta(seconds=3600)
    )


def _measure(db: Session, rp: ResearchProject, commit: ProteinCommit, seed: int) -> dict:
    """Simulate one wet-lab round on the newest plan for this version and learn from it."""
    plan = next(iter(research_svc.plans_for(db, rp, commit.id)), None)
    if plan is None:
        project = db.get(Project, rp.project_id)
        plan = wetlab_loop.build_plan(db, rp, project, commit)
    result = wetlab_loop.simulate_results(
        db, rp, plan, seed=seed, systematic_bias=DEMO_LAB_BIAS
    )
    project = db.get(Project, rp.project_id)
    daemon_svc.notify(
        db,
        project,
        "results_registered",
        commit_id=commit.id,
        payload={"result_id": result.id, "source": result.source, "seeded": True},
        dispatch=False,
    )
    _drain(db, rp)
    residuals = {
        m: r.get("residual") for m, r in (result.residuals or {}).items() if r.get("residual") is not None
    }
    return {"result_id": result.id, "plan_id": plan.id, "residuals": residuals}


def _demo_scientist_id(db: Session) -> str | None:
    """``ResidueLabel.created_by`` is a users.id foreign key, so a display name will not do."""
    user = db.scalar(select(User).where(User.role == "scientist").order_by(User.created_at))
    return user.id if user else None


def seed_campaign(db: Session, project: Project) -> dict:
    """Idempotent: a campaign that already has simulated results is left untouched."""
    rp = research_svc.ensure_research_project(db, project)
    if research_svc.results_for(db, rp):
        return {"campaign_id": rp.id, "seeded": False, "reason": "already seeded"}

    path = research_svc.version_path(db, rp)
    if not path:
        return {"campaign_id": rp.id, "seeded": False, "reason": "project has no root version"}
    root = path[0]

    label_svc.create_label(
        db,
        root,
        kind=DEMO_LABEL["kind"],
        name=DEMO_LABEL["name"],
        residues=list(DEMO_LABEL["residues"]),
        note=DEMO_LABEL["note"],
        created_by=_demo_scientist_id(db),
    )

    versions = [root]
    daemon_svc.notify(
        db, project, "version_created", commit_id=root.id, payload={"seeded": True}, dispatch=False
    )
    _drain(db, rp)
    rounds = [_measure(db, rp, root, seed=101)]

    parent = root
    for index, spec in enumerate(DESIGN_ROUNDS, start=1):
        commit = _commit_round(db, project, parent, spec, index)
        if commit is None:
            continue
        versions.append(commit)
        daemon_svc.notify(
            db,
            project,
            "version_created",
            commit_id=commit.id,
            payload={"seeded": True, "round": index},
            dispatch=False,
        )
        _drain(db, rp)
        rounds.append(_measure(db, rp, commit, seed=101 + index))
        parent = commit

    # A final pass on the head so the visible proposal is computed from the full, measured history.
    daemon_svc.notify(
        db, project, "manual_refresh", commit_id=parent.id, payload={"seeded": True}, dispatch=False
    )
    _drain(db, rp)
    db.flush()

    drift = research_svc.drift_models(db, rp)
    return {
        "campaign_id": rp.id,
        "seeded": True,
        "versions": [{"id": c.id, "label": c.label} for c in versions],
        "rounds": rounds,
        "papers": len(research_svc.papers(db, rp, 200)),
        "drift_metrics": {d.metric: {"n": d.n_observations, "bias": d.bias, "rmse": d.rmse}
                          for d in drift},
        "lab_bias": DEMO_LAB_BIAS,
    }
