"""Propose the next version from everything the campaign has learned.

The proposal is deliberately *derived*, not invented: candidate mutations come from the structure
(exposure, packing) and from the scientist's labels, they are scored with the same skills the wet-lab
predictions use, and they are filtered by what the campaign already tried and by which hypotheses
the measurements refuted. The metric to move is chosen from the measured drift, not from taste.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app import skills
from app.models import Project, ProteinCommit, ResearchProject
from app.services import cycle as cycle_svc
from app.services import evaluation as ev
from app.services import lab_research as research_svc
from app.services import wetlab_loop
from app.toolkit import structure as structlib
from app.toolkit.constants import HYDROPATHY

logger = logging.getLogger(__name__)

SOLUBILIZING = ("K", "E", "Q", "D", "S", "R", "T", "N")
PACKING = {"A": "V", "V": "I", "G": "A", "S": "A", "T": "V", "I": "L", "L": "I", "M": "L", "C": "S"}

# Which metric a proposal should move, given where the campaign is weakest.
GOAL_METRICS = ("melting_temperature", "soluble_fraction", "expression_yield", "aggregation_hmw")


def protected_positions(labels: list) -> set[int]:
    """Residues the scientist told us not to touch."""
    out: set[int] = set()
    for label in labels:
        if label.kind in {"active_site", "epitope"}:
            out.update(int(r) for r in (label.residues or []))
    return out


def preferred_positions(labels: list) -> list[int]:
    """Residues the scientist pointed at: liabilities first, then explicit mutation intent."""
    liability: list[int] = []
    intent: list[int] = []
    for label in labels:
        if label.kind == "liability":
            liability.extend(int(r) for r in (label.residues or []))
        elif label.kind == "mutation_intent":
            intent.extend(int(r) for r in (label.residues or []))
    return list(dict.fromkeys(intent + liability))


def tried_mutation_sets(db: Session, rp: ResearchProject) -> set[str]:
    sets: set[str] = set()
    for commit in research_svc.version_path(db, rp):
        muts = sorted(m["mutation"] for m in (commit.mutations or []) if m.get("mutation"))
        if muts:
            sets.add(",".join(muts))
    return sets


def refuted_mutations(db: Session, rp: ResearchProject) -> set[str]:
    """Single mutations that appear in a refuted hypothesis are not proposed again."""
    out: set[str] = set()
    for h in research_svc.hypotheses_for(db, rp):
        if h.status != "refuted":
            continue
        commit = db.get(ProteinCommit, h.commit_id) if h.commit_id else None
        if commit is None:
            continue
        muts = [m["mutation"] for m in (commit.mutations or []) if m.get("mutation")]
        if len(muts) == 1:
            out.add(muts[0])
    return out


def target_metric(db: Session, rp: ResearchProject) -> tuple[str, str]:
    """Move the metric with the largest measured shortfall; fall back to stability."""
    drift = {d.metric: d for d in research_svc.drift_models(db, rp)}
    for metric in GOAL_METRICS:
        model = drift.get(metric)
        if model is None or not model.history:
            continue
        latest = model.history[-1]
        measured, predicted = latest.get("measured"), latest.get("predicted")
        if measured is None or predicted is None:
            continue
        higher_is_better = metric != "aggregation_hmw"
        missed = (measured < predicted) if higher_is_better else (measured > predicted)
        if missed:
            return metric, (
                f"{metric} came in at {measured} against a predicted {predicted}; that shortfall is "
                "the campaign's binding constraint"
            )
    return GOAL_METRICS[0], "no measurement contradicts the model yet, so keep pushing stability"


def candidate_mutations(
    sequence: str,
    structure: structlib.Structure | None,
    *,
    metric: str,
    protected: set[int],
    preferred: list[int],
    exclude: set[str],
    limit: int = 6,
) -> list[tuple[str, str]]:
    """(mutation, rationale) pairs aimed at one metric, respecting the scientist's labels."""
    exposure = structlib.relative_exposure(structure) if structure is not None else []
    out: list[tuple[str, str]] = []

    def add(pos: int, new_aa: str, why: str) -> None:
        if pos in protected or not (1 <= pos <= len(sequence)):
            return
        wt = sequence[pos - 1]
        if wt == new_aa:
            return
        token = f"{wt}{pos}{new_aa}"
        if token in exclude or any(token == m for m, _ in out):
            return
        out.append((token, why))

    scored: list[tuple[float, int]] = []
    for i, aa in enumerate(sequence):
        exp = exposure[i] if i < len(exposure) else 0.6
        hyd = HYDROPATHY.get(aa, 0.0)
        if hyd > 1.0:
            scored.append((hyd * (0.3 + exp), i + 1))
    scored.sort(key=lambda t: (-t[0], t[1]))
    exposed_hydrophobic = [pos for _, pos in scored]
    buried = (
        [i + 1 for i in sorted(range(len(exposure)), key=lambda i: exposure[i])
         if sequence[i] in PACKING]
        if exposure
        else [i + 1 for i, aa in enumerate(sequence) if aa in PACKING]
    )

    for pos in preferred:
        if 1 <= pos <= len(sequence):
            wt = sequence[pos - 1]
            if HYDROPATHY.get(wt, 0.0) > 1.0:
                add(pos, SOLUBILIZING[pos % len(SOLUBILIZING)],
                    f"residue {pos} is inside a scientist-labelled liability and is hydrophobic")
            elif wt in PACKING:
                add(pos, PACKING[wt], f"scientist-labelled position {pos}, packing substitution")

    if metric in {"soluble_fraction", "expression_yield", "aggregation_hmw"}:
        for idx, pos in enumerate(exposed_hydrophobic[:limit]):
            add(pos, SOLUBILIZING[idx % len(SOLUBILIZING)],
                f"exposed hydrophobic residue {pos} -> charged/polar reduces the aggregation-prone patch")
    for pos in buried[:limit]:
        wt = sequence[pos - 1]
        add(pos, PACKING[wt], f"buried {wt}{pos} -> {PACKING[wt]} improves core packing (stability)")
    if metric == "melting_temperature":
        for idx, pos in enumerate(exposed_hydrophobic[:limit]):
            add(pos, SOLUBILIZING[(idx + 3) % len(SOLUBILIZING)],
                f"surface charge at {pos} adds a salt-bridge opportunity without touching the core")
    return out[: limit * 2]


