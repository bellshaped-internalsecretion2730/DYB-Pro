"""Coarse rigid-body docking and geometry minimization at CA resolution.

The interaction model is a soft Lennard-Jones term plus a Debye-screened electrostatic term over
inter-chain CA pairs, sampled over a deterministic ensemble of rigid-body placements.

What this is not:
  * not a binding free energy — the score is in arbitrary units and cannot be converted to a KD;
  * not molecular dynamics — `minimize_geometry` is steepest descent on a soft potential, with no
    time integration, thermostat, solvent or force field;
  * not a pose prediction — CAPRI assessments show even full-atom docking rarely produces a
    correct interface without restraints, and this model has no side chains at all.

It is a fast, reproducible ranking signal and the seam where a real engine plugs in.
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
            "unit": "arbitrary units (uncalibrated)",
            "uncertainty": self.uncertainty,
            "uncertainty_meaning": "spread of the top poses; sampling noise, not a confidence interval",
            "interface_residues": self.interface_residues[:40],
            "contacts": self.contacts,
            "buried_apolar_fraction": self.buried_apolar_fraction,
            "poses": self.poses,
            "method": self.method,
            "interpretation": (
                "orders candidates against one fixed receptor; not a binding affinity, not "
                "convertible to KD, and the pose itself is unvalidated"
            ),
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


def _extent(coords, center) -> float:
    """Radius of the sphere enclosing `coords` around `center`."""
    return max((_dist(c, center) for c in coords), default=0.0)


def dock(
    ligand: Structure,
    receptor: Structure,
    poses: int = 24,
    seed: int = 20240917,
    separation: float | None = None,
) -> DockingResult:
    """Rigid-body ensemble docking. Deterministic for a given (inputs, seed).

    The centre-to-centre separation defaults to the two molecular radii plus a contact gap. A
    fixed separation only produces contacts for molecules of one particular size: too small and
    every pose clashes, too large and no pose touches, either way the score stops depending on
    the sequence. Each pose is then slid along the approach vector to its own best separation.
    """
    rng = random.Random(seed)
    lig_center = _center(ligand.coords)
    rec_center = _center(receptor.coords)
    centered = [(c[0] - lig_center[0], c[1] - lig_center[1], c[2] - lig_center[2]) for c in ligand.coords]
    if separation is None:
        separation = (
            _extent(ligand.coords, lig_center) + _extent(receptor.coords, rec_center) + 4.0
        )

    energies: list[float] = []
    best = (float("inf"), [], 0)
    for _ in range(poses):
        theta = rng.uniform(0, 2 * math.pi)
        phi = rng.uniform(0, math.pi)
        rotation = (rng.uniform(0, 2 * math.pi), rng.uniform(0, 2 * math.pi))
        axis = (
            math.sin(phi) * math.cos(theta),
            math.sin(phi) * math.sin(theta),
            math.cos(phi),
        )
        pose_best = (float("inf"), [], 0)
        for offset in (4.0, 2.0, 0.0, -2.0, -4.0, -6.0, -8.0):
            radius = separation + offset
            if radius <= 0:
                continue
            translation = (
                rec_center[0] + radius * axis[0],
                rec_center[1] + radius * axis[1],
                rec_center[2] + radius * axis[2],
            )
            moved = _transform(centered, translation, rotation)
            energy, interface, contacts = score_pose(ligand, receptor, moved)
            if energy < pose_best[0]:
                pose_best = (energy, interface, contacts)
        energies.append(pose_best[0])
        if pose_best[0] < best[0]:
            best = pose_best

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


def minimize_geometry(structure: Structure, steps: int = 60, seed: int = 7) -> dict:
    """Steepest-descent minimization of the CA trace on a soft geometric potential.

    Bonded (3.8 A CA-CA) + non-bonded soft repulsion only. This is geometry regularization, **not**
    molecular dynamics and not an energy in kcal/mol: there is no force field, solvent, temperature
    or time integration, so the reported energy only answers "is this trace geometrically strained?"
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
        "unit": "arbitrary units (uncalibrated)",
        "rmsd_to_input": round(rmsd, 3),
        "steps": steps,
        "method": "coarse CA steepest descent (bonded + soft repulsion)",
        "is_molecular_dynamics": False,
    }
