"""Structured-output schemas for the Pharmakon (drug-discovery) agent roles.

Small-molecule work needs different structure than protein design: agents return SMILES,
liability assessments, routes, dose proposals and IP positions. Every schema forces the agent
to attach a mechanism and, where it makes a factual claim about the world, a citation — so the
program record stays auditable and nothing enters a gate as unsourced prose.
"""

from __future__ import annotations

PHARMA_ROLES = (
    "program",  # program orchestrator: plans one stage round and fans out
    "target",  # target assessment: evidence + disease hypothesis
    "medchem",  # medicinal chemistry: proposes molecules
    "admet",  # ADME/physchem liabilities and fixes
    "tox",  # toxicology and safety pharmacology
    "dmpk",  # exposure, dose regimen, formulation
    "synthesis",  # route and CMC feasibility
    "ip",  # freedom to operate and claim strategy
    "clinical",  # first-in-human strategy
    "triage",  # candidate ranking and selection rationale
)

_CITATIONS = {"type": "array", "items": {"type": "string"}}
_CONFIDENCE = {"type": "number", "minimum": 0, "maximum": 1}

PROGRAM_PLAN_SCHEMA = {
    "type": "object",
    "required": ["strategy", "agents"],
    "properties": {
        "strategy": {"type": "string", "description": "plan for this stage round"},
        "hypotheses": {"type": "array", "items": {"type": "string"}},
        "gate_focus": {
            "type": "array",
            "items": {"type": "string"},
            "description": "gate metric keys this round must move, e.g. ['best_pkd']",
        },
        "agents": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "required": ["role", "task"],
                "properties": {
                    "role": {"type": "string", "enum": [r for r in PHARMA_ROLES if r != "program"]},
                    "task": {"type": "string"},
                    "acu_limit": {"type": "integer", "minimum": 1, "maximum": 30},
                    "must_avoid": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": False,
            },
        },
        "success_criteria": {"type": "array", "items": {"type": "string"}},
        "experiments_requested": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

MOLECULE_SCHEMA = {
    "type": "object",
    "required": ["label", "smiles", "rationale"],
    "properties": {
        "label": {"type": "string", "description": "short unique name, e.g. PK-3-amide"},
        "smiles": {"type": "string", "description": "valid SMILES for the proposed molecule"},
        "parent_smiles": {
            "type": "string",
            "description": "SMILES of the molecule this design modifies, if any",
        },
        "series": {"type": "string", "description": "chemical series or scaffold name"},
        "rationale": {"type": "string", "description": "SAR mechanism and expected trade-off"},
        "expected_effects": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "property -> expected direction, e.g. {'potency':'improve'}",
        },
        "citations": _CITATIONS,
        "confidence": _CONFIDENCE,
    },
    "additionalProperties": False,
}

