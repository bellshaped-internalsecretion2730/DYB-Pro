"""Folding backends.

`FoldingBackend` is the seam where a real predictor (ESMFold, OpenFold, Boltz…) plugs in.
The default backend is a deterministic coarse geometric model: it threads the sequence onto
idealized secondary-structure geometry derived from Chou-Fasman propensities. It is explicitly
*not* a structure prediction — it is a reproducible scaffold so that a design without an
experimental structure still gets comparable geometric and docking features. Every structure
records its `source` so nothing downstream can mistake it for an experimental model.
"""

from __future__ import annotations

import math
from typing import Protocol

from app.toolkit.constants import HELIX_PROPENSITY, SHEET_PROPENSITY
from app.toolkit.sequence import clean_sequence
from app.toolkit.structure import Structure


class FoldingBackend(Protocol):
    name: str

    def fold(self, sequence: str, name: str = "design") -> Structure: ...


def predict_ss_string(sequence: str, window: int = 5) -> str:
    """Chou-Fasman style windowed assignment -> string of H/E/C."""
    seq = clean_sequence(sequence)
    out: list[str] = []
    half = window // 2
    for i in range(len(seq)):
        sub = seq[max(0, i - half) : i + half + 1]
        h = sum(HELIX_PROPENSITY[a] for a in sub) / len(sub)
        e = sum(SHEET_PROPENSITY[a] for a in sub) / len(sub)
        if h > 1.05 and h >= e:
            out.append("H")
        elif e > 1.05:
            out.append("E")
        else:
            out.append("C")
    return "".join(out)


MIN_CA_SEPARATION = 3.9
CA_BOND = 3.8


