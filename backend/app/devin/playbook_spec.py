"""Shared playbook value type, imported by both the protein-design and Pharmakon playbooks."""

from __future__ import annotations

from dataclasses import dataclass

SLUG_PREFIX = "dyb-pro"


@dataclass(frozen=True)
class PlaybookSpec:
    slug: str
    title: str
    body: str
