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
            temperature=0.2,
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
            temperature=0.2,
        )
        return {
            "provider": "openai",
            "model": settings.openai_vision_model,
            "assessment": response.choices[0].message.content or "",
        }
    except Exception as exc:
        logger.warning("openai analyze_design failed: %s", exc)
        return {"provider": "deterministic", "assessment": "", "fallback_reason": str(exc)[:300]}