def _grid_neighbours(pts: list[list[float]], cell: float) -> list[tuple[int, int]]:
    """Candidate close pairs via a spatial hash, so clash relief stays near-linear."""
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for idx, p in enumerate(pts):
        key = (int(p[0] // cell), int(p[1] // cell), int(p[2] // cell))
        buckets.setdefault(key, []).append(idx)
    pairs: set[tuple[int, int]] = set()
    for (cx, cy, cz), members in buckets.items():
        near: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    near.extend(buckets.get((cx + dx, cy + dy, cz + dz), ()))
        for i in members:
            for j in near:
                if j > i + 1:
                    pairs.add((i, j))
    return sorted(pairs)


def relieve_clashes(
    coords: list[tuple[float, float, float]],
    min_dist: float = MIN_CA_SEPARATION,
    bond: float = CA_BOND,
) -> list[tuple[float, float, float]]:
    """Deterministically push apart clashing CA pairs while holding the chain bond length.

    A coarse model with overlapping backbone atoms produces meaningless burial and docking
    features, so the geometry is regularised before anything downstream reads it.
    """
    pts = [list(c) for c in coords]
    n = len(pts)
    if n < 3:
        return [tuple(p) for p in pts]
    iterations = max(20, min(150, 12000 // n))
    for _ in range(iterations):
        moved = False
        for i, j in _grid_neighbours(pts, min_dist):
            dx = pts[j][0] - pts[i][0]
            dy = pts[j][1] - pts[i][1]
            dz = pts[j][2] - pts[i][2]
            d = math.sqrt(dx * dx + dy * dy + dz * dz)
            if d >= min_dist:
                continue
            moved = True
            if d < 1e-6:  # exactly superposed: separate along a deterministic axis
                dx, dy, dz, d = 1.0, 0.0, 0.0, 1.0
            push = 0.5 * (min_dist - d) / d
            for k, delta in enumerate((dx, dy, dz)):
                pts[i][k] -= delta * push
                pts[j][k] += delta * push
        # restore backbone bond lengths after the pushes
        for _pass in range(3):
            for i in range(n - 1):
                dx = pts[i + 1][0] - pts[i][0]
                dy = pts[i + 1][1] - pts[i][1]
                dz = pts[i + 1][2] - pts[i][2]
                d = math.sqrt(dx * dx + dy * dy + dz * dz) or 1e-6
                corr = 0.5 * (d - bond) / d
                for k, delta in enumerate((dx, dy, dz)):
                    pts[i][k] += delta * corr
                    pts[i + 1][k] -= delta * corr
        if not moved:
            break
    return [(round(p[0], 3), round(p[1], 3), round(p[2], 3)) for p in pts]


class CoarseGeometricBackend:
    """Builds a CA trace: alpha-helices as helices, strands extended, coils as turns."""

    name = "coarse-geometric"

    HELIX_RADIUS = 2.3
    HELIX_RISE = 1.5
    HELIX_TWIST = math.radians(100.0)
    STRAND_RISE = 3.3
    COIL_RISE = 3.0

    def fold(self, sequence: str, name: str = "design") -> Structure:
        seq = clean_sequence(sequence)
        ss = predict_ss_string(seq)
        coords: list[tuple[float, float, float]] = []
        # walk along a slowly turning global axis so segments pack instead of extending forever
        pos = [0.0, 0.0, 0.0]
        direction = [1.0, 0.0, 0.0]
        turn = 0
        for i, state in enumerate(ss):
            if state == "H":
                angle = i * self.HELIX_TWIST
                offset = (
                    self.HELIX_RADIUS * math.cos(angle),
                    self.HELIX_RADIUS * math.sin(angle),
                    0.0,
                )
                step = self.HELIX_RISE
            elif state == "E":
                offset = (0.0, 0.9 * (-1) ** i, 0.0)
                step = self.STRAND_RISE
            else:
                offset = (0.0, 0.0, 0.9 * (-1) ** i)
                step = self.COIL_RISE
                turn += 1
            # rotate the walking direction on coil residues to fold the chain back on itself
            if state == "C" and turn % 4 == 0:
                theta = math.radians(72.0)
                dx, dy = direction[0], direction[1]
                direction[0] = dx * math.cos(theta) - dy * math.sin(theta)
                direction[1] = dx * math.sin(theta) + dy * math.cos(theta)
                direction[2] += 0.15
                norm = math.sqrt(sum(d * d for d in direction)) or 1.0
                direction = [d / norm for d in direction]
            pos = [pos[k] + direction[k] * step for k in range(3)]
            coords.append(
                (
                    round(pos[0] + offset[0], 3),
                    round(pos[1] + offset[1], 3),
                    round(pos[2] + offset[2], 3),
                )
            )
        return Structure(
            name=name,
            sequence=seq,
            coords=relieve_clashes(coords),
            source=f"model:{self.name}",
        )


class TemplateThreadingBackend:
    """Threads a design onto a parent/experimental CA trace (equal-length or aligned prefix)."""

    name = "template-threading"

    def __init__(self, template: Structure) -> None:
        self.template = template

    def fold(self, sequence: str, name: str = "design") -> Structure:
        seq = clean_sequence(sequence)
        coords = list(self.template.coords)
        if len(coords) >= len(seq):
            coords = coords[: len(seq)]
        else:
            fallback = CoarseGeometricBackend().fold(seq[len(coords) :], name=name)
            last = coords[-1]
            shift = (last[0] + 3.3, last[1], last[2])
            coords += [
                (c[0] + shift[0], c[1] + shift[1], c[2] + shift[2]) for c in fallback.coords
            ]
            coords = relieve_clashes(coords)
        return Structure(
            name=name,
            sequence=seq,
            coords=coords,
            source=f"model:{self.name}:{self.template.name}",
        )


def default_backend() -> FoldingBackend:
    return CoarseGeometricBackend()


def fold_sequence(sequence: str, template: Structure | None = None, name: str = "design") -> Structure:
    backend: FoldingBackend = (
        TemplateThreadingBackend(template) if template is not None else CoarseGeometricBackend()
    )
    return backend.fold(sequence, name=name)
