"""Validation and classification for machine-checkable project objectives."""

from __future__ import annotations

import math
import re

from app.models import MeasuredResult, ProblemSpec
from app.services import calibration
from app.services.prices import TIERS
from app.services.ranking import DEFAULT_OBJECTIVES

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
KNOWN_READOUTS = frozenset().union(*(tier["decides"] for tier in TIERS.values()))
DIRECTIONS = {"lower_is_better", "higher_is_better"}


class SpecError(ValueError):
    """Raised when a ProblemSpec payload violates a declared rule."""


def _finite_number(value, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise SpecError(f"{label} must be a finite number")


def validate_spec(payload: dict) -> dict:
    """Validate a ProblemSpec payload without repairing or coercing any declared value."""
    if not isinstance(payload, dict):
        raise SpecError("problem spec must be an object")
    raw_objectives = payload.get("objectives")
    if not isinstance(raw_objectives, list) or not raw_objectives:
        raise SpecError("at least one objective is required")

    objectives: list[dict] = []
    names: set[str] = set()
    soft_weight = 0.0
    for index, raw in enumerate(raw_objectives):
        if not isinstance(raw, dict):
            raise SpecError(f"objective {index} must be an object")
        name = raw.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise SpecError(f"objective {index} name must match ^[a-z][a-z0-9_]{{1,31}}$")
        if name in names:
            raise SpecError(f"duplicate objective name: {name}")
        names.add(name)

        readout = raw.get("readout")
        if not isinstance(readout, str) or readout not in KNOWN_READOUTS:
            allowed = ", ".join(sorted(KNOWN_READOUTS))
            raise SpecError(f"unknown readout '{readout}'; allowed values: {allowed}")
        unit = raw.get("unit")
        if not isinstance(unit, str) or not unit.strip():
            raise SpecError(f"objective {name} unit must be non-empty")
        direction = raw.get("direction")
        if not isinstance(direction, str) or direction not in DIRECTIONS:
            raise SpecError(f"objective {name} direction must be lower_is_better or higher_is_better")
        threshold = raw.get("threshold")
        _finite_number(threshold, f"objective {name} threshold")

        must_pass = raw.get("must_pass", True)
        if not isinstance(must_pass, bool):
            raise SpecError(f"objective {name} must_pass must be boolean")
        weight = raw.get("weight", 0.0)
        _finite_number(weight, f"objective {name} weight")
        if not 0.0 <= weight <= 1.0:
            raise SpecError(f"objective {name} weight must be within [0.0, 1.0]")
        if not must_pass:
            soft_weight += weight

        proxy = raw.get("proxy")
        if proxy is not None and (
            not isinstance(proxy, str) or proxy not in DEFAULT_OBJECTIVES
        ):
            raise SpecError(f"unknown proxy '{proxy}'")
        assay_tiers = raw.get("assay_tiers")
        if not isinstance(assay_tiers, list) or not assay_tiers:
            raise SpecError(f"objective {name} assay_tiers must be non-empty")
        for tier in assay_tiers:
            if not isinstance(tier, str) or tier not in TIERS:
                raise SpecError(f"unknown assay tier '{tier}'")
            if readout not in TIERS[tier]["decides"]:
                raise SpecError(f"tier {tier} does not decide {readout}")

        objectives.append(
            {
                "name": name,
                "readout": readout,
                "unit": unit,
                "direction": direction,
                "threshold": threshold,
                "must_pass": must_pass,
                "weight": weight,
                "proxy": proxy,
                "proxy_calibrated": False,
                "assay_tiers": assay_tiers,
            }
        )
    if soft_weight > 1.0:
        raise SpecError("soft objective weights must sum to <= 1.0")

    deciding_objective = payload.get("deciding_objective")
    if not isinstance(deciding_objective, str) or deciding_objective not in names:
        raise SpecError(f"unknown deciding_objective: {deciding_objective}")
    deciding = next(item for item in objectives if item["name"] == deciding_objective)
    if not deciding["must_pass"]:
        raise SpecError("deciding_objective must have must_pass=true")

    hard_constraints = payload.get("hard_constraints", [])
    if not isinstance(hard_constraints, list):
        raise SpecError("hard_constraints must be a list")
    for index, constraint in enumerate(hard_constraints):
        if (
            not isinstance(constraint, dict)
            or not isinstance(constraint.get("name"), str)
            or not constraint["name"].strip()
            or not isinstance(constraint.get("description"), str)
            or not constraint["description"].strip()
        ):
            raise SpecError(f"hard constraint {index} requires non-empty name and description")

    notes = payload.get("notes", "")
    if not isinstance(notes, str):
        raise SpecError("notes must be a string")
    return {
        "objectives": objectives,
        "hard_constraints": hard_constraints,
        "deciding_objective": deciding_objective,
        "target_readout": deciding["readout"],
        "notes": notes,
    }


def _objective_for_result(spec: ProblemSpec, result: MeasuredResult) -> dict | None:
    for objective in spec.objectives or []:
        if result.objective == objective["name"]:
            return objective
    readout = (result.readout or "").strip().lower()
    for objective in spec.objectives or []:
        if readout == objective["readout"].lower():
            return objective
    return None


def classify(spec: ProblemSpec, result: MeasuredResult) -> dict:
    """Classify a result using inclusive threshold comparisons, never converting units."""
    objective = _objective_for_result(spec, result)
    if objective is None:
        return {
            "decision": "undecidable",
            "reason": f"readout '{result.readout}' is not declared in spec v{spec.version}",
            "objective": None,
            "spec_version": spec.version,
        }
    result_readout = (result.readout or "").strip().lower()
    if result.objective == objective["name"] and result_readout != objective["readout"].lower():
        return {
            "decision": "undecidable",
            "reason": (
                f"objective/readout conflict: result objective '{result.objective}' "
                f"carries readout '{result.readout}', spec objective '{objective['name']}' "
                f"declares '{objective['readout']}'"
            ),
            "objective": objective["name"],
            "spec_version": spec.version,
        }
    measured_unit = (result.unit or "").strip()
    declared_unit = objective["unit"]
    if measured_unit.lower() != declared_unit.strip().lower():
        return {
            "decision": "undecidable",
            "reason": f"unit mismatch: measured {measured_unit}, spec declares {declared_unit}",
            "objective": objective["name"],
            "spec_version": spec.version,
        }
    expected_higher = objective["direction"] == "higher_is_better"
    if bool(result.higher_is_better) != expected_higher:
        return {
            "decision": "undecidable",
            "reason": (
                "direction conflict: result says "
                f"{'higher_is_better' if result.higher_is_better else 'lower_is_better'}, "
                f"spec v{spec.version} declares {objective['direction']}"
            ),
            "objective": objective["name"],
            "spec_version": spec.version,
        }
    if not isinstance(result.value, int | float) or not math.isfinite(result.value):
        return {
            "decision": "undecidable",
            "reason": "non-finite measured value",
            "objective": objective["name"],
            "spec_version": spec.version,
        }
    threshold = objective["threshold"]
    hit = (
        result.value <= threshold
        if objective["direction"] == "lower_is_better"
        else result.value >= threshold
    )
    return {
        "decision": "hit" if hit else "miss",
        "reason": (
            f"{objective['name']} value {result.value} "
            f"{'<=' if objective['direction'] == 'lower_is_better' else '>='} "
            f"threshold {threshold}"
        ),
        "objective": objective["name"],
        "spec_version": spec.version,
    }


def prompt_block(spec: ProblemSpec, calibration_data: dict | None = None) -> str:
    """Render a compact objective block for agents without implying proxy validation."""
    calibration_data = calibration_data or {}
    calibrated = calibration_data.get("objectives", {})
    lines = [
        "OBJECTIVES (machine-checked; a design is a hit only if the deciding objective passes)"
    ]
    for objective in spec.objectives or []:
        direction = "<=" if objective["direction"] == "lower_is_better" else ">="
        proxy = objective["proxy"]
        proxy_text = "none"
        if proxy:
            live = calibrated.get(proxy)
            status = "calibrated" if live and live.get("kendall_tau") is not None else "uncalibrated"
            proxy_text = f"{proxy}, {status}"
        suffix = " [deciding]" if objective["name"] == spec.deciding_objective else ""
        lines.append(
            f"- {objective['name']}{suffix}: {objective['readout']} {direction} "
            f"{objective['threshold']} {objective['unit']}  (proxy: {proxy_text})"
        )
    if spec.hard_constraints:
        lines.append("HARD CONSTRAINTS (declared, not enforced in code)")
        for constraint in spec.hard_constraints:
            lines.append(f"- {constraint['name']}: {constraint['description']}")
    return "\n".join(lines)


def objectives_with_calibration(db, spec: ProblemSpec) -> list[dict]:
    """Return stored objectives annotated with live calibration data for API reads."""
    live = calibration.project_calibration(db, spec.project_id).get("objectives", {})
    out = []
    for objective in spec.objectives or []:
        item = dict(objective)
        item["proxy_calibration"] = live.get(objective["proxy"]) if objective.get("proxy") else None
        item["proxy_calibration_status"] = (
            "available" if item["proxy_calibration"] is not None else "no measured data"
        )
        out.append(item)
    return out
