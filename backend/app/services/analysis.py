"""OpenAI-backed structured/multimodal analysis.

Scope is deliberately narrow: OpenAI is used *only* to interpret sequence + structure + score plots
and to narrate a cycle for the scientist. It never plans the cycle and never replaces Devin — that
would violate the product contract. When no key is configured, deterministic summaries are used and
the response records `provider="deterministic"`.
"""

from __future__ import annotations

import base64
import json
import logging
import re

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

CYCLE_NARRATIVE_SCHEMA = {
    "type": "object",
    "required": ["headline", "what_changed", "why_it_matters", "risks", "next_experiment"],
    "properties": {
        "headline": {"type": "string"},
        "what_changed": {"type": "array", "items": {"type": "string"}},
        "why_it_matters": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "next_experiment": {"type": "string"},
    },
    "additionalProperties": False,
}


def _client(settings: Settings):
    from openai import OpenAI

    return OpenAI(api_key=settings.openai_api_key)


def _sampling_kwargs(model: str) -> dict:
    """Reasoning-family models reject an explicit temperature parameter."""
    return {} if model.startswith("gpt-5") else {"temperature": 0.2}


def _deterministic_narrative(payload: dict) -> dict:
    top = (payload.get("ranked") or [{}])[0]
    changed = [
        f"{c.get('label')}: {'+'.join(c.get('mutations') or []) or 'parent'} "
        f"(composite {c.get('composite_score')})"
        for c in (payload.get("ranked") or [])[:5]
    ]
    return {
        "headline": (
            f"Top candidate {top.get('label', 'n/a')} with composite score "
            f"{top.get('composite_score', 'n/a')}"
        ),
        "what_changed": changed,
        "why_it_matters": (
            "Candidates were ranked on binding, stability, solubility, aggregation and "
            "immunogenicity proxies; hard developability filters removed non-viable designs before "
            "ranking."
        ),
        "risks": (payload.get("risks") or ["All scores are in-silico proxies with uncertainty."]),
        "next_experiment": (
            "Test the shortlist and feed measured outcomes back into the version graph so the next "
            "cycle can exclude what failed."
        ),
        "provider": "deterministic",
    }


def narrate_cycle(payload: dict, settings: Settings | None = None) -> dict:
    """Structured, scientist-facing narrative of one cycle's outcome."""
    settings = settings or get_settings()
    if not settings.openai_enabled:
        return _deterministic_narrative(payload)
    prompt = (
        "You are summarizing one in-silico protein design cycle for a protein engineer. Be "
        "concrete and quantitative; never invent numbers that are not in the data. Data:\n"
        + json.dumps(payload, indent=2, default=str)[:12000]
    )
    try:
        client = _client(settings)
        response = client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": "Reply with JSON matching the requested schema."},
                {"role": "user", "content": prompt},
                {
                    "role": "user",
                    "content": "JSON schema: " + json.dumps(CYCLE_NARRATIVE_SCHEMA),
                },
            ],
            response_format={"type": "json_object"},
            **_sampling_kwargs(settings.openai_model),
        )
        data = json.loads(response.choices[0].message.content or "{}")
        data["provider"] = "openai"
        data["model"] = settings.openai_model
        return data
    except Exception as exc:  # network/quota/parse failures must never break a cycle
        logger.warning("openai narrate_cycle failed, falling back: %s", exc)
        fallback = _deterministic_narrative(payload)
        fallback["fallback_reason"] = str(exc)[:300]
        return fallback


def analyze_design(
    *,
    sequence: str,
    structure_summary: dict,
    scores: dict,
    plot_png: bytes | None = None,
    settings: Settings | None = None,
) -> dict:
    """Multimodal read of a single design: sequence + structure metrics + an optional score plot."""
    settings = settings or get_settings()
    if not settings.openai_enabled:
        return {
            "provider": "deterministic",
            "assessment": (
                f"{len(sequence)} residues; radius of gyration "
                f"{structure_summary.get('radius_of_gyration')} Å, "
                f"{structure_summary.get('helix_fraction', 0)} helical fraction; composite score "
                f"{scores.get('composite_score')}."
            ),
            "flags": [k for k, v in scores.items() if isinstance(v, int | float) and v < 0],
        }
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                "Assess this protein design for wet-lab readiness. Use only the provided data.\n"
                f"sequence ({len(sequence)} aa): {sequence}\n"
                f"structure: {json.dumps(structure_summary, default=str)[:4000]}\n"
                f"scores: {json.dumps(scores, default=str)[:4000]}"
            ),
        }
    ]
    if plot_png:
        b64 = base64.b64encode(plot_png).decode()
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
        )
    try:
        client = _client(settings)
        response = client.chat.completions.create(
            model=settings.openai_vision_model,
            messages=[{"role": "user", "content": content}],
            **_sampling_kwargs(settings.openai_vision_model),
        )
        return {
            "provider": "openai",
            "model": settings.openai_vision_model,
            "assessment": response.choices[0].message.content or "",
        }
    except Exception as exc:
        logger.warning("openai analyze_design failed: %s", exc)
        return {"provider": "deterministic", "assessment": "", "fallback_reason": str(exc)[:300]}


CHAT_ACTION_TYPES = {
    "run_cycle",
    "handoff",
    "open_tab",
    "trigger_research",
    "select_version",
    "advance_program",
}
CHAT_TABS = ("structure", "lineage", "shortlist", "research", "lab", "data")


