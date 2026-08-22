"""Coarse rigid-body docking and a Langevin-style relaxation proxy.

This is a *coarse-grained* interaction model at CA resolution: a soft Lennard-Jones term plus a
Debye-screened electrostatic term over inter-chain CA pairs, sampled over a deterministic
ensemble of rigid-body perturbations. The ensemble spread is reported as the uncertainty of the
binding score. It replaces neither AutoDock nor GROMACS; it is a fast, reproducible ranking
signal and the seam where a real engine plugs in (`DockingBackend`).
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from app.toolkit.constants import CHARGE_AT_PH7, HYDROPATHY
from app.toolkit.structure import Structure, _dist

# Debye length in water at physiological ionic strength (~0.15 M) in angstrom.
DEBYE_LENGTH = 8.0
CONTACT_CUTOFF = 12.0


@dataclass
class DockingResult:
    binding_score: float
    uncertainty: float
    interface_residues: list[int]
    contacts: int
    buried_apolar_fraction: float
    poses: int
    method: str = "coarse CA Lennard-Jones + Debye electrostatics, rigid-body ensemble"

    def as_dict(self) -> dict:
        return {
            "binding_score": self.binding_score,
            "uncertainty": self.uncertainty,
            "interface_residues": self.interface_residues[:40],
            "contacts": self.contacts,
            "buried_apolar_fraction": self.buried_apolar_fraction,
            "poses": self.poses,
            "method": self.method,
        }


def _pair_energy(aa_a: str, aa_b: str, d: float) -> float:
    """Soft LJ (apolar attraction) + screened electrostatics. Negative = favourable."""
    if d < 2.0:
        return 25.0  # clash
    sigma = 6.0
    lj = ((sigma / d) ** 8) - 2 * ((sigma / d) ** 4)
    apolar = max(0.0, HYDROPATHY[aa_a]) * max(0.0, HYDROPATHY[aa_b]) / 20.25
    qa, qb = CHARGE_AT_PH7.get(aa_a, 0.0), CHARGE_AT_PH7.get(aa_b, 0.0)
    coulomb = 332.0 * qa * qb / (80.0 * d) * math.exp(-d / DEBYE_LENGTH)
    return 0.6 * lj * (0.4 + apolar) + 0.25 * coulomb


def _center(coords):
    n = len(coords)
    return (
        sum(c[0] for c in coords) / n,
        sum(c[1] for c in coords) / n,
        sum(c[2] for c in coords) / n,
    )


def _transform(coords, translation, rotation):
    ca, sa = math.cos(rotation[0]), math.sin(rotation[0])
    cb, sb = math.cos(rotation[1]), math.sin(rotation[1])
    out = []
    for x, y, z in coords:
        x1, y1 = x * ca - y * sa, x * sa + y * ca
        y2, z2 = y1 * cb - z * sb, y1 * sb + z * cb
        out.append((x1 + translation[0], y2 + translation[1], z2 + translation[2]))
    return out


def score_pose(ligand: Structure, receptor: Structure, ligand_coords) -> tuple[float, list[int], int]:
    energy = 0.0
    interface: set[int] = set()
    contacts = 0
    for i, lc in enumerate(ligand_coords):
        for j, rc in enumerate(receptor.coords):
            d = _dist(lc, rc)
            if d > CONTACT_CUTOFF:
                continue
            energy += _pair_energy(ligand.sequence[i], receptor.sequence[j], d)
            if d <= 8.0:
                interface.add(i)
                contacts += 1
    return energy, sorted(interface), contacts


def dock(
    ligand: Structure,
    receptor: Structure,
    poses: int = 24,
    seed: int = 20240917,
    separation: float = 14.0,
) -> DockingResult:
    """Rigid-body ensemble docking. Deterministic for a given (inputs, seed)."""
    rng = random.Random(seed)
    lig_center = _center(ligand.coords)
    rec_center = _center(receptor.coords)
    centered = [(c[0] - lig_center[0], c[1] - lig_center[1], c[2] - lig_center[2]) for c in ligand.coords]

    energies: list[float] = []
    best = (float("inf"), [], 0)
    for _ in range(poses):
        theta = rng.uniform(0, 2 * math.pi)
        phi = rng.uniform(0, math.pi)
        radius = separation + rng.uniform(-2.0, 2.0)
        translation = (
            rec_center[0] + radius * math.sin(phi) * math.cos(theta),
            rec_center[1] + radius * math.sin(phi) * math.sin(theta),
            rec_center[2] + radius * math.cos(phi),
        )
        rotation = (rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi))
        moved = _transform(centered, translation, rotation)
        energy, interface, contacts = score_pose(ligand, receptor, moved)
        energies.append(energy)
        if energy < best[0]:
            best = (energy, interface, contacts)

    energies.sort()
    top = energies[: max(3, poses // 4)]
    mean_top = sum(top) / len(top)
    spread = math.sqrt(sum((e - mean_top) ** 2 for e in top) / len(top))
    interface = best[1]
    apolar = sum(1 for i in interface if HYDROPATHY[ligand.sequence[i]] > 1.0)
    return DockingResult(
        binding_score=round(best[0], 2),
        uncertainty=round(spread, 2),
        interface_residues=[i + 1 for i in interface],
        contacts=best[2],
        buried_apolar_fraction=round(apolar / max(1, len(interface)), 3),
        poses=poses,
    )


def relax(structure: Structure, steps: int = 60, seed: int = 7) -> dict:
    """Coarse steepest-descent relaxation of the CA trace (MD proxy).

    Minimizes a bonded (3.8 A CA-CA) + non-bonded soft-repulsion potential and reports the energy
    drop and RMSD, giving a cheap "is this geometry strained?" signal.
    """
    rng = random.Random(seed)
    coords = [list(c) for c in structure.coords]
    start = [tuple(c) for c in coords]

    def energy(cs) -> float:
        e = 0.0
        for i in range(len(cs) - 1):
            d = math.dist(cs[i], cs[i + 1])
            e += 10.0 * (d - 3.8) ** 2
        for i in range(len(cs)):
            for j in range(i + 2, len(cs)):
                d = math.dist(cs[i], cs[j])
                if d < 4.0:
                    e += (4.0 - d) ** 2 * 5.0
        return e

    e0 = energy(coords)
    step = 0.25
    for _ in range(steps):
        improved = False
        for i in range(len(coords)):
            for axis in range(3):
                for delta in (step, -step):
                    coords[i][axis] += delta
                    e1 = energy(coords)
                    if e1 < e0 - 1e-9:
                        e0 = e1
                        improved = True
                    else:
                        coords[i][axis] -= delta
        if not improved:
            step *= 0.5
            if step < 0.02:
                break
        rng.random()  # keep the stream deterministic across versions
    rmsd = math.sqrt(sum(math.dist(a, b) ** 2 for a, b in zip(start, coords, strict=False)) / len(coords))
    return {
        "final_energy": round(e0, 3),
        "rmsd_to_input": round(rmsd, 3),
        "steps": steps,
        "method": "coarse CA steepest descent (bonded + soft repulsion)",
    }