def propose_next(
    db: Session,
    rp: ResearchProject,
    project: Project,
    commit: ProteinCommit,
    *,
    top_n: int = 3,
    host: str = "E. coli BL21(DE3)",
) -> dict:
    """Rank derived candidates by predicted gain per dollar and return the winner."""
    sequence = commit.sequence
    labels = research_svc.labels_for(db, commit.id)
    metric, why_metric = target_metric(db, rp)
    exclude = refuted_mutations(db, rp)
    parent_structure = cycle_svc.load_structure(db, commit)
    pairs = candidate_mutations(
        sequence,
        parent_structure,
        metric=metric,
        protected=protected_positions(labels),
        preferred=preferred_positions(labels),
        exclude=exclude,
    )
    if not pairs:
        return {
            "proposed": None,
            "target_metric": metric,
            "reason": "every derived candidate is either protected by a label or already refuted",
        }
    tried = tried_mutation_sets(db, rp)
    target = cycle_svc.target_structure(project)
    candidates: list[ev.Candidate] = []
    rationales: dict[str, str] = {}
    for mutation, why in pairs[: top_n * 3]:
        if mutation in tried:
            continue
        label = f"{commit.label or commit.id[:6]}+{mutation}"
        candidates.append(
            ev.Candidate(
                label=label,
                parent_sequence=sequence,
                mutations=[mutation],
                rationale=why,
                agent_role="research-daemon",
            )
        )
        rationales[label] = why
        if len(candidates) >= top_n * 2:
            break
    evaluations = [
        e for e in ev.evaluate_all(candidates, target_structure=target, parent_structure=parent_structure)
        if e.sequence
    ]
    if not evaluations:
        return {"proposed": None, "target_metric": metric, "reason": "no candidate evaluated cleanly"}

    assessed = skills.run(
        "skill.drug_discovery",
        {
            "candidates": [
                {
                    "label": e.label,
                    "sequence": e.sequence,
                    "mutations": e.mutations,
                    "affinity_score": float(e.scores.get("binding_score", 0.0) or 0.0),
                    "affinity_sd": float(e.uncertainty.get("binding_score", 0.0) or 0.0),
                }
                for e in evaluations
            ]
        },
    )
    by_label = {a["label"]: a for a in assessed["assessments"]}
    baseline = float((commit.scores or {}).get("composite_score", 0.0) or 0.0)
    items = []
    for e in evaluations:
        a = by_label.get(e.label, {})
        if not e.filters.get("passed", False):
            continue
        gain = float(a.get("developability_index", 0.0)) - baseline
        items.append(
            {
                "label": e.label,
                "value": round(gain, 5),
                "sd": round(float(a.get("developability_sd") or 0.1), 5),
                "cost_usd": 150.0 + 60.0 * len(e.mutations),
            }
        )
    if not items:
        return {
            "proposed": None,
            "target_metric": metric,
            "reason": "every derived candidate failed a hard developability filter",
            "rejected": [
                {"label": e.label, "failed": e.filters.get("failed", [])} for e in evaluations
            ],
        }
    ranking = skills.run(
        "skill.math", {"operation": "rank_under_cost", "items": items, "exploration": 1.0}
    )["result"]["ranked"]
    winner_label = ranking[0]["label"]
    winner = next(e for e in evaluations if e.label == winner_label)
    winner_assessment = by_label.get(winner_label, {})

    predicted = wetlab_loop.predict_metrics(
        db,
        rp,
        ProteinCommit(
            id=f"proposal:{winner_label}",
            project_id=project.id,
            sequence=winner.sequence,
            label=winner_label,
            mutations=winner.mutations,
            scores=winner.scores,
            uncertainty=winner.uncertainty,
        ),
        host=host,
    )
    current = wetlab_loop.predict_metrics(db, rp, commit, host=host)
    deltas = {}
    for name, pred in predicted["predictions"].items():
        now = current["predictions"].get(name, {}).get("value")
        if isinstance(pred["value"], int | float) and isinstance(now, int | float):
            deltas[name] = round(float(pred["value"]) - float(now), 4)
    return {
        "proposed": {
            "label": winner_label,
            "mutations": [m["mutation"] for m in winner.mutations if m.get("mutation")],
            "sequence": winner.sequence,
            "rationale": rationales.get(winner_label, ""),
            "scores": winner.scores,
            "uncertainty": winner.uncertainty,
            "filters": winner.filters,
            "developability_index": winner_assessment.get("developability_index"),
            "predicted_wetlab": predicted["predictions"],
            "predicted_delta": deltas,
            "citations": sorted(set(winner.citations + predicted["citations"]))[:20],
        },
        "target_metric": metric,
        "why_this_metric": why_metric,
        "ranking": ranking,
        "considered": [
            {
                "label": e.label,
                "mutations": [m["mutation"] for m in e.mutations if m.get("mutation")],
                "passed_filters": bool(e.filters.get("passed")),
                "failed": e.filters.get("failed", []),
                "rationale": rationales.get(e.label, ""),
            }
            for e in evaluations
        ],
        "excluded_by_history": sorted(exclude),
        "protected_residues": sorted(protected_positions(labels)),
        "skills_used": ["skill.drug_discovery", "skill.math", "skill.physics", "skill.chemistry"],
    }
