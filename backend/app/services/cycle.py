"""The design cycle engine: plan -> fan out -> poll -> score -> rank -> commit -> wet-lab pack.

This is the loop the scientist sees. It is provider-agnostic (real Devin, or the explicitly
labelled local simulation) and every step writes an append-only `Observation` so the next cycle can
learn from it.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.compute.nim import NIMClient, NIMError
from app.compute.workflow import (
    DEFAULT_POLICIES,
    WorkflowError,
    input_hash,
    normalize_policies,
    store_compute_artifact,
    validate_required_providers,
)
from app.config import get_settings
from app.devin import prompts as promptlib
from app.devin.runner import PROVIDER_DEVIN, AgentSupervisor, LaunchSpec
from app.devin.schemas import PLAN_SCHEMA, ROLES, schema_for
from app.models import (
    AgentRun,
    Artifact,
    DesignCycle,
    Observation,
    ProblemSpec,
    Project,
    ProteinCommit,
    utcnow,
)
from app.services import (
    analysis,
    autonomy,
    calibration,
    economics,
    learning,
    problem,
    ranking,
    research,
    wetlab,
)
from app.services.evaluation import Candidate, Evaluation, evaluate_all
from app.storage import store
from app.toolkit import developability as dev
from app.toolkit import docking as docklib
from app.toolkit import folding
from app.toolkit import sequence as seqlib
from app.toolkit import structure as structlib
from app.versioning import commit_design, get_branch

logger = logging.getLogger(__name__)

MAX_CANDIDATES = 24

ALPHAFOLD_CITATION = "https://docs.nvidia.com/nim/bionemo/alphafold2/latest/endpoints.html"
PROTEINMPNN_CITATION = "https://docs.nvidia.com/nim/bionemo/proteinmpnn/latest/endpoints.html"


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


def store_structure(project_id: str, label: str, pdb: str, db: Session) -> str | None:
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


# ----------------------------------------------------------- compute workflow


def _workflow_state(cycle: DesignCycle) -> dict[str, Any]:
    raw = dict((cycle.plan or {}).get("workflow") or {})
    return {
        "policies": normalize_policies(raw.get("policies") or DEFAULT_POLICIES),
        "runs": list(raw.get("runs") or []),
    }


def _save_workflow(cycle: DesignCycle, workflow: dict[str, Any], **plan_fields: Any) -> None:
    # JSON columns do not reliably notice nested mutation; always assign a fresh object.
    cycle.plan = {**(cycle.plan or {}), **plan_fields, "workflow": workflow}


def _record_compute(
    db: Session,
    cycle: DesignCycle,
    workflow: dict[str, Any],
    run: dict[str, Any],
) -> None:
    workflow["runs"].append(run)
    _save_workflow(cycle, workflow)
    _observe(
        db,
        cycle,
        "compute_" + run["status"],
        f"{run['tool']} ({run.get('target', 'working')}): " f"{run.get('reason') or run['status']}",
        {"compute": run},
        role="compute",
    )


def _nim_client(settings) -> NIMClient:
    return NIMClient(
        alphafold_url=settings.alphafold_api_url,
        alphafold_key=settings.alphafold_api_key,
        alphafold_timeout=settings.alphafold_timeout_seconds,
        proteinmpnn_url=settings.proteinmpnn_api_url,
        proteinmpnn_key=settings.proteinmpnn_api_key,
        proteinmpnn_timeout=settings.proteinmpnn_timeout_seconds,
    )


def _run_alphafold(
    db: Session,
    cycle: DesignCycle,
    project: Project,
    parent: ProteinCommit,
    parent_structure: structlib.Structure | None,
    workflow: dict[str, Any],
    settings,
) -> tuple[structlib.Structure | None, structlib.Structure | None]:
    """Return the best parent and target structures available for this cycle."""
    policy = workflow["policies"]["alphafold"]
    target = target_structure(project)
    if policy == "off":
        _record_compute(
            db,
            cycle,
            workflow,
            {"tool": "alphafold", "status": "skipped", "reason": "disabled by scientist"},
        )
        return parent_structure, target

    credible_parent = bool(
        parent_structure is not None and not (parent.structure_source or "").startswith("model:coarse")
    )
    requests: list[tuple[str, str]] = []
    if not credible_parent:
        requests.append(("working", parent.sequence))
    else:
        _record_compute(
            db,
            cycle,
            workflow,
            {
                "tool": "alphafold",
                "target": "working",
                "status": "reused",
                "reason": f"credible structure already available ({parent.structure_source})",
                "artifact_key": parent.structure_key,
            },
        )
    if project.target_sequence:
        requests.append(("target", project.target_sequence))

    if not requests:
        _record_compute(
            db,
            cycle,
            workflow,
            {"tool": "alphafold", "status": "skipped", "reason": "no sequence needs folding"},
        )
        return parent_structure, target
    if not settings.alphafold_api_url:
        run = {
            "tool": "alphafold",
            "status": "skipped" if policy == "auto" else "failed",
            "reason": "ALPHAFOLD_API_URL is not configured",
        }
        _record_compute(db, cycle, workflow, run)
        if policy == "required":
            raise WorkflowError(run["reason"])
        return parent_structure, target

    client = _nim_client(settings)
    for target_name, sequence in requests:
        base_run: dict[str, Any] = {
            "tool": "alphafold",
            "target": target_name,
            "provider": "nvidia-bionemo-nim",
            "model": settings.alphafold_model_version,
            "input_sha256": input_hash(sequence),
            "parameters": {
                "databases": ["uniref90", "mgnify", "small_bfd"],
                "algorithm": "mmseqs2",
                "relax_prediction": True,
            },
        }
        try:
            prediction = client.predict_structure(sequence)
            parsed = structlib.load_structure(
                prediction.pdb,
                f"alphafold-{target_name}.pdb",
                f"alphafold-{target_name}",
            )
            artifact = store_compute_artifact(
                db,
                project_id=project.id,
                cycle_id=cycle.id,
                tool="alphafold",
                label=target_name,
                content=prediction.pdb,
                extension="pdb",
                content_type="chemical/x-pdb",
            )
            run = {
                **base_run,
                "status": "finished",
                "reason": "verified PDB stored and loaded as cycle evidence",
                "artifact_id": artifact.id,
                "artifact_key": artifact.key,
                "output_sha256": artifact.sha256,
                "metadata": prediction.response_metadata,
                "citation": ALPHAFOLD_CITATION,
            }
            _record_compute(db, cycle, workflow, run)
            if target_name == "working":
                parent_structure = parsed
            else:
                target = parsed
        except (NIMError, ValueError) as exc:
            run = {
                **base_run,
                "status": "failed",
                "reason": str(exc)[:500],
                "citation": ALPHAFOLD_CITATION,
            }
            _record_compute(db, cycle, workflow, run)
            if policy == "required":
                raise WorkflowError(f"required AlphaFold run failed: {exc}") from exc
    return parent_structure, target


def _run_proteinmpnn(
    db: Session,
    cycle: DesignCycle,
    project: Project,
    parent: ProteinCommit,
    parent_structure: structlib.Structure | None,
    plan: dict,
    workflow: dict[str, Any],
    settings,
) -> list[Candidate]:
    policy = workflow["policies"]["proteinmpnn"]
    if policy == "off":
        _record_compute(
            db,
            cycle,
            workflow,
            {"tool": "proteinmpnn", "status": "skipped", "reason": "disabled by scientist"},
        )
        return []
    if not settings.proteinmpnn_api_url:
        run = {
            "tool": "proteinmpnn",
            "status": "skipped" if policy == "auto" else "failed",
            "reason": "PROTEINMPNN_API_URL is not configured",
        }
        _record_compute(db, cycle, workflow, run)
        if policy == "required":
            raise WorkflowError(run["reason"])
        return []

    credible_backbone = bool(
        parent_structure is not None
        and (
            not (parent.structure_source or "").startswith("model:coarse")
            or any(
                run.get("tool") == "alphafold"
                and run.get("target") == "working"
                and run.get("status") == "finished"
                for run in workflow["runs"]
            )
        )
    )
    if not credible_backbone:
        run = {
            "tool": "proteinmpnn",
            "status": "skipped" if policy == "auto" else "failed",
            "reason": "ProteinMPNN requires an uploaded or verified AlphaFold backbone",
        }
        _record_compute(db, cycle, workflow, run)
        if policy == "required":
            raise WorkflowError(run["reason"])
        return []

    requested_positions = sorted(
        {
            int(pos)
            for agent in plan.get("agents", [])
            for pos in (agent.get("focus_regions") or [])
            if str(pos).isdigit() and 1 <= int(pos) <= len(parent.sequence)
        }
    )
    # Existing candidate evaluation is intentionally bounded to four substitutions. Let Devin
    # select the relevant focus, then enforce that scientific safety boundary in code.
    mutable_positions = requested_positions[:4]
    if not mutable_positions:
        run = {
            "tool": "proteinmpnn",
            "status": "skipped" if policy == "auto" else "failed",
            "reason": "the Devin plan supplied no valid focus residues for bounded redesign",
        }
        _record_compute(db, cycle, workflow, run)
        if policy == "required":
            raise WorkflowError(run["reason"])
        return []

    pdb = parent_structure.to_pdb()
    fixed_positions = [
        position for position in range(1, len(parent.sequence) + 1) if position not in set(mutable_positions)
    ]
    parameters = {
        "mutable_positions": mutable_positions,
        "num_sequences": settings.proteinmpnn_num_sequences,
        "sampling_temperature": settings.proteinmpnn_sampling_temperature,
        "random_seed": settings.proteinmpnn_random_seed,
    }
    base_run: dict[str, Any] = {
        "tool": "proteinmpnn",
        "target": "working",
        "provider": "nvidia-bionemo-nim",
        "model": settings.proteinmpnn_model_version,
        "input_sha256": input_hash(pdb),
        "parameters": parameters,
        "citation": PROTEINMPNN_CITATION,
    }
    try:
        prediction = _nim_client(settings).design_sequences(
            pdb,
            fixed_positions=fixed_positions,
            num_sequences=settings.proteinmpnn_num_sequences,
            temperature=settings.proteinmpnn_sampling_temperature,
            random_seed=settings.proteinmpnn_random_seed,
        )
        artifact = store_compute_artifact(
            db,
            project_id=project.id,
            cycle_id=cycle.id,
            tool="proteinmpnn",
            label="designs",
            content=prediction.mfasta,
            extension="fasta",
            content_type="text/x-fasta",
        )
        candidates: list[Candidate] = []
        rejected = 0
        for index, (_header, raw_sequence) in enumerate(prediction.sequences, start=1):
            try:
                sequence = seqlib.clean_sequence(raw_sequence.replace("/", ""))
                differences = seqlib.diff_sequences(parent.sequence, sequence)
            except (seqlib.SequenceError, ValueError):
                rejected += 1
                continue
            changed = [int(item["position"]) for item in differences]
            if (
                not differences
                or any(item.get("kind") != "substitution" for item in differences)
                or len(differences) > 4
                or any(position not in mutable_positions for position in changed)
            ):
                rejected += 1
                continue
            score = prediction.scores[index - 1] if index <= len(prediction.scores) else None
            candidates.append(
                Candidate(
                    label=f"mpnn-{index}",
                    parent_sequence=parent.sequence,
                    sequence=sequence,
                    rationale=(
                        "ProteinMPNN sequence sampled against the verified backbone at Devin-selected "
                        f"focus residues {mutable_positions}; provider score={score}."
                    ),
                    agent_role="proteinmpnn",
                    provider="nvidia-bionemo-nim",
                    citations=[PROTEINMPNN_CITATION],
                )
            )
        run = {
            **base_run,
            "status": "finished",
            "reason": f"accepted {len(candidates)} bounded designs; rejected {rejected}",
            "artifact_id": artifact.id,
            "artifact_key": artifact.key,
            "output_sha256": artifact.sha256,
            "candidate_count": len(candidates),
            "rejected_count": rejected,
            "metadata": prediction.response_metadata,
        }
        _record_compute(db, cycle, workflow, run)
        if policy == "required" and not candidates:
            raise WorkflowError("required ProteinMPNN run produced no valid bounded designs")
        return candidates
    except NIMError as exc:
        run = {**base_run, "status": "failed", "reason": str(exc)[:500]}
        _record_compute(db, cycle, workflow, run)
        if policy == "required":
            raise WorkflowError(f"required ProteinMPNN run failed: {exc}") from exc
        return []


# ------------------------------------------------------------------ the cycle


def run_cycle(db: Session, cycle_id: str, sleep=time.sleep) -> DesignCycle:
    settings = get_settings()
    cycle = db.get(DesignCycle, cycle_id)
    if cycle is None:
        raise KeyError(f"unknown cycle {cycle_id}")
    if cycle.status == "cancelled":
        return cycle
    project = db.get(Project, cycle.project_id)
    active_spec = db.scalar(
        select(ProblemSpec).where(
            ProblemSpec.project_id == project.id,
            ProblemSpec.status == "active",
        )
    )
    spec_prompt = (
        problem.prompt_block(active_spec, calibration.project_calibration(db, project.id))
        if active_spec
        else None
    )

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
    workflow = _workflow_state(cycle)
    _save_workflow(
        cycle,
        workflow,
        workflow_tools=workflow["policies"],
    )
    cycle.status = "preparing_compute"
    db.commit()
    try:
        # Required providers are checked before an orchestrator session can consume Devin ACUs.
        validate_required_providers(workflow["policies"], settings)
        parent_structure, target = _run_alphafold(
            db,
            cycle,
            project,
            parent,
            parent_structure,
            workflow,
            settings,
        )
        db.commit()
    except WorkflowError as exc:
        cycle.status = "failed"
        cycle.error = f"WorkflowError: {exc}"[:2000]
        cycle.finished_at = utcnow()
        _observe(db, cycle, "cycle_failed", cycle.error)
        db.commit()
        return cycle

    evidence = build_evidence(parent.sequence, parent_structure, target)
    evidence["compute_workflow"] = {
        "policies": workflow["policies"],
        "runs": workflow["runs"],
        "rule": "Only finished, hash-verified artifacts are model evidence; skipped is not success.",
    }
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
            problem_spec=spec_prompt,
            workflow_tools=workflow["policies"],
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
        plan_fallback = not plan.get("agents")
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
        _save_workflow(
            cycle,
            workflow,
            **plan,
            workflow_tools=workflow["policies"],
        )
        shortlist_size = int(plan.get("shortlist_size") or shortlist_size)

        # AlphaFold evidence is available to the planner. ProteinMPNN then consumes Devin's
        # narrow focus regions, while the backend enforces the four-substitution design boundary.
        proteinmpnn_candidates = _run_proteinmpnn(
            db,
            cycle,
            project,
            parent,
            parent_structure,
            plan,
            workflow,
            settings,
        )
        evidence["compute_workflow"] = {
            "policies": workflow["policies"],
            "runs": workflow["runs"],
        }
        db.commit()
        _observe(
            db,
            cycle,
            "plan",
            plan.get("strategy", "")[:2000],
            {
                "plan": plan,
                "workflow": workflow,
                "session": orchestrator.devin_session_url,
            },
            role="orchestrator",
            agent_run_id=orchestrator.id,
        )
        autonomy.record(
            db,
            project.id,
            "plan",
            "strategy=agent_plan" if not plan_fallback else "strategy=fallback",
            actor="system" if plan_fallback else "agent:orchestrator",
            autonomy="fallback" if plan_fallback else "agent_advised",
            basis={
                "strategy": plan.get("strategy", ""),
                "agent_roles": [a.get("role") for a in plan.get("agents", [])],
                "session_url": orchestrator.devin_session_url,
            },
            reversible=True,
            confidence_basis=(
                "orchestrator plan is an agent recommendation; " "fallback indicates no usable plan"
            ),
            cycle_id=cycle.id,
        )

        # 2) fan out -----------------------------------------------------------
        cycle.status = "fanning_out"
        db.commit()
        requested_roles = [str(a.get("role", "")).strip() for a in plan.get("agents", [])]
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
        launched_roles = [run.role for run in runs]
        autonomy.record(
            db,
            project.id,
            "fanout",
            f"launched_roles={','.join(launched_roles)}",
            actor="system",
            autonomy="autonomous",
            basis={
                "requested_roles": requested_roles,
                "launched_roles": launched_roles,
                "dropped_roles": [role for role in requested_roles if role not in launched_roles],
            },
            reversible=True,
            confidence_basis="fan-out follows the validated role vocabulary and cycle limits",
            cycle_id=cycle.id,
        )
        cycle.status = "awaiting_agents"
        db.commit()

        # 3) poll --------------------------------------------------------------
        supervisor.wait_for(runs, sleep=sleep)
        candidates: list[Candidate] = list(proteinmpnn_candidates)
        for run in runs:
            output = run.structured_output or {}
            found = _candidates_from_output(run.role, output, parent.sequence, source_run_id=run.id)
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
        evaluations = evaluate_all(candidates, target_structure=target, parent_structure=parent_structure)
        by_label: dict[str, Evaluation] = {e.label: e for e in evaluations}
        candidate_by_label = {c.label: c for c in candidates}
        runs_by_id = {r.id: r for r in runs}
        ranked = ranking.rank(evaluations, exclusions=exclusions)

        ranking_run: AgentRun | None = None
        if any(a.get("role") == "ranking" for a in plan.get("agents", [])):
            ranking_before = [candidate.label for candidate in ranked]
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
                str(item.get("label")): str(item.get("reason", "")) for item in triage.get("ordering") or []
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
        else:
            ranking_before = [candidate.label for candidate in ranked]
        autonomy.record(
            db,
            project.id,
            "ranking_order",
            f"top={','.join(candidate.label for candidate in ranked[:5])}",
            actor="agent:ranking" if ranking_run else "system",
            autonomy="agent_advised" if ranking_run else "autonomous",
            basis={
                "top_labels_before": ranking_before[:5],
                "top_labels_after": [candidate.label for candidate in ranked[:5]],
                "agent_exclusions": [candidate.label for candidate in ranked if candidate.excluded_reason],
            },
            reversible=True,
            confidence_basis=(
                "ranking order is deterministic unless an optional ranking agent triages the cohort"
            ),
            cycle_id=cycle.id,
        )
        failed_filter_names = sorted({name for candidate in ranked for name in candidate.failed_filters})
        autonomy.record(
            db,
            project.id,
            "filter_gate",
            f"passing={sum(candidate.passed_filters for candidate in ranked)}",
            actor="system",
            autonomy="autonomous",
            basis={
                "passing": sum(candidate.passed_filters for candidate in ranked),
                "failing": sum(not candidate.passed_filters for candidate in ranked),
                "failed_filter_names": failed_filter_names,
            },
            reversible=True,
            confidence_basis=(
                "filter recall is unmeasured; see filter-performance once >=10 paired designs exist"
            ),
            cycle_id=cycle.id,
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
            source_run = runs_by_id.get(source_candidate.source_run_id) if source_candidate else None
            mutation_summary = "+".join(m.get("mutation") for m in ev.mutations) or "no change"
            structure_key = store_structure(project.id, f"{cycle.id[:8]}-{cand.label}", ev.structure_pdb, db)
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
                provider=(source_candidate.provider if source_candidate else None) or supervisor.provider,
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
        head_commit = next((c for c in commits if c.label == best.label), None) if best else None
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
        autonomy.record(
            db,
            project.id,
            "branch_head",
            f"chosen_commit={head_commit.id if head_commit else previous_head}",
            actor="system",
            autonomy="autonomous" if head_commit else "human_required",
            basis={
                "chosen_commit": head_commit.id if head_commit else previous_head,
                "previous_head": previous_head,
                "unchanged": head_commit is None,
            },
            reversible=False,
            confidence_basis=(
                "the head follows the highest-ranked passing design; no passing design requires human review"
            ),
            cycle_id=cycle.id,
            commit_id=head_commit.id if head_commit else previous_head,
        )

        # 6) wet-lab pack ------------------------------------------------------
        deciding_readout = active_spec.target_readout if active_spec else None
        bench_candidates = [
            {
                "sequence": by_label[candidate.label].sequence,
                "mutations": by_label[candidate.label].mutations,
            }
            for candidate in ranked
            if candidate.passed_filters and not candidate.excluded_reason
        ]
        tier = (
            economics.cheapest_tier_for_readout(deciding_readout, bench_candidates) or "T2"
            if active_spec
            else "T2"
        )
        autonomy.record(
            db,
            project.id,
            "tier_choice",
            f"tier={tier}",
            actor="system",
            autonomy="autonomous",
            basis={
                "tier": tier,
                "deciding_readout": deciding_readout,
                "spec_version": active_spec.version if active_spec else None,
            },
            reversible=True,
            confidence_basis="tier is selected from the declared assay capability map and catalogue prices",
            cycle_id=cycle.id,
        )
        pack = wetlab.build_pack(
            ranked,
            by_label,
            parent.sequence,
            project.name,
            top_n=shortlist_size,
            total_candidate_pool=len(evaluations),
            calibration=calibration.project_calibration(db, project.id),
            cycle_id=cycle.id,
            tier=tier,
            deciding_readout=deciding_readout,
        )
        stop_rule = pack.get("economics", {}).get("stop_rule", {})
        autonomy.record(
            db,
            project.id,
            "shortlist_stop_rule",
            f"n_eff={stop_rule.get('n_eff', 0)}",
            actor="system",
            autonomy="autonomous",
            basis={
                "n_eff": stop_rule.get("n_eff"),
                "stop_reason": stop_rule.get("stop_reason"),
                "achieved_confidence": stop_rule.get("achieved_confidence"),
                "target_confidence": stop_rule.get("target_confidence"),
                "budget_limited": stop_rule.get("budget_limited"),
            },
            reversible=True,
            confidence_basis="stop rule uses the declared prior and planning-model budget arithmetic",
            cycle_id=cycle.id,
        )
        autonomy.record(
            db,
            project.id,
            "probe_sampling",
            f"probes={len(pack.get('probes', []))}",
            actor="system",
            autonomy="autonomous",
            basis={
                "probe_count": len(pack.get("probes", [])),
                "seed": cycle.id,
                "probe_cost": pack.get("economics", {}).get("probe_cost_usd"),
            },
            reversible=False,
            confidence_basis=(
                "filter recall is unmeasured; probes are the only future source of false-negative evidence"
            ),
            cycle_id=cycle.id,
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
