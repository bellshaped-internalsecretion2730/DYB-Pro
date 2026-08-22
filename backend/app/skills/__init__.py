"""Foldsmith repository skills.

Importing this package registers every skill, so ``skills.run("skill.physics", {...})`` works
anywhere in the app. Skills are the only sanctioned source of numbers: see
:func:`app.skills.base.require_skill_metrics`.
"""

from app.skills import (
    chemistry,
    drug_discovery,
    literature,
    mathematics,
    physics,
    wetlab_metrics,
)
from app.skills.base import (
    Metric,
    SkillError,
    SkillSpec,
    catalog,
    collect,
    get,
    metrics_from,
    registry,
    require_skill_metrics,
    run,
)

SKILL_NAMES = (
    "skill.literature",
    "skill.math",
    "skill.physics",
    "skill.chemistry",
    "skill.drug_discovery",
    "skill.wetlab_metrics",
)

__all__ = [
    "SKILL_NAMES",
    "Metric",
    "SkillError",
    "SkillSpec",
    "catalog",
    "chemistry",
    "collect",
    "drug_discovery",
    "get",
    "literature",
    "mathematics",
    "metrics_from",
    "physics",
    "registry",
    "require_skill_metrics",
    "run",
    "wetlab_metrics",
]