def _infer_chat_actions(prompt: str, context: dict) -> list[dict]:
    """Provide dependable actions for explicit commands even if the model omits tool JSON."""
    text = prompt.strip()
    lower = text.lower()
    if re.search(r"\b(run|start|launch)\b.{0,30}\b(design )?cycle\b", lower):
        return [{"type": "run_cycle", "value": text, "reason": "Explicit design-cycle request."}]
    if re.search(r"\b(hand ?off|delegate)\b", lower):
        return [{"type": "handoff", "value": text, "reason": "Explicit swarm handoff request."}]
    if re.search(r"\b(refresh|run|update)\b.{0,24}\bresearch\b", lower):
        return [{"type": "trigger_research", "value": "", "reason": "Explicit research refresh."}]
    for tab in CHAT_TABS:
        if re.search(rf"\b(open|show|view)\b.{{0,24}}\b{re.escape(tab)}\b", lower):
            return [{"type": "open_tab", "value": tab, "reason": f"Open {tab}."}]
    programs = context.get("programs") or []
    if (
        re.search(r"\b(run|advance|continue)\b.{0,30}\b(program|pharmakon|round)\b", lower)
        and len(programs) == 1
    ):
        return [
            {
                "type": "advance_program",
                "value": str(programs[0].get("id") or ""),
                "reason": "Advance the selected project's only drug program.",
            }
        ]
    return []


def _sanitize_chat_actions(value: object, enabled: bool) -> list[dict]:
    if not enabled or not isinstance(value, list):
        return []
    actions: list[dict] = []
    for candidate in value[:5]:
        if not isinstance(candidate, dict):
            continue
        action_type = str(candidate.get("type") or "")
        action_value = str(candidate.get("value") or "")[:1000]
        reason = str(candidate.get("reason") or "")[:300]
        if action_type not in CHAT_ACTION_TYPES:
            continue
        if action_type == "open_tab" and action_value not in CHAT_TABS:
            continue
        actions.append({"type": action_type, "value": action_value, "reason": reason})
    return actions


def chat_workspace(
    *,
    messages: list[dict],
    context: dict,
    requested_model: str | None = None,
    skill: str = "workflow",
    actions_enabled: bool = True,
    settings: Settings | None = None,
) -> dict:
    """Context-aware research chat with a small, explicit workspace action contract."""
    settings = settings or get_settings()
    model = (
        requested_model
        if requested_model in settings.openai_chat_model_list
        else settings.openai_model
    )
    last_prompt = next(
        (
            str(message.get("content") or "")
            for message in reversed(messages)
            if message.get("role") == "user"
        ),
        "",
    )
    inferred = _infer_chat_actions(last_prompt, context) if actions_enabled else []
    if not settings.openai_enabled:
        return {
            "text": (
                "Ready to apply that workspace action. Add an OpenAI key for a context-aware "
                "scientific explanation."
                if inferred
                else (
                    "OpenAI chat is not configured. Local design cycles, versioning and the "
                    "Devin swarm remain available."
                )
            ),
            "actions": inferred,
            "provider": "deterministic",
            "model": model,
            "skill": skill,
        }

    system = """You are DYB Pro's concise research workspace agent.
Use the supplied selected protein version, project, cycle, drug-program and PDB-input context.
Never invent assay results, affinity, docking, safety, efficacy, citations or completed compute.
Keep visible text short. When the user explicitly asks for a supported workspace operation and
actions are enabled, return the corresponding action instead of merely describing the button.
Supported actions: run_cycle (value is the brief), handoff (value is the brief), open_tab
(structure|lineage|shortlist|research|lab|data), trigger_research, select_version (commit id or
label), advance_program (program id). Actions are reversible or provenance-tracked by the existing
workspace APIs; never claim they succeeded before the UI reports the result."""
    skill_notes = {
        "workflow": "Coordinate the workspace and prefer an action for an explicit command.",
        "structure": "Focus on the selected version and uploaded target/ligand coordinates.",
        "research": "Explain evidence and uncertainty; do not propose actions unless explicitly requested.",
        "drug-discovery": "Use the selected project's Pharmakon programs and binding inputs.",
    }
    prompt = (
        f"Skill: {skill_notes.get(skill, skill_notes['workflow'])}\n"
        f"Actions enabled: {actions_enabled}\n"
        f"Workspace context:\n{json.dumps(context, default=str)[:12000]}"
    )
    try:
        response = _client(settings).chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "system", "content": prompt},
                *messages[-10:],
                {
                    "role": "system",
                    "content": (
                        "Return JSON only: {\"text\": string, \"actions\": "
                        "[{\"type\": string, \"value\": string, \"reason\": string}]}."
                    ),
                },
            ],
            response_format={"type": "json_object"},
            **_sampling_kwargs(model),
        )
        parsed = json.loads(response.choices[0].message.content or "{}")
        text = str(parsed.get("text") or "").strip()[:6000]
        if not text:
            raise ValueError("assistant returned no text")
        actions = _sanitize_chat_actions(parsed.get("actions"), actions_enabled)
        if not actions and inferred:
            actions = inferred
        return {
            "text": text,
            "actions": actions,
            "provider": "openai",
            "model": model,
            "skill": skill,
        }
    except Exception as exc:
        logger.warning("openai workspace chat failed: %s", exc)
        return {
            "text": (
                "The model is unavailable, but the requested workspace action is ready."
                if inferred
                else "The model is temporarily unavailable. Local tools and the Devin swarm are still online."
            ),
            "actions": inferred,
            "provider": "deterministic",
            "model": model,
            "skill": skill,
        }
