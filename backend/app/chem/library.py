"""Deterministic analog enumeration: substituent scans, bioisosteres and scaffold decoration.

Every candidate is generated as a SMILES edit and then *validated by re-parsing*: the
edit is only kept when exactly one hydrogen was replaced (or the fragment swap balances),
so the library never contains over-valent nonsense.
"""

from __future__ import annotations

from itertools import product

from app.chem.constants import BIOISOSTERES, R_GROUPS
from app.chem.smiles import Molecule, SmilesError, molecule_hash, parse_smiles

SCAN_GROUPS = ("F", "Cl", "Me", "OMe", "CF3", "CN", "OH", "NH2")


def _counts(mol: Molecule) -> tuple[int, int]:
    return mol.heavy_atom_count, mol.hydrogen_count


def _fragment_counts(fragment: str) -> tuple[int, int] | None:
    if fragment == "":
        return (0, 0)
    try:
        frag = parse_smiles(fragment)
    except SmilesError:
        return None
    return _counts(frag)


def substituent_scan(
    parent_smiles: str, groups: tuple[str, ...] = SCAN_GROUPS
) -> list[dict]:
    """Add each R-group at every aromatic C-H position (the classic SAR scan)."""
    parent = parse_smiles(parent_smiles)
    p_heavy, p_h = _counts(parent)
    out: list[dict] = []
    for name in groups:
        fragment = R_GROUPS.get(name)
        if fragment is None:
            continue
        frag_counts = _fragment_counts(fragment)
        if frag_counts is None or fragment == "":
            continue
        f_heavy, f_h = frag_counts
        for pos in _aromatic_positions(parent_smiles):
            candidate = f"{parent_smiles[:pos + 1]}({fragment}){parent_smiles[pos + 1:]}"
            mol = _validate(candidate, p_heavy + f_heavy, p_h + f_h - 1)
            if mol is None:
                continue
            out.append(
                {
                    "smiles": candidate,
                    "transform": f"aromatic C-H -> {name}",
                    "rationale": f"substituent scan at ring position offset {pos}",
                    "position": pos,
                }
            )
    return out


def _aromatic_positions(smiles: str) -> list[int]:
    """Offsets *after* each aromatic atom token (ring-closure digits included).

    Inserting after the ring-bond digits keeps the emitted SMILES canonical-friendly:
    ``c1ccccc1`` becomes ``c1(F)ccccc1`` rather than ``c(F)1ccccc1``.
    """
    positions: list[int] = []
    i = 0
    while i < len(smiles):
        ch = smiles[i]
        if ch == "[":  # bracket atoms are explicit; never touched by the scan
            i = smiles.find("]", i) + 1 or len(smiles)
            continue
        if ch in "cn" and smiles[i : i + 2] not in ("cl", "Cl"):
            end = i + 1
            while end < len(smiles) and (smiles[end].isdigit() or smiles[end] == "%"):
                end += 3 if smiles[end] == "%" else 1
            positions.append(end - 1)
        i += 1
    return positions


def bioisosteric_analogs(parent_smiles: str) -> list[dict]:
    """Apply classical bioisosteric swaps where the fragment is present."""
    parent = parse_smiles(parent_smiles)
    out: list[dict] = []
    for old, new, rationale in BIOISOSTERES:
        if old not in parent_smiles:
            continue
        old_counts = _fragment_counts(old)
        new_counts = _fragment_counts(new)
        if old_counts is None or new_counts is None:
            continue
        candidate = parent_smiles.replace(old, new, 1)
        p_heavy, p_h = _counts(parent)
        expected_heavy = p_heavy - old_counts[0] + new_counts[0]
        mol = _validate(candidate, expected_heavy, None)
        if mol is None:
            continue
        out.append(
            {
                "smiles": candidate,
                "transform": f"{old} -> {new}",
                "rationale": rationale,
                "position": parent_smiles.find(old),
            }
        )
    return out


def decorate_scaffold(template: str, r_groups: dict[str, list[str]]) -> list[dict]:
    """Enumerate a scaffold written with ``{R1}``-style placeholders.

    ``decorate_scaffold("c1ccc({R1})cc1{R2}", {"R1": ["F", "Cl"], "R2": ["C"]})``
    """
    keys = sorted(r_groups)
    out: list[dict] = []
    for combo in product(*(r_groups[k] for k in keys)):
        candidate = template
        for key, value in zip(keys, combo, strict=True):
            candidate = candidate.replace("{" + key + "}", value)
        if "{" in candidate:
            continue
        try:
            parse_smiles(candidate)
        except SmilesError:
            continue
        out.append(
            {
                "smiles": candidate,
                "transform": ", ".join(f"{k}={v or 'H'}" for k, v in zip(keys, combo, strict=True)),
                "rationale": "scaffold decoration",
                "position": -1,
            }
        )
    return out


def _validate(candidate: str, expected_heavy: int | None, expected_h: int | None) -> Molecule | None:
    try:
        mol = parse_smiles(candidate)
    except SmilesError:
        return None
    heavy, hydrogens = _counts(mol)
    if expected_heavy is not None and heavy != expected_heavy:
        return None
    if expected_h is not None and hydrogens != expected_h:
        return None
    return mol


def enumerate_analogs(parent_smiles: str, max_analogs: int = 24) -> list[dict]:
    """Combined, de-duplicated and deterministically ordered analog library."""
    parent_hash = molecule_hash(parent_smiles)
    seen = {parent_hash}
    analogs: list[dict] = []
    for candidate in [*bioisosteric_analogs(parent_smiles), *substituent_scan(parent_smiles)]:
        digest = molecule_hash(candidate["smiles"])
        if digest in seen:
            continue
        seen.add(digest)
        analogs.append({**candidate, "molecule_hash": digest, "parent_smiles": parent_smiles})
    analogs.sort(key=lambda a: (a["transform"], a["position"], a["smiles"]))
    return analogs[:max_analogs]
