"""JSON Schemas for structured handoff between the orchestrator and child agents.

These are passed to the Devin API as `structured_output_schema`, so every agent returns
machine-readable results that DYB Pro can validate, score and commit.
"""

from __future__ import annotations

ROLES = ("sequence", "structure", "docking", "literature", "ranking")

CANDIDATE_SCHEMA = {
    "type": "object",
    "required": ["label", "mutations", "rationale"],
    "properties": {
        "label": {"type": "string", "description": "short unique candidate name, e.g. v2-A34K"},
        "mutations": {
            "type": "array",
            "items": {"type": "string", "pattern": "^[A-Z][0-9]+[A-Z]$"},
            "description": "mutations relative to the parent sequence, e.g. ['A34K','L88V']",
        },
        "sequence": {
            "type": "string",
            "description": "optional full sequence when the design is not expressible as point mutations",
        },
        "rationale": {"type": "string", "description": "why this design should work"},
        "expected_effects": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "objective -> expected direction, e.g. {'binding':'improve'}",
        },
        "citations": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": False,
}

PLAN_SCHEMA = {
    "type": "object",
    "required": ["strategy", "agents"],
    "properties": {
        "strategy": {"type": "string", "description": "the plan for this design cycle"},
        "hypotheses": {"type": "array", "items": {"type": "string"}},
        "agents": {
            "type": "array",
            "minItems": 1,
            "maxItems": 6,
            "items": {
                "type": "object",
                "required": ["role", "task"],
                "properties": {
                    "role": {"type": "string", "enum": list(ROLES)},
                    "task": {"type": "string"},
                    "acu_limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "focus_regions": {"type": "array", "items": {"type": "integer"}},
                    "must_avoid": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        },
        "success_criteria": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "shortlist_size": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "additionalProperties": False,
}

CANDIDATE_LIST_SCHEMA = {
    "type": "object",
    "required": ["candidates"],
    "properties": {
        "candidates": {"type": "array", "minItems": 1, "maxItems": 12, "items": CANDIDATE_SCHEMA},
        "analysis": {"type": "string"},
        "hotspots": {"type": "array", "items": {"type": "integer"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

LITERATURE_SCHEMA = {
    "type": "object",
    "required": ["findings"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["claim", "citation"],
                "properties": {
                    "claim": {"type": "string"},
                    "citation": {"type": "string"},
                    "implication": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "recommended_mutations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["mutation", "rationale"],
                "properties": {
                    "mutation": {"type": "string"},
                    "rationale": {"type": "string"},
                    "citation": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "analysis": {"type": "string"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

RANKING_SCHEMA = {
    "type": "object",
    "required": ["ordering"],
    "properties": {
        "ordering": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["label", "reason"],
                "properties": {
                    "label": {"type": "string"},
                    "reason": {"type": "string"},
                    "risk": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "exclusions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["target", "reason"],
                "properties": {
                    "target": {"type": "string", "description": "label or mutation set"},
                    "reason": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "shortlist_size": {"type": "integer", "minimum": 1, "maximum": 20},
        "analysis": {"type": "string"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

ROLE_SCHEMAS = {
    "sequence": CANDIDATE_LIST_SCHEMA,
    "structure": CANDIDATE_LIST_SCHEMA,
    "docking": CANDIDATE_LIST_SCHEMA,
    "literature": LITERATURE_SCHEMA,
    "ranking": RANKING_SCHEMA,
}


def schema_for(role: str) -> dict:
    return ROLE_SCHEMAS.get(role, CANDIDATE_LIST_SCHEMA)
