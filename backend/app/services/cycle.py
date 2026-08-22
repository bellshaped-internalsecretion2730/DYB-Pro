"""The design cycle engine: plan -> fan out -> poll -> score -> rank -> commit -> wet-lab pack.

This is the loop the scientist sees. It is provider-agnostic (real Devin, or the explicitly
labelled local simulation) and every step writes an append-only `Observation` so the next cycle can
learn from it.
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.devin import prompts as promptlib
from app.devin.runner import PROVIDER_DEVIN, AgentSupervisor, LaunchSpec
from app.devin.schemas import PLAN_SCHEMA, ROLES, schema_for
from app.models import AgentRun, Artifact, DesignCycle, Observation, Project, ProteinCommit, utcnow
from app.services import analysis, calibration, learning, ranking, research, wetlab
from app.services.evaluation import Candidate, Evaluation, evaluate_all
from app.storage import store
from app.toolkit import developability as dev
from app.toolkit import docking as docklib
from app.toolkit import folding
from app.toolkit import structure as structlib
from app.versioning import commit_design, get_branch

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 24


# ------------------------------------------------------------------- evidence


def load_structure(db: Session, commit: ProteinCommit) -> structlib.Structure | None:
    if not commit.structure_key:
        return None
    artifact = db.scalar(select(Artifact).where(Artifact.key == commit.structure_key))
    backend = artifact.backend if artifact else "s3"
    try:
        raw = store.get(commit.structure_key, backend=backend)
    except OSError as exc:
        logger.warning("structure %s unavailable: %s", commit.structure_key, exc)
        return None
    return structlib.load_structure(
        raw.decode("utf-8", "replace"), commit.structure_key, commit.label or "parent"
    )


def target_structure(project: Project) -> structlib.Structure | None:
    if not project.target_sequence:
        return None
    return folding.fold_sequence(project.target_sequence, name=project.target_name or "target")


def build_evidence(
    sequence: str,
    structure: structlib.Structure | None,
    target: structlib.Structure | None,
) -> dict:
    """Deterministic toolkit evidence shown to every agent."""
    model = structure or folding.fold_sequence(sequence, name="parent")
    profile = dev.profile(sequence, structure=model)
    geometry = structlib.summary(model)
    evidence: dict = {
        "descriptors": profile["descriptors"],
        "developability": {
            "ddg": profile["ddg"],
            "solubility": profile["solubility"],
            "aggregation": profile["aggregation"],
            "immunogenicity": profile["immunogenicity"],
            "liabilities": profile["liabilities"],
        },
        "structure": {
            **geometry,
            "source": model.source,
            "relative_exposure": [round(v, 3) for v in structlib.relative_exposure(model)],
            "is_model": structlib.is_model(model),
            "geometry_usable": structlib.geometry_usable(model),
        },
        "notes": [
            "All values are deterministic in-silico proxies from DYB Pro's open toolkit.",
            "Coarse models are not experimental structures; burial, contacts and docking read off "
            "a generated CA trace describe the model, not the protein.",
            "No score here is calibrated against measurements: use them to order candidates, not "
            "to predict assay outcomes.",
        ],
    }
    if target is not None:
        result = docklib.dock(model, target)
        evidence["docking"] = {
            **result.as_dict(),
            "method": "coarse rigid-body CA-level proxy; rank ordering only",
        }
    return evidence


# ------------------------------------------------------- structured handoff


def _candidates_from_output(
    role: str, output: dict, parent_sequence: str, source_run_id: str | None = None
) -> list[Candidate]:
    out: list[Candidate] = []
    raw = list(output.get("candidates") or [])
    if role == "literature":
        for rec in output.get("recommended_mutations") or []:
            token = str(rec.get("mutation", "")).strip()
            if not token:
                continue
            raw.append(
                {
                    "label": f"lit-{token}",
                    "mutations": [token],
                    "rationale": rec.get("rationale", ""),
                    "citations": [rec.get("citation")] if rec.get("citation") else [],
                }
            )
    for rec in raw:
        label = str(rec.get("label") or "").strip() or f"{role}-{len(out) + 1}"
        out.append(
            Candidate(
                label=f"{label}"[:60],
                parent_sequence=parent_sequence,
                mutations=[str(m).strip().upper() for m in (rec.get("mutations") or [])],
                sequence=rec.get("sequence"),
                rationale=str(rec.get("rationale") or "")[:2000],
                agent_role=role,
                citations=[str(c) for c in (rec.get("citations") or [])],
                source_run_id=source_run_id,
            )
        )
    return out


def _dedupe_candidates(candidates: list[Candidate]) -> list[Candidate]:
    """Drop duplicate proposals, keyed on the *whole* design.

    Truncating the key to the first 64 residues silently discarded distinct designs from different
    agents whenever they shared a prefix, which is the normal case for point mutants of one parent.
    """
    seen: set[str] = set()
    unique: list[Candidate] = []
    for cand in candidates:
        key = ",".join(sorted(cand.mutations)) or (cand.sequence or "")
        if not key or key in seen:
            continue
        seen.add(key)
        label = cand.label
        suffix = 2
        while any(c.label == label for c in unique):
            label = f"{cand.label}-{suffix}"
            suffix += 1
        cand.label = label
        unique.append(cand)
    return unique[:MAX_CANDIDATES]


def _apply_agent_ordering(ranked: list, ordering: list) -> list:
    """Re-order candidates according to the ranking agent, within filter tiers.

    The agent was previously asked for an ordering and only its free-text reasons were kept, so
    its judgement never reached the shortlist. Candidates it did not mention keep their
    deterministic order behind the ones it did, and designs failing hard filters stay last: an
    agent cannot promote a design past a developability filter.
    """
    if not ordering:
        return ranked
    priority: dict[str, int] = {}
    for index, item in enumerate(ordering):
        label = str(item.get("label") or "").strip()
        if label and label not in priority:
            priority[label] = index
    if not priority:
        return ranked
    fallback = len(priority)
    reordered = sorted(
        ranked,
        key=lambda c: (
            0 if c.passed_filters else 1,
            priority.get(c.label, fallback),
            c.rank,
        ),
    )
    for position, cand in enumerate(reordered, start=1):
        cand.rank = position
        cand.payload["agent_ordered"] = cand.label in priority
    for i, cand in enumerate(reordered[:-1]):
        cand.why_not_next = ranking.why_not(cand, reordered[i + 1])
    if reordered:
        reordered[-1].why_not_next = "lowest-ranked candidate in this cycle"
    return reordered


def _observe(
    db: Session,
    cycle: DesignCycle,
    kind: str,
    summary: str,
    payload: dict | None = None,
    role: str = "system",
    agent_run_id: str | None = None,
) -> None:
    db.add(
        Observation(
            project_id=cycle.project_id,
            cycle_id=cycle.id,
            agent_run_id=agent_run_id,
            role=role,
            kind=kind,
            summary=summary[:4000],
            payload=payload or {},
        )
    )
    db.flush()


def _store_structure(project_id: str, label: str, pdb: str, db: Session) -> str | None:
    if not pdb:
        return None
    key = f"{project_id}/structures/{label.replace('/', '_')}.pdb"
    stored = store.put(key, pdb.encode(), content_type="chemical/x-pdb")
    db.add(
        Artifact(
            project_id=project_id,
            kind="structure",
            filename=f"{label}.pdb",
            key=stored.key,
            backend=stored.backend,
            sha256=stored.sha256,
            size=stored.size,
            content_type="chemical/x-pdb",
        )
    )
    db.flush()
    return stored.key


# ------------------------------------------------------------------ the cycle


def run_cycle(db: Session, cycle_id: str, sleep=time.sleep) -> DesignCycle:
    settings = get_settings()
    cycle = db.get(DesignCycle, cycle_id)
    if cycle is None:
        raise KeyError(f"unknown cycle {cycle_id}")
    if cycle.status == "cancelled":
        return cycle
    project = db.get(Project, cycle.project_id)

    branch = get_branch(db, project.id, cycle.branch, create=True)
    if not branch.head_commit_id:
        cycle.status = "failed"
        cycle.error = "branch has no commits: upload a sequence before running a cycle"
        cycle.finished_at = utcnow()
        db.commit()
        return cycle
    parent = db.get(ProteinCommit, branch.head_commit_id)
    previous_head = branch.head_commit_id

    parent_structure = load_structure(db, parent)
    target = target_structure(project)
    evidence = build_evidence(parent.sequence, parent_structure, target)
    digest = learning.history_digest(db, project)
    history_text = learning.digest_to_prompt(digest)
    exclusions = learning.exclusions_from_digest(digest)
    shortlist_size = 5

    parent_info = {
        "label": parent.label or "parent",
        "commit_id": parent.id[:12],
        "length": len(parent.sequence),
        "sequence": parent.sequence,
    }

    supervisor = AgentSupervisor(db, cycle, settings)
    cycle.provider = supervisor.provider
    _observe(
        db,
        cycle,
        "cycle_started",
        f"Cycle {cycle.round} started on parent {parent.label} via provider {supervisor.provider}",
        {"provider": supervisor.provider, "parent_commit": parent.id, "evidence": evidence},
    )

    try:
        # 1) plan --------------------------------------------------------------
        cycle.status = "planning"
        db.commit()
        plan_prompt = promptlib.orchestrator_prompt(
            brief=cycle.brief,
            project_goal=project.goal,
            parent=parent_info,
            evidence=evidence,
            history=history_text,
            available_roles=list(ROLES),
            shortlist_size=shortlist_size,
        )
        orchestrator = supervisor.launch(
            LaunchSpec(
                role="orchestrator",
                task="plan the design cycle and fan out to child agents",
                prompt=plan_prompt,
                schema=PLAN_SCHEMA,
                acu_limit=settings.devin_orchestrator_acu_limit,
                title=f"DYB Pro orchestrator — {project.name} round {cycle.round}",
            ),
            simulation_kwargs={
                "brief": cycle.brief,
                "evidence": evidence,
                "shortlist_size": shortlist_size,
            },
        )
        cycle.orchestrator_session_id = orchestrator.devin_session_id
        cycle.orchestrator_session_url = orchestrator.devin_session_url
        db.commit()

        supervisor.wait_for([orchestrator], sleep=sleep)
        plan = orchestrator.structured_output or {}
        if not plan.get("agents"):
            plan = {
                "strategy": (
                    "Orchestrator returned no usable plan; falling back to the default fan-out of "
                    "sequence, structure and literature agents."
                ),
                "agents": [
                    {"role": r, "task": f"Propose designs from the {r} perspective."}
                    for r in ("sequence", "structure", "literature")
                ],
                "shortlist_size": shortlist_size,
            }
            _observe(db, cycle, "plan_fallback", plan["strategy"], {"raw": orchestrator.structured_output})
        cycle.plan = plan
        shortlist_size = int(plan.get("shortlist_size") or shortlist_size)
        _observe(
            db,
            cycle,
            "plan",
            plan.get("strategy", "")[:2000],
            {"plan": plan, "session": orchestrator.devin_session_url},
            role="orchestrator",
            agent_run_id=orchestrator.id,
        )

        # 2) fan out -----------------------------------------------------------
        cycle.status = "fanning_out"
        db.commit()
        child_specs = []
        for agent in plan.get("agents", []):
            role = str(agent.get("role", "")).strip()
            if role not in ROLES or role == "ranking":
                continue
            child_specs.append(agent)
        if not child_specs:
            child_specs = [{"role": "sequence", "task": "Propose sequence-level designs."}]

        runs: list[AgentRun] = []
        for agent in child_specs[:5]:
            role = agent["role"]
            prompt = promptlib.child_prompt(
                role=role,
                task=str(agent.get("task", ""))[:4000],
                brief=cycle.brief,
                strategy=plan.get("strategy", ""),
                parent=parent_info,
                evidence=evidence,
                history=history_text,
                focus_regions=[int(r) for r in (agent.get("focus_regions") or []) if str(r).isdigit()],
                must_avoid=[str(x) for x in (agent.get("must_avoid") or [])],
            )
            runs.append(
                supervisor.launch(
                    LaunchSpec(
                        role=role,
                        task=str(agent.get("task", ""))[:4000],
                        prompt=prompt,
                        schema=schema_for(role),
                        acu_limit=int(agent.get("acu_limit") or settings.devin_child_acu_limit),
                        title=f"DYB Pro {role} agent — {project.name} r{cycle.round}",
                    ),
                    parent_session_id=cycle.orchestrator_session_id,
                    simulation_kwargs={
                        "sequence": parent.sequence,
                        "evidence": evidence,
                        "exclusions": exclusions,
                    },
                )
            )
        cycle.status = "awaiting_agents"
        db.commit()

        # 3) poll --------------------------------------------------------------
        supervisor.wait_for(runs, sleep=sleep)
        candidates: list[Candidate] = []
        for run in runs:
            output = run.structured_output or {}
            found = _candidates_from_output(
                run.role, output, parent.sequence, source_run_id=run.id
            )
            candidates.extend(found)
            _observe(
                db,
                cycle,
                "agent_result" if run.status == "finished" else f"agent_{run.status}",
                (output.get("analysis") or run.error or "no analysis returned")[:2000],
                {
                    "role": run.role,
                    "status": run.status,
                    "session": run.devin_session_url,
                    "acus": run.acus,
                    "candidates": len(found),
                    "output": output,
                },
                role=run.role,
                agent_run_id=run.id,
            )
            for question in output.get("open_questions") or []:
                _observe(
                    db,
                    cycle,
                    "open_question",
                    str(question)[:500],
                    {},
                    role=run.role,
                    agent_run_id=run.id,
                )

        candidates = _dedupe_candidates(candidates)
        if not candidates:
            cycle.status = "failed"
            cycle.error = "no valid candidates were produced by the agent swarm"
            cycle.finished_at = utcnow()
            cycle.acus_used = sum(r.acus for r in runs) + orchestrator.acus
            db.commit()
            return cycle

        # 4) score + rank ------------------------------------------------------
        cycle.status = "scoring"
        db.commit()
        evaluations = evaluate_all(
            candidates, target_structure=target, parent_structure=parent_structure
        )
        by_label: dict[str, Evaluation] = {e.label: e for e in evaluations}
        candidate_by_label = {c.label: c for c in candidates}
        runs_by_id = {r.id: r for r in runs}
        ranked = ranking.rank(evaluations, exclusions=exclusions)

        ranking_run: AgentRun | None = None
        if any(a.get("role") == "ranking" for a in plan.get("agents", [])):
            summary_rows = [
                {
                    "label": r.label,
                    "mutations": [m.get("mutation") for m in by_label[r.label].mutations],
                    "composite_score": r.composite,
                    "confidence": r.confidence,
                    "scores": r.scores,
                    "passed_filters": r.passed_filters,
                    "failed_filters": r.failed_filters,
                    "rationale": by_label[r.label].rationale,
                }
                for r in ranked
            ]
            ranking_run = supervisor.launch(
                LaunchSpec(
                    role="ranking",
                    task="triage the pooled candidates into an orthogonal wet-lab shortlist",
                    prompt=promptlib.ranking_prompt(
                        brief=cycle.brief,
                        strategy=plan.get("strategy", ""),
                        candidates=summary_rows,
                        history=history_text,
                        shortlist_size=shortlist_size,
                    ),
                    schema=schema_for("ranking"),
                    acu_limit=settings.devin_child_acu_limit,
                    title=f"DYB Pro ranking agent — {project.name} r{cycle.round}",
                ),
                parent_session_id=cycle.orchestrator_session_id,
                simulation_kwargs={"candidates": summary_rows, "shortlist_size": shortlist_size},
            )
            supervisor.wait_for([ranking_run], sleep=sleep)
            triage = ranking_run.structured_output or {}
            agent_reasons = {
                str(item.get("label")): str(item.get("reason", ""))
                for item in triage.get("ordering") or []
            }
            for cand in ranked:
                if cand.label in agent_reasons:
                    cand.payload["agent_reason"] = agent_reasons[cand.label]
            ranked = _apply_agent_ordering(ranked, triage.get("ordering") or [])
            for excl in triage.get("exclusions") or []:
                label = str(excl.get("target"))
                for cand in ranked:
                    if cand.label == label and cand.excluded_reason is None:
                        cand.excluded_reason = f"ranking agent: {excl.get('reason')}"
            shortlist_size = int(triage.get("shortlist_size") or shortlist_size)
            runs.append(ranking_run)
            _observe(
                db,
                cycle,
                "ranking",
                (triage.get("analysis") or "ranking agent returned no analysis")[:2000],
                {"triage": triage, "session": ranking_run.devin_session_url},
                role="ranking",
                agent_run_id=ranking_run.id,
            )

        # 5) commit ------------------------------------------------------------
        commits: list[ProteinCommit] = []
        for cand in ranked:
            ev = by_label[cand.label]
            if not ev.sequence:
                _observe(
                    db,
                    cycle,
                    "candidate_rejected",
                    f"{cand.label}: {ev.rationale}",
                    {"label": cand.label},
                )
                continue
            source_candidate = candidate_by_label.get(cand.label)
            role = source_candidate.agent_role if source_candidate else "sequence"
            # Provenance must point at the run that produced *this* candidate: picking the first
            # run with a matching role attributed the design to the wrong session whenever two
            # agents shared a role.
            source_run = (
                runs_by_id.get(source_candidate.source_run_id) if source_candidate else None
            )
            mutation_summary = "+".join(m.get("mutation") for m in ev.mutations) or "no change"
            structure_key = _store_structure(project.id, f"{cycle.id[:8]}-{cand.label}", ev.structure_pdb, db)
            commit = commit_design(
                db,
                project_id=project.id,
                sequence=ev.sequence,
                message=f"round {cycle.round}: {cand.label} ({mutation_summary})",
                label=cand.label,
                parent_ids=[parent.id],
                branch=cycle.branch,
                mutations=ev.mutations,
                scores={**ev.scores, "composite_score": cand.composite, "rank": cand.rank},
                uncertainty={**ev.uncertainty, "confidence": cand.confidence},
                filters=ev.filters,
                rationale=f"{cand.why} | {ev.rationale}"[:4000],
                agent_role=role,
                provider=supervisor.provider,
                devin_session_id=source_run.devin_session_id if source_run else None,
                devin_session_url=source_run.devin_session_url if source_run else None,
                prompt=(source_run.prompt if source_run else "")[:20000],
                citations=ev.citations,
                structure_key=structure_key,
                structure_source=ev.structure_source,
                structure_content=ev.structure_pdb,
                cycle_id=cycle.id,
                cycle_round=cycle.round,
            )
            commits.append(commit)
            if not cand.passed_filters:
                _observe(
                    db,
                    cycle,
                    "filter_failure",
                    f"{cand.label} failed {', '.join(cand.failed_filters)}",
                    {"commit": commit.id, "mutations": [m.get("mutation") for m in ev.mutations]},
                    role=role,
                )

        # The branch head is the best passing design of this cycle, or the previous head when
        # nothing passed: committing a design moves the head, so without this restore a cycle in
        # which every candidate failed its filters would leave a rejected design as the head and
        # the next cycle would build on it.
        best = next((c for c in ranked if c.passed_filters), None)
        head_commit = (
            next((c for c in commits if c.label == best.label), None) if best else None
        )
        branch.head_commit_id = head_commit.id if head_commit else previous_head
        db.flush()
        if head_commit is None:
            _observe(
                db,
                cycle,
                "branch_head_unchanged",
                "no candidate passed the hard filters; branch head left on the parent commit",
                {"branch": branch.name, "head_commit": previous_head},
            )

        # 6) wet-lab pack ------------------------------------------------------
        pack = wetlab.build_pack(
            ranked,
            by_label,
            parent.sequence,
            project.name,
            top_n=shortlist_size,
            total_candidate_pool=len(evaluations),
            calibration=calibration.project_calibration(db, project.id),
        )
        narrative = analysis.narrate_cycle(
            {
                "brief": cycle.brief,
                "strategy": plan.get("strategy"),
                "ranked": [r.as_dict() for r in ranked[:8]],
                "risks": pack.get("risks"),
                "economics": pack.get("economics"),
            }
        )
        cycle.shortlist = {
            "pack": pack,
            "ranked": [r.as_dict() for r in ranked],
            "narrative": narrative,
            "commits": [{"label": c.label, "commit": c.id} for c in commits],
        }
        cycle.summary = narrative.get("headline", "")[:2000]
        cycle.acus_used = round(orchestrator.acus + sum(r.acus for r in runs), 3)
        passing = [r for r in ranked if r.passed_filters]
        cycle.status = "committed" if passing else "partial"
        cycle.finished_at = utcnow()
        _observe(
            db,
            cycle,
            "cycle_finished",
            cycle.summary or "cycle finished",
            {
                "commits": len(commits),
                "passing": len(passing),
                "economics": pack.get("economics"),
                "narrative": narrative,
            },
        )
        # The daemon is told what happened rather than run here: a cycle that commits thirteen
        # designs must produce one coalesced research event, on the daemon's own worker.
        research.enqueue(
            db,
            project.id,
            "cycle",
            ref=cycle.id,
            detail=f"round {cycle.round}: {len(commits)} commit(s), {len(passing)} passing",
        )
        db.commit()
        return cycle

    except Exception as exc:
        logger.exception("cycle %s failed", cycle_id)
        cycle.status = "failed"
        cycle.error = f"{type(exc).__name__}: {exc}"[:2000]
        cycle.finished_at = utcnow()
        _observe(db, cycle, "cycle_failed", cycle.error or "unknown error")
        db.commit()
        return cycle
    finally:
        if supervisor.provider == PROVIDER_DEVIN:
            supervisor.close()
