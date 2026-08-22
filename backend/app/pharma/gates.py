"""Gate evaluation: turn recorded evidence into Go / No-Go / Recycle / Kill.

A gate decision is a pure function of ``(stage, metrics, autonomy_level, stage_cycles)``. The
same inputs always produce the same decision, and the decision record stores every criterion
result, so a reviewer can see exactly which number moved the program — and nothing here can be
talked into a decision by an agent's prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.pharma.stages import Stage, next_stage, stage

# A program that keeps failing the same blocking criterion is not learning; after this many
# rounds in one stage an unrecoverable failure becomes a kill rather than another recycle.
RECYCLE_PATIENCE = 3
GO_SCORE_THRESHOLD = 0.75


@dataclass
class GateResult:
    stage_key: str
    decision: str
    score: float
    criteria: list[dict]
    blocking_failures: list[dict]
    rationale: str
    next_stage: str | None
    requires_approval: bool
    approval_reason: str
    autonomy_level: int
    recommended_actions: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "stage": self.stage_key,
            "decision": self.decision,
            "score": self.score,
            "criteria": self.criteria,
            "blocking_failures": [c["key"] for c in self.blocking_failures],
            "rationale": self.rationale,
            "next_stage": self.next_stage,
            "requires_approval": self.requires_approval,
            "approval_reason": self.approval_reason,
            "autonomy_level": self.autonomy_level,
            "recommended_actions": self.recommended_actions,
        }


def evaluate_gate(
    stage_key: str,
    metrics: dict[str, float],
    *,
    autonomy_level: int = 2,
    stage_cycles: int = 0,
) -> GateResult:
    spec = stage(stage_key)
    results = [c.check(metrics) for c in spec.criteria]
    total_weight = sum(c["weight"] for c in results) or 1.0
    score = sum(c["weight"] for c in results if c["passed"]) / total_weight
    blocking_failures = [c for c in results if c["blocking"] and not c["passed"]]

    decision, rationale = _decide(spec, results, blocking_failures, score, stage_cycles)
    requires_approval, approval_reason = _approval(spec, decision, autonomy_level)
    return GateResult(
        stage_key=stage_key,
        decision=decision,
        score=round(score, 3),
        criteria=results,
        blocking_failures=blocking_failures,
        rationale=rationale,
        next_stage=next_stage(stage_key) if decision == "go" else None,
        requires_approval=requires_approval,
        approval_reason=approval_reason,
        autonomy_level=autonomy_level,
        recommended_actions=_actions(results, decision),
    )


def _decide(
    spec: Stage,
    results: list[dict],
    blocking_failures: list[dict],
    score: float,
    stage_cycles: int,
) -> tuple[str, str]:
    if not blocking_failures and score >= GO_SCORE_THRESHOLD:
        return "go", (
            f"All blocking criteria for {spec.name} are met and the weighted gate score is "
            f"{score:.2f} (>= {GO_SCORE_THRESHOLD}). Deliverable: {spec.exit_deliverable}"
        )
    if not blocking_failures:
        weak = ", ".join(c["label"] for c in results if not c["passed"]) or "none"
        return "no_go", (
            f"No blocking failure, but the weighted gate score is {score:.2f} (< "
            f"{GO_SCORE_THRESHOLD}). Hold at {spec.name} and close: {weak}."
        )

    labels = ", ".join(
        f"{c['label']} (observed {c['observed']}, needs {c['requirement']})"
        for c in blocking_failures
    )
    # Absence of evidence is not disproof: a criterion nobody has measured yet can only send the
    # program back for another round, never kill it.
    measured = [c for c in blocking_failures if not c["missing_evidence"]]
    kinds = {c["kind"] for c in measured}
    if not measured:
        missing = ", ".join(c["label"] for c in blocking_failures)
        if stage_cycles >= RECYCLE_PATIENCE:
            return "no_go", (
                f"{stage_cycles} rounds at {spec.name} have still produced no evidence for: "
                f"{missing}. Holding instead of killing: the criteria were never measured, which "
                "usually means the evidence source (literature, patents, assay data) is not "
                "reachable and a human has to supply it."
            )
        return "recycle", (
            f"No evidence yet for the blocking criteria at {spec.name}: {missing}. Recycle to "
            "generate that evidence; an unmeasured criterion is not a failed one."
        )
    if "validity" in kinds:
        return "kill", (
            f"The premise of the program fails at {spec.name}: {labels}. This is not fixable by "
            "another design round, so the program is killed rather than recycled."
        )
    if stage_cycles >= RECYCLE_PATIENCE:
        return "kill", (
            f"{stage_cycles} rounds at {spec.name} have not cleared: {labels}. Patience of "
            f"{RECYCLE_PATIENCE} rounds is exhausted; killing beats spending on a flat trajectory."
        )
    if "safety" in kinds:
        return "recycle", (
            f"Safety-blocking failure at {spec.name}: {labels}. Recycle into a design round that "
            "targets the liability directly; the current chemistry cannot advance."
        )
    return "recycle", (
        f"Blocking failure at {spec.name}: {labels}. Recycle for another optimisation round."
    )


def _approval(spec: Stage, decision: str, autonomy_level: int) -> tuple[bool, str]:
    if spec.human_signature_required:
        return True, (
            f"{spec.name} commits the organisation to regulatory filings or human exposure; a "
            "human signature is required at every autonomy level."
        )
    if decision == "kill":
        return True, "Killing a program is irreversible, so a human confirms it."
    if decision == "recycle":
        if autonomy_level >= 1:
            return False, ""
        return True, "Autonomy L0: every action is executed by a human."
    if decision == "go":
        if autonomy_level >= spec.min_autonomy:
            return False, ""
        return True, (
            f"Advancing past {spec.name} needs autonomy L{spec.min_autonomy}; the program runs at "
            f"L{autonomy_level}."
        )
    return False, ""


def _actions(results: list[dict], decision: str) -> list[str]:
    if decision == "go":
        return []
    actions: list[str] = []
    for c in sorted(results, key=lambda r: (-r["weight"], r["key"])):
        if c["passed"]:
            continue
        if c["missing_evidence"]:
            actions.append(f"Generate the missing evidence for '{c['label']}' ({c['key']}).")
        else:
            actions.append(
                f"Move {c['label']} from {c['observed']} to {c['requirement']}"
                + (f" — {c['rationale']}" if c["rationale"] else "")
            )
    return actions[:8]
