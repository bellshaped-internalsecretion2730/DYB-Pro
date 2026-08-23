"""Evidence registry: what each heuristic is based on, how far it applies, and how wrong it is.

Every number a skill emits comes from one of five kinds of source, and the difference matters more
than the number itself:

``CALIBRATED``
    The quantity is computed by a published method *and* the published benchmark reports an error
    for that quantity, so our sd is the literature error (example: the isoelectric point, whose
    average error is 0.87 pH units).
``ANCHORED``
    The formula is published, but the constant we feed it (or the input it runs on) has no
    published calibration of its own, so the error bar comes from a benchmark of *comparable*
    methods rather than of this implementation (example: the ddG proxy, whose sd is the spread of
    published ddG predictors).
``POLICY``
    A project decision - a pass/fail gate, a deliberately conservative sd, a safety margin. No
    paper says "45 C"; the platform does, and says so.
``PROXY``
    An uncalibrated screening heuristic: hand-chosen weights, motif counts, arbitrary units. Useful
    to rank designs of the same protein, never to claim a physical value.
``EXACT``
    A closed-form mathematical result (error propagation, a conjugate update, ordinary least
    squares). There is nothing to calibrate: the only assumptions are the ones the formula makes,
    and those are stated in ``applicability``.

A record with no DOI is only legal for ``POLICY``, ``PROXY`` and ``EXACT``, and
``tests/test_evidence.py`` enforces that, plus the requirement that every DOI cited in code also
appears in ``SKILLS/*.md``.
"""

from __future__ import annotations

from dataclasses import dataclass

CALIBRATED = "literature-calibrated"
ANCHORED = "literature-anchored"
POLICY = "project-policy"
PROXY = "uncalibrated-proxy"
EXACT = "analytically-exact"

CALIBRATION_KINDS = (CALIBRATED, ANCHORED, POLICY, PROXY, EXACT)


@dataclass(frozen=True)
class Evidence:
    """One heuristic, one provenance record."""

    key: str
    """Stable identifier, e.g. ``"physics.predicted_tm"``."""
    claim: str
    """What we compute, in one line."""
    applicability: str
    """The population/conditions the source covers - outside this, the number is decoration."""
    error: str
    """Published error magnitude, or an explicit statement that none exists."""
    calibration: str
    """One of :data:`CALIBRATION_KINDS`."""
    source: str = ""
    """Short bibliographic string."""
    doi: str = ""
    """Bare DOI (no ``doi:`` prefix, no URL)."""
    reference_value: str = ""
    """A literature value a test can pin, when the source publishes one."""

    def __post_init__(self) -> None:
        if self.calibration not in CALIBRATION_KINDS:
            raise ValueError(f"{self.key}: unknown calibration kind {self.calibration!r}")
        if not self.doi and self.calibration in (CALIBRATED, ANCHORED):
            raise ValueError(f"{self.key}: {self.calibration} evidence needs a DOI")
        if not (self.claim and self.applicability and self.error):
            raise ValueError(f"{self.key}: claim, applicability and error are all required")

    def citation(self) -> str:
        """Human-readable citation line, DOI included when there is one."""
        if not self.source:
            return f"{self.claim} - {self.calibration}, no source"
        return f"{self.source}" + (f" (doi:{self.doi})" if self.doi else "")

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "claim": self.claim,
            "applicability": self.applicability,
            "error": self.error,
            "calibration": self.calibration,
            "source": self.source,
            "doi": self.doi,
            "reference_value": self.reference_value,
        }


_REGISTRY: dict[str, tuple[Evidence, ...]] = {}


def register(skill: str, records: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    keys = [r.key for r in records]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{skill}: duplicate evidence keys {sorted(keys)}")
    _REGISTRY[skill] = records
    return records


def for_skill(skill: str) -> tuple[Evidence, ...]:
    return _REGISTRY.get(skill, ())


def registry() -> dict[str, tuple[Evidence, ...]]:
    return dict(_REGISTRY)


def record(skill: str, key: str) -> Evidence:
    for item in for_skill(skill):
        if item.key == key:
            return item
    raise KeyError(f"{skill}: no evidence registered under {key!r}")


def citations(skill: str) -> list[str]:
    """Deduplicated citation lines for a skill, in registration order."""
    out: list[str] = []
    for item in for_skill(skill):
        line = item.citation()
        if line not in out:
            out.append(line)
    return out


def dois(skill: str) -> list[str]:
    out: list[str] = []
    for item in for_skill(skill):
        if item.doi and item.doi not in out:
            out.append(item.doi)
    return out


def provenance(skill: str) -> list[dict]:
    """Machine-readable evidence table, exposed alongside the skill catalog."""
    return [item.as_dict() for item in for_skill(skill)]
