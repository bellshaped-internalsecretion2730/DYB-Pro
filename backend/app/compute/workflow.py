"""Policy, provenance and artifact helpers for the GPU compute workflow."""

from __future__ import annotations

import hashlib
from typing import Literal

from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Artifact
from app.storage import store

ToolPolicy = Literal["off", "auto", "required"]
TOOLS = ("alphafold", "proteinmpnn")
DEFAULT_POLICIES: dict[str, ToolPolicy] = {"alphafold": "auto", "proteinmpnn": "auto"}


class WorkflowError(RuntimeError):
    """A required compute policy could not be satisfied."""


def normalize_policies(raw: dict | None) -> dict[str, ToolPolicy]:
    source = raw or {}
    policies: dict[str, ToolPolicy] = {}
    for tool in TOOLS:
        value = str(source.get(tool, DEFAULT_POLICIES[tool])).lower()
        policies[tool] = value if value in {"off", "auto", "required"} else "auto"  # type: ignore[assignment]
    return policies


def validate_required_providers(policies: dict[str, ToolPolicy], settings: Settings) -> None:
    missing = []
    if policies["alphafold"] == "required" and not settings.alphafold_api_url:
        missing.append("ALPHAFOLD_API_URL")
    if policies["proteinmpnn"] == "required" and not settings.proteinmpnn_api_url:
        missing.append("PROTEINMPNN_API_URL")
    if missing:
        raise WorkflowError("required compute provider is not configured: " + ", ".join(missing))


def input_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def store_compute_artifact(
    db: Session,
    *,
    project_id: str,
    cycle_id: str,
    tool: str,
    label: str,
    content: str,
    extension: str,
    content_type: str,
) -> Artifact:
    filename = f"{cycle_id[:8]}-{label}.{extension}"
    key = f"{project_id}/compute/{tool}/{filename}"
    stored = store.put(key, content.encode(), content_type=content_type)
    artifact = Artifact(
        project_id=project_id,
        kind="structure" if extension == "pdb" else "fasta",
        filename=filename,
        key=stored.key,
        backend=stored.backend,
        sha256=stored.sha256,
        size=stored.size,
        content_type=content_type,
    )
    db.add(artifact)
    db.flush()
    return artifact


def capability_status(settings: Settings) -> dict:
    return {
        "alphafold": {
            "configured": bool(settings.alphafold_api_url),
            "provider": "nvidia-bionemo-nim" if settings.alphafold_api_url else None,
            "model": settings.alphafold_model_version,
        },
        "proteinmpnn": {
            "configured": bool(settings.proteinmpnn_api_url),
            "provider": "nvidia-bionemo-nim" if settings.proteinmpnn_api_url else None,
            "model": settings.proteinmpnn_model_version,
        },
    }
