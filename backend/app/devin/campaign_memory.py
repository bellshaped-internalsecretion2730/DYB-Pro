"""Campaign memory: turn what a program learned at a gate into durable knowledge.

Two layers, deliberately:

* The **local digest** is always produced. It is a deterministic, auditable summary of the
  program's rounds, gate decisions, exclusions and prediction drift, and it is what goes into the
  next round's prompts. No network, no Devin, no cost.
* The **knowledge note** is optional. When Devin is configured, the digest is also written to an
  org knowledge note (created once per program, updated in place), so *future* Devin sessions -
  including ones nobody started from this platform - inherit what the campaign learned.
"""

from __future__ import annotations

import logging

from app.devin.client import DevinAPIError, DevinClient, DevinNotConfigured

logger = logging.getLogger(__name__)

NOTE_PREFIX = "Pharmakon campaign"
MAX_EXCLUSIONS = 40
MAX_LESSONS = 20


def note_name(program_name: str) -> str:
    return f"{NOTE_PREFIX}: {program_name}"[:120]


def note_trigger(program: dict) -> str:
    return (
        f"When working on the {program.get('target_name')} program or on small-molecule design "
        f"for {program.get('indication')} in this repository"
    )


def build_digest(
    *,
    program: dict,
    gate_history: list[dict],
    exclusions: list[dict],
    lessons: list[str],
    drift: dict | None = None,
    best_molecules: list[dict] | None = None,
) -> str:
    """Deterministic markdown digest of a campaign, used as prompt context and as note body."""
    lines = [
        f"# {note_name(str(program.get('name', 'program')))}",
        "",
        f"- target: {program.get('target_name')}",
        f"- indication: {program.get('indication')}",
        f"- objective: {program.get('objective')}",
        f"- current stage: {program.get('current_stage')}",
        f"- autonomy: L{program.get('autonomy_level')}",
        "",
        "All predictions below come from deterministic in-silico heuristics unless a line says "
        "`measured`. They are ranking hypotheses, not experimental evidence.",
        "",
        "## Gate history",
    ]
    if gate_history:
        for entry in gate_history:
            lines.append(
                f"- {entry.get('stage_key')}: **{entry.get('decision')}** "
                f"(score {entry.get('score')}) - {entry.get('rationale')}"
            )
    else:
        lines.append("- no gate has been evaluated yet")

    lines += ["", "## What not to try again"]
    if exclusions:
        for item in exclusions[:MAX_EXCLUSIONS]:
            lines.append(f"- `{item.get('smiles')}`: {item.get('reason')}")
    else:
        lines.append("- nothing excluded yet")

    lines += ["", "## Lessons"]
    if lessons:
        lines += [f"- {lesson}" for lesson in lessons[:MAX_LESSONS]]
    else:
        lines.append("- none recorded yet")

    if best_molecules:
        lines += ["", "## Best molecules so far (predicted)"]
        for mol in best_molecules[:10]:
            binding = mol.get("binding") or {}
            lines.append(
                f"- `{mol.get('smiles')}` composite {mol.get('composite_score')} "
                f"pKd(proxy) {binding.get('pkd')} verdict {mol.get('verdict')}"
            )

    if drift:
        lines += [
            "",
            "## Prediction drift (predicted vs measured potency)",
            f"- pairs compared: {drift.get('n')}",
            f"- RMSE: {drift.get('rmse')} log units",
            f"- bias: {drift.get('bias')} log units",
        ]
        if (drift.get("n") or 0) >= 3 and (drift.get("rmse") or 0) > 1.0:
            lines.append(
                "- the binding proxy is poorly calibrated for this target: prefer measured data "
                "and treat the ranking as ordinal only"
            )

    return "\n".join(lines)


def lessons_from_gate(gate: dict, metrics: dict) -> list[str]:
    """Extract reusable statements from one gate evaluation - facts, not encouragement."""
    lessons: list[str] = []
    for criterion in gate.get("criteria", []):
        if criterion.get("passed"):
            continue
        observed = criterion.get("observed")
        detail = "no evidence recorded" if observed is None else f"observed {observed}"
        lessons.append(
            f"{gate.get('stage')}: {criterion.get('label')} failed ({detail}, "
            f"needs {criterion.get('requirement')})"
        )
    if gate.get("decision") == "kill":
        lessons.append(
            f"{gate.get('stage')}: program recommended for kill - {gate.get('rationale')}"
        )
    drift_rmse = metrics.get("prediction_drift_rmse")
    if drift_rmse is not None and drift_rmse > 1.0:
        lessons.append(
            f"potency predictions for this target are off by {drift_rmse} log units RMSE against "
            "measured data; do not use absolute predicted affinities in decisions"
        )
    return lessons


def sync_note(
    *,
    program: dict,
    digest: str,
    note_id: str | None = None,
    client: DevinClient | None = None,
) -> dict:
    """Create or update the campaign's org knowledge note. Never raises on Devin failure.

    Returns ``{"synced": bool, "note_id": str | None, "error": str | None}`` so the caller can
    record honestly whether the campaign memory reached Devin or stayed local-only.
    """
    owns_client = client is None
    try:
        client = client or DevinClient()
    except DevinNotConfigured as exc:
        return {"synced": False, "note_id": None, "error": str(exc)}
    name = note_name(str(program.get("name", "program")))
    trigger = note_trigger(program)
    try:
        if note_id is None:
            for note in client.list_knowledge():
                if note.get("name") == name:
                    note_id = note.get("id") or note.get("note_id")
                    break
        if note_id:
            client.update_knowledge(note_id, name, digest, trigger)
        else:
            created = client.create_knowledge(name, digest, trigger)
            note_id = created.get("id") or created.get("note_id")
        return {"synced": bool(note_id), "note_id": note_id, "error": None}
    except (DevinAPIError, OSError) as exc:
        logger.info("campaign memory sync failed: %s", exc)
        return {"synced": False, "note_id": note_id, "error": str(exc)[:300]}
    finally:
        if owns_client and client is not None:
            client.close()
