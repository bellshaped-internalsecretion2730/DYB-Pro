"""The always-on research daemon.

The daemon is what makes a program *autonomous* rather than *on-demand*: it wakes up on a schedule,
looks at every live program, and advances the ones whose gate says they can move on their own.

There are two ways to run it, and the platform is explicit about which one is active:

* ``devin`` - a real Devin **scheduled session** (v3 organization API). Devin itself wakes up,
  runs the program orchestrator playbook and reports. Requires ``DEVIN_ORG_ID``.
* ``local`` - the platform's own Celery beat tick calls :func:`daemon_plan` and runs the same
  round in-process. This is the only mode available without Devin credentials.

Autonomy is enforced here as well as at the gate: the daemon never performs an action the program's
autonomy level does not permit, and never signs a human-signed stage.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.devin.client import DevinAPIError, DevinClient, DevinNotConfigured
from app.pharma.stages import HUMAN_SIGNED_STAGES, stage

logger = logging.getLogger(__name__)

SCHEDULE_TAG = "pharmakon:daemon"
DEFAULT_FREQUENCY = "daily"


def schedule_name(program_name: str) -> str:
    return f"Pharmakon research daemon: {program_name}"[:120]


def daemon_prompt(program: dict, api_base: str) -> str:
    """Prompt for the scheduled Devin session that advances one program."""
    return f"""\
You are the research daemon for the Pharmakon drug-discovery program "{program.get('name')}"
(target {program.get('target_name')}, indication {program.get('indication')}).

Every time you wake up:
1. GET {api_base}/api/pharma/programs/{program.get('id')} to read the current stage, the gate
   state and the molecule portfolio.
2. Search for new literature, patents and clinical results published since the timestamp in
   `last_research_at`. Report only what is new and what it changes.
3. POST each new finding to {api_base}/api/pharma/programs/{program.get('id')}/research with the
   claim, the citation and the implication for the program.
4. If your findings invalidate a gate assumption - the target is contradicted, a competitor has
   published the same chemistry, a safety signal appeared for the mechanism - say so explicitly and
   recommend `recycle` or `kill`. That is the single most valuable thing you can do.
5. POST {api_base}/api/pharma/programs/{program.get('id')}/advance to run one round, but only if
   the program's autonomy level allows the current stage to advance without a human.

Rules:
- Never claim experimental evidence, clinical validation or human safety.
- Every finding needs a citation.
- Do not modify any code in any repository.
- Stages {", ".join(HUMAN_SIGNED_STAGES)} always require a human signature. Never attempt to pass
  them; prepare the package and stop.
