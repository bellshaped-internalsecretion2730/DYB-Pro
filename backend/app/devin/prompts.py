"""Prompt construction for the orchestrator and child agents.

Prompts are deterministic functions of (brief, parent design, toolkit evidence, history digest) so
that every commit's `prompt` field is a faithful, reproducible record of what the agent was told.
"""

from __future__ import annotations

import json

TAG_PREFIX = "dyb-pro"


def tags_for(project_id: str, cycle_id: str, round_: int, role: str) -> list[str]:
    return [
        TAG_PREFIX,
        f"{TAG_PREFIX}:role:{role}",
        f"{TAG_PREFIX}:project:{project_id[:12]}",
        f"{TAG_PREFIX}:cycle:{cycle_id[:12]}",
        f"{TAG_PREFIX}:round:{round_}",
    ]


def _evidence_block(evidence: dict) -> str:
    return json.dumps(evidence, indent=2, default=str, sort_keys=True)


def orchestrator_prompt(
    *,
    brief: str,
    project_goal: str,
    parent: dict,
    evidence: dict,
    history: str,
    available_roles: list[str],
    shortlist_size: int,
    problem_spec: str | None = None,
    workflow_tools: dict[str, str] | None = None,
) -> str:
    tool_policies = workflow_tools or {}
    prompt = f"""\
You are the orchestrator of one in-silico protein design cycle for DYB Pro, a pre-wetlab design
platform. You plan the cycle and fan out to specialized child agents. You do not modify any code.

# Research goal (project)
{project_goal or "not specified"}

# Scientist's brief for this cycle
{brief}

# Parent design (the design this cycle mutates)
label: {parent.get("label")}
commit: {parent.get("commit_id")}
length: {parent.get("length")} residues
sequence:
{parent.get("sequence")}

# Deterministic in-silico evidence for the parent (computed by DYB Pro's toolkit)
{_evidence_block(evidence)}

# Project history and what has already been learned
{history}

# Your job
Produce a plan for this cycle. Choose 3-5 child agents from: {", ".join(available_roles)}.
Each child gets one specific, non-overlapping task written as an instruction to a specialist.
Include focus regions (1-based residue numbers) and things to avoid where the evidence supports it.
Target a wet-lab shortlist of about {shortlist_size} candidates.

# Allowlisted compute-tool policy
AlphaFold: {tool_policies.get("alphafold", "auto")}
ProteinMPNN: {tool_policies.get("proteinmpnn", "auto")}
These are backend tools, not child-agent roles. You must not claim that either tool ran unless the
evidence above contains a finished, hash-verified run. When ProteinMPNN is auto or required, assign
specific 1-based `focus_regions` to the relevant design agents; the backend will allow at most four
of those positions to vary and will validate every generated sequence.

Respond only with structured output matching the provided schema.
"""
    if problem_spec is not None:
        prompt += f"\n# Machine-checkable problem specification\n{problem_spec}\n"
    return prompt


def child_prompt(
    *,
    role: str,
    task: str,
    brief: str,
    strategy: str,
    parent: dict,
    evidence: dict,
    history: str,
    focus_regions: list[int],
    must_avoid: list[str],
    problem_spec: str | None = None,
) -> str:
    focus = ", ".join(str(r) for r in focus_regions) or "none specified"
    avoid = "; ".join(must_avoid) or "nothing specified beyond the exclusion list"
    prompt = f"""\
You are the DYB Pro **{role}** agent for one in-silico protein design cycle. Work only on your
assigned task. Do not modify any code repository.

# Cycle strategy (from the orchestrator)
{strategy}

# Scientist's brief
{brief}

# Your task
{task}

# Focus regions (1-based residue numbers)
{focus}

# Must avoid
{avoid}

# Parent design
label: {parent.get("label")}
length: {parent.get("length")} residues
sequence:
{parent.get("sequence")}

# Deterministic in-silico evidence (DYB Pro toolkit output for the parent)
{_evidence_block(evidence)}

# Project history, prior results and exclusion list
{history}

# Requirements
- Mutations use `A34K` syntax, 1-based, and the wild-type residue MUST match the parent sequence.
- Maximum 4 mutations per candidate.
- Every candidate needs a mechanistic rationale and the trade-off you expect.
- Do not re-propose anything on the exclusion list without justifying why the earlier failure does
  not apply.

Respond only with structured output matching the provided schema.
"""
    if problem_spec is not None:
        prompt += f"\n# Machine-checkable problem specification\n{problem_spec}\n"
    return prompt


def ranking_prompt(
    *,
    brief: str,
    strategy: str,
    candidates: list[dict],
    history: str,
    shortlist_size: int,
) -> str:
    return f"""\
You are the DYB Pro **ranking** agent. Triage the pooled candidate set into a wet-lab shortlist.

# Scientist's brief
{brief}

# Cycle strategy
{strategy}

# Candidates with deterministic toolkit scores and hard-filter results
{_evidence_block(candidates)}

# Project history
{history}

# Requirements
- Order candidates for wet-lab testing, rewarding orthogonal hypotheses over near-duplicates.
- Exclude dominated, redundant or hard-filter-failing candidates and name the reason.
- Recommend a shortlist size near {shortlist_size}, justified by information gained per unit cost.

Respond only with structured output matching the provided schema.
"""
