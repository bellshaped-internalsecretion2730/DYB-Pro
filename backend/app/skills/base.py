"""Skill registry.

A *skill* is the only way a number enters Foldsmith's research record. Every skill declares a
pydantic input and output model (so its JSON Schema is machine-readable and testable) and stamps
every metric it emits with provenance: which skill, which version, which method, which citations.

Agents — including Devin child sessions — may not claim a metric that no skill produced:
:func:`require_skill_metrics` rejects unstamped numbers, so a hallucinated Tm cannot reach a
research event, a wet-lab plan or a commit.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError


class SkillError(RuntimeError):
    """Raised when a skill is misused: unknown name, invalid payload, unstamped metric."""


@dataclass(frozen=True)
class Metric:
    """One number plus everything needed to defend it later."""

    name: str
    value: float | bool | str | None
    unit: str
    method: str
    skill: str
    sd: float | None = None
    citations: tuple[str, ...] = ()
    notes: str = ""

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "sd": self.sd,
            "method": self.method,
            "skill": self.skill,
            "citations": list(self.citations),
            "notes": self.notes,
        }


@dataclass
class SkillSpec:
    name: str
    version: str
    summary: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    runner: Callable[[BaseModel], BaseModel]
    citations: list[str] = field(default_factory=list)

    def input_schema(self) -> dict:
        return self.input_model.model_json_schema()

    def output_schema(self) -> dict:
        return self.output_model.model_json_schema()


_REGISTRY: dict[str, SkillSpec] = {}


def register(spec: SkillSpec) -> SkillSpec:
    _REGISTRY[spec.name] = spec
    return spec


def get(name: str) -> SkillSpec:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise SkillError(f"unknown skill '{name}'; registered: {sorted(_REGISTRY)}") from exc


def registry() -> dict[str, SkillSpec]:
    return dict(_REGISTRY)


def catalog() -> list[dict]:
    """Machine-readable description of every skill, exposed over the API and used by prompts."""
    return [
        {
            "name": spec.name,
            "version": spec.version,
            "summary": spec.summary,
            "citations": spec.citations,
            "input_schema": spec.input_schema(),
            "output_schema": spec.output_schema(),
        }
        for spec in sorted(_REGISTRY.values(), key=lambda s: s.name)
    ]


def run(name: str, payload: dict | BaseModel) -> dict:
    """Validate the payload, run the skill, validate the result, return plain JSON."""
    spec = get(name)
    try:
        parsed = payload if isinstance(payload, spec.input_model) else spec.input_model(**dict(payload))
    except ValidationError as exc:
        raise SkillError(f"{name}: invalid input: {exc.errors()[:3]}") from exc
    result = spec.runner(parsed)
    if not isinstance(result, spec.output_model):
        raise SkillError(f"{name}: returned {type(result).__name__}, expected {spec.output_model.__name__}")
    out = result.model_dump()
    out["skill"] = spec.name
    out["skill_version"] = spec.version
    return out


def metrics_from(payload: dict) -> dict[str, dict]:
    """Pull the `metrics` block out of a skill result, keyed by metric name."""
    metrics = payload.get("metrics") or []
    if isinstance(metrics, dict):
        return {k: v for k, v in metrics.items() if isinstance(v, dict)}
    return {m["name"]: m for m in metrics if isinstance(m, dict) and m.get("name")}


def require_skill_metrics(metrics: dict[str, Any]) -> dict[str, dict]:
    """Reject any metric that is not stamped by a registered skill.

    This is the enforcement point behind "agents may only claim a metric if a skill produced it".
    """
    checked: dict[str, dict] = {}
    for name, metric in metrics.items():
        if not isinstance(metric, dict):
            raise SkillError(f"metric '{name}' is not a skill result (got {type(metric).__name__})")
        skill = metric.get("skill")
        if not skill:
            raise SkillError(f"metric '{name}' has no skill provenance and cannot be claimed")
        if skill not in _REGISTRY:
            raise SkillError(f"metric '{name}' claims unknown skill '{skill}'")
        if not metric.get("method"):
            raise SkillError(f"metric '{name}' has no method string")
        checked[name] = metric
    return checked


def collect(*metric_lists: list[Metric]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for metrics in metric_lists:
        for metric in metrics:
            out[metric.name] = metric.as_dict()
    return out