"""


def daemon_status(program: dict | None = None) -> dict:
    """What mode the daemon can run in right now, and why."""
    settings = get_settings()
    status: dict = {
        "devin_configured": settings.devin_enabled,
        "devin_schedules_supported": settings.devin_enabled and settings.devin_api_flavor == "v3",
        "local_tick_available": True,
        "mode": None,
        "schedules": [],
    }
    if status["devin_schedules_supported"]:
        status["mode"] = "devin"
        try:
            with DevinClient(settings) as client:
                status["schedules"] = [
                    {
                        "scheduled_session_id": s.get("scheduled_session_id"),
                        "name": s.get("name"),
                        "enabled": s.get("enabled"),
                        "frequency": s.get("frequency"),
                        "last_executed_at": s.get("last_executed_at"),
                        "last_error_message": s.get("last_error_message"),
                    }
                    for s in client.list_schedules()
                    if SCHEDULE_TAG in (s.get("tags") or [])
                ]
        except (DevinAPIError, DevinNotConfigured, OSError) as exc:
            status["error"] = str(exc)[:300]
    else:
        status["mode"] = "local"
        status["note"] = (
            "Devin scheduled sessions need DEVIN_API_KEY and DEVIN_ORG_ID on the v3 API. Without "
            "them the daemon runs as a local Celery beat tick, which cannot search literature."
        )
    if program is not None:
        status["autonomy_level"] = program.get("autonomy_level")
        status["current_stage"] = program.get("current_stage")
    return status


def ensure_schedule(
    program: dict,
    *,
    api_base: str,
    frequency: str = DEFAULT_FREQUENCY,
    playbook_id: str | None = None,
    client: DevinClient | None = None,
) -> dict:
    """Create the program's Devin schedule if it does not exist yet. Idempotent by name."""
    owns_client = client is None
    try:
        client = client or DevinClient()
    except DevinNotConfigured as exc:
        return {"created": False, "scheduled_session_id": None, "error": str(exc)}
    name = schedule_name(str(program.get("name", "program")))
    try:
        for existing in client.list_schedules():
            if existing.get("name") == name:
                return {
                    "created": False,
                    "scheduled_session_id": existing.get("scheduled_session_id"),
                    "error": None,
                }
        created = client.create_schedule(
            name,
            daemon_prompt(program, api_base),
            frequency=frequency,
            playbook_id=playbook_id,
            tags=[SCHEDULE_TAG, f"pharmakon:program:{str(program.get('id', ''))[:12]}"],
        )
        return {
            "created": True,
            "scheduled_session_id": created.get("scheduled_session_id"),
            "error": None,
        }
    except (DevinAPIError, OSError) as exc:
        logger.info("daemon schedule creation failed: %s", exc)
        return {"created": False, "scheduled_session_id": None, "error": str(exc)[:300]}
    finally:
        if owns_client and client is not None:
            client.close()


def disable_schedule(scheduled_session_id: str, client: DevinClient | None = None) -> dict:
    owns_client = client is None
    try:
        client = client or DevinClient()
    except DevinNotConfigured as exc:
        return {"disabled": False, "error": str(exc)}
    try:
        client.delete_schedule(scheduled_session_id)
        return {"disabled": True, "error": None}
    except (DevinAPIError, OSError) as exc:
        return {"disabled": False, "error": str(exc)[:300]}
    finally:
        if owns_client and client is not None:
            client.close()


def daemon_plan(program: dict, gate: dict) -> dict:
    """Decide what the daemon is allowed to do for one program on this tick.

    Deterministic and side-effect free: the caller executes the returned action. Autonomy is
    enforced here so a scheduled tick can never quietly exceed the level a scientist granted.
    """
    autonomy = int(program.get("autonomy_level") or 0)
    stage_key = str(program.get("current_stage") or "")
    decision = gate.get("decision")
    requires_approval = bool(gate.get("requires_approval"))
    spec = stage(stage_key) if stage_key else None

    if spec is not None and spec.human_signature_required:
        return {
            "action": "await_human",
            "reason": f"stage {stage_key} always requires a human signature",
            "autonomy_level": autonomy,
        }
    if requires_approval:
        return {
            "action": "await_approval",
            "reason": gate.get("approval_reason") or "gate requires approval",
            "autonomy_level": autonomy,
        }
    if decision == "go":
        if spec is not None and autonomy < spec.min_autonomy:
            return {
                "action": "await_approval",
                "reason": (
                    f"advancing {stage_key} needs autonomy L{spec.min_autonomy}, "
                    f"program is at L{autonomy}"
                ),
                "autonomy_level": autonomy,
            }
        return {"action": "advance_stage", "reason": "gate passed", "autonomy_level": autonomy}
    if decision in {"recycle", "no_go"}:
        if autonomy < 1:
            return {
                "action": "await_approval",
                "reason": "L0 programs never run a round on their own",
                "autonomy_level": autonomy,
            }
        return {
            "action": "run_round",
            "reason": f"gate returned {decision}: run another design round",
            "autonomy_level": autonomy,
        }
    return {
        "action": "await_approval",
        "reason": "kill recommendations always require a human decision",
        "autonomy_level": autonomy,
    }