MOLECULE_LIST_SCHEMA = {
    "type": "object",
    "required": ["molecules"],
    "properties": {
        "molecules": {"type": "array", "minItems": 1, "maxItems": 16, "items": MOLECULE_SCHEMA},
        "sar_analysis": {"type": "string"},
        "scaffold_hopping_ideas": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

TARGET_SCHEMA = {
    "type": "object",
    "required": ["target_evidence_score", "disease_hypothesis", "findings"],
    "properties": {
        "target_evidence_score": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "strength of target-disease linkage: genetic, functional, clinical",
        },
        "disease_hypothesis": {"type": "string"},
        "modality_rationale": {"type": "string"},
        "findings": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["claim", "citation"],
                "properties": {
                    "claim": {"type": "string"},
                    "citation": {"type": "string"},
                    "evidence_class": {
                        "type": "string",
                        "enum": ["genetic", "functional", "clinical", "pharmacological", "other"],
                    },
                    "implication": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "known_ligands": {"type": "array", "items": {"type": "string"}},
        "pocket_residues": {"type": "array", "items": {"type": "integer"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

LIABILITY_SCHEMA = {
    "type": "object",
    "required": ["assessments"],
    "properties": {
        "assessments": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["smiles", "liabilities"],
                "properties": {
                    "smiles": {"type": "string"},
                    "liabilities": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["issue", "severity"],
                            "properties": {
                                "issue": {"type": "string"},
                                "severity": {
                                    "type": "string",
                                    "enum": ["low", "medium", "high", "blocking"],
                                },
                                "mechanism": {"type": "string"},
                                "mitigation": {"type": "string"},
                                "citation": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "recommended_modifications": {"type": "array", "items": {"type": "string"}},
                    "confidence": _CONFIDENCE,
                },
                "additionalProperties": False,
            },
        },
        "analysis": {"type": "string"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

DMPK_SCHEMA = {
    "type": "object",
    "required": ["assessments"],
    "properties": {
        "assessments": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["smiles", "regimen"],
                "properties": {
                    "smiles": {"type": "string"},
                    "regimen": {"type": "string", "description": "e.g. 'oral, 150 mg once daily'"},
                    "clearance_band": {
                        "type": "string",
                        "enum": ["low", "moderate", "high", "unknown"],
                    },
                    "exposure_risks": {"type": "array", "items": {"type": "string"}},
                    "formulation_notes": {"type": "string"},
                    "dose_limiting_property": {"type": "string"},
                    "confidence": _CONFIDENCE,
                },
                "additionalProperties": False,
            },
        },
        "analysis": {"type": "string"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

SYNTHESIS_SCHEMA = {
    "type": "object",
    "required": ["routes"],
    "properties": {
        "routes": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["smiles", "steps"],
                "properties": {
                    "smiles": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "required": ["transformation"],
                            "properties": {
                                "transformation": {"type": "string"},
                                "reagents": {"type": "string"},
                                "expected_yield_pct": {
                                    "type": "number",
                                    "minimum": 0,
                                    "maximum": 100,
                                },
                                "risk": {"type": "string"},
                            },
                            "additionalProperties": False,
                        },
                    },
                    "building_blocks": {"type": "array", "items": {"type": "string"}},
                    "scale_up_feasibility": {"type": "number", "minimum": 0, "maximum": 1},
                    "confidence": _CONFIDENCE,
                },
                "additionalProperties": False,
            },
        },
        "analysis": {"type": "string"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

IP_SCHEMA = {
    "type": "object",
    "required": ["freedom_to_operate", "prior_art"],
    "properties": {
        "freedom_to_operate": {
            "type": "number",
            "minimum": 0,
            "maximum": 1,
            "description": "0 = fully blocked by prior art, 1 = clear composition-of-matter space",
        },
        "prior_art": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["reference", "relevance"],
                "properties": {
                    "reference": {"type": "string", "description": "patent number, DOI or URL"},
                    "relevance": {"type": "string"},
                    "claim_overlap": {
                        "type": "string",
                        "enum": ["none", "partial", "substantial", "blocking"],
                    },
                },
                "additionalProperties": False,
            },
        },
        "claim_strategy": {"type": "string"},
        "whitespace": {"type": "array", "items": {"type": "string"}},
        "caveat": {
            "type": "string",
            "description": "must state that this is not legal advice",
        },
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

CLINICAL_SCHEMA = {
    "type": "object",
    "required": ["indication", "patient_population", "endpoints"],
    "properties": {
        "indication": {"type": "string"},
        "patient_population": {"type": "string"},
        "starting_dose_mg": {"type": "number", "minimum": 0},
        "starting_dose_basis": {
            "type": "string",
            "description": "how the dose follows from exposure and safety margins",
        },
        "escalation_scheme": {"type": "string"},
        "endpoints": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "stopping_rules": {"type": "array", "items": {"type": "string"}},
        "biomarkers": {"type": "array", "items": {"type": "string"}},
        "competitive_context": {"type": "string"},
        "risks": {"type": "array", "items": {"type": "string"}},
        "citations": _CITATIONS,
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

TRIAGE_SCHEMA = {
    "type": "object",
    "required": ["ordering"],
    "properties": {
        "ordering": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["smiles", "reason"],
                "properties": {
                    "smiles": {"type": "string"},
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
                "required": ["smiles", "reason"],
                "properties": {
                    "smiles": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "recommended_candidate": {"type": "string", "description": "SMILES of the pick"},
        "recommended_backup": {"type": "string"},
        "gate_readiness": {
            "type": "string",
            "description": "why this set does or does not clear the current stage gate",
        },
        "analysis": {"type": "string"},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

PHARMA_ROLE_SCHEMAS: dict[str, dict] = {
    "program": PROGRAM_PLAN_SCHEMA,
    "target": TARGET_SCHEMA,
    "medchem": MOLECULE_LIST_SCHEMA,
    "admet": LIABILITY_SCHEMA,
    "tox": LIABILITY_SCHEMA,
    "dmpk": DMPK_SCHEMA,
    "synthesis": SYNTHESIS_SCHEMA,
    "ip": IP_SCHEMA,
    "clinical": CLINICAL_SCHEMA,
    "triage": TRIAGE_SCHEMA,
}
