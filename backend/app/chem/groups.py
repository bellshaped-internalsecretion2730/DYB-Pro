"""Functional-group perception on the molecule graph.

Detectors are explicit graph queries rather than SMARTS so the toolkit stays
dependency-free. Every detector returns the matching atom indices, which lets the
alert/ADMET layers explain *where* a liability sits instead of only counting it.
"""

from __future__ import annotations

from app.chem.constants import HALOGENS
from app.chem.smiles import Molecule

Match = tuple[int, ...]


# --------------------------------------------------------------------------- primitives


def double_bonded(mol: Molecule, idx: int, element: str) -> list[int]:
    return [
        b.other(idx)
        for b in mol.bonds_of(idx)
        if b.order >= 2 and mol.atoms[b.other(idx)].element == element
    ]


def single_bonded(mol: Molecule, idx: int, element: str) -> list[int]:
    return [
        b.other(idx)
        for b in mol.bonds_of(idx)
        if b.order < 2 and mol.atoms[b.other(idx)].element == element
    ]


def neighbor_elements(mol: Molecule, idx: int) -> list[str]:
    return [mol.atoms[n].element for n in mol.neighbors(idx)]


def is_carbonyl(mol: Molecule, idx: int) -> bool:
    return mol.atoms[idx].element == "C" and bool(double_bonded(mol, idx, "O"))


def carbonyl_carbons(mol: Molecule) -> list[int]:
    return [a.index for a in mol.atoms if is_carbonyl(mol, a.index)]


def is_amide_nitrogen(mol: Molecule, idx: int) -> bool:
    atom = mol.atoms[idx]
    if atom.element != "N":
        return False
    return any(is_carbonyl(mol, n) or _is_thiocarbonyl(mol, n) for n in mol.neighbors(idx))


def _is_thiocarbonyl(mol: Molecule, idx: int) -> bool:
    return mol.atoms[idx].element == "C" and bool(double_bonded(mol, idx, "S"))


def is_sulfonyl_sulfur(mol: Molecule, idx: int) -> bool:
    return mol.atoms[idx].element == "S" and len(double_bonded(mol, idx, "O")) >= 2


def is_nitro_nitrogen(mol: Molecule, idx: int) -> bool:
    atom = mol.atoms[idx]
    if atom.element != "N":
        return False
    oxygens = [n for n in mol.neighbors(idx) if mol.atoms[n].element == "O"]
    if len(oxygens) < 2:
        return False
    order = sum(b.order for b in mol.bonds_of(idx) if mol.atoms[b.other(idx)].element == "O")
    return order >= 2.5


def is_basic_amine(mol: Molecule, idx: int) -> bool:
    """Aliphatic amine nitrogen that is protonated at physiological pH."""
    atom = mol.atoms[idx]
    if atom.element != "N" or atom.aromatic:
        return False
    if is_amide_nitrogen(mol, idx) or is_nitro_nitrogen(mol, idx):
        return False
    if any(is_sulfonyl_sulfur(mol, n) for n in mol.neighbors(idx)):
        return False
    if any(mol.atoms[n].aromatic for n in mol.neighbors(idx)):
        return False  # aniline-like, pKa ~5
    if any(b.order >= 2 for b in mol.bonds_of(idx)):
        return False
    return mol.degree(idx) <= 3


def is_acidic(mol: Molecule, idx: int) -> bool:
    """Ionised (anionic) at physiological pH: carboxylic acid, acyl sulfonamide, tetrazole."""
    atom = mol.atoms[idx]
    if atom.element == "O" and atom.charge < 0:
        return True
    if atom.element == "O" and atom.total_h and any(is_carbonyl(mol, n) for n in mol.neighbors(idx)):
        return True
    if atom.element == "N" and atom.total_h and any(is_sulfonyl_sulfur(mol, n) for n in mol.neighbors(idx)):
        return True
    if atom.element == "N" and atom.aromatic and atom.total_h:
        ring = next((r for r in mol.aromatic_rings() if idx in r), None)
        if ring and sum(1 for i in ring if mol.atoms[i].element == "N") >= 3:
            return True  # tetrazole / triazole NH
    return False


# --------------------------------------------------------------------------- detectors


def find_groups(mol: Molecule) -> dict[str, list[Match]]:
    """Map of functional-group name -> list of matched atom-index tuples."""
    g: dict[str, list[Match]] = {}

    def add(name: str, match: Match) -> None:
        g.setdefault(name, []).append(match)

    ring_sets = mol.rings
    aromatic_rings = mol.aromatic_rings()

    for atom in mol.atoms:
        i = atom.index
        el = atom.element

        if el == "C" and (oxo := double_bonded(mol, i, "O")):
            o = oxo[0]
            singles = single_bonded(mol, i, "O")
            nitrogens = single_bonded(mol, i, "N")
            carbons = single_bonded(mol, i, "C")
            halides = [n for n in mol.neighbors(i) if mol.atoms[n].element in HALOGENS]
            hydroxy = [x for x in singles if mol.atoms[x].total_h or mol.atoms[x].charge < 0]
            if hydroxy:
                add("carboxylic_acid", (i, o, hydroxy[0]))
            elif singles:
                add("ester", (i, o, singles[0]))
            elif nitrogens:
                n = nitrogens[0]
                add("amide", (i, o, n))
                if mol.atoms[n].total_h == 0 and mol.degree(n) >= 3:
                    add("tertiary_amide", (i, o, n))
            elif halides:
                add("acyl_halide", (i, o, halides[0]))
            elif atom.total_h:
                add("aldehyde", (i, o))
            elif len(carbons) >= 2:
                add("ketone", (i, o))
            if any(mol.atoms[c].aromatic for c in carbons):
                add("aryl_carbonyl", (i, o))
            if any(
                b.order >= 2 and mol.atoms[b.other(c)].element == "C"
                for c in carbons
                for b in mol.bonds_of(c)
                if b.other(c) != i
            ):
                add("michael_acceptor", (i, o))

        if el == "C" and double_bonded(mol, i, "S"):
            add("thiocarbonyl", (i,))

        if el == "C" and not atom.aromatic:
            for b in mol.bonds_of(i):
                other = b.other(i)
                if other < i:
                    continue
                if mol.atoms[other].element == "C" and not mol.atoms[other].aromatic:
                    if b.order == 2.0:
                        add("alkene", (i, other))
                    elif b.order == 3.0:
                        add("alkyne", (i, other))
            if (nn := double_bonded(mol, i, "N")) and not any(
                is_amide_nitrogen(mol, x) for x in nn
            ):
                n = nn[0]
                if len(single_bonded(mol, i, "N")) >= 2:
                    add("guanidine", (i, n))
                elif single_bonded(mol, i, "N"):
                    add("amidine", (i, n))
                else:
                    add("imine", (i, n))

        if el == "C" and any(b.order >= 3 and mol.atoms[b.other(i)].element == "N" for b in mol.bonds_of(i)):
            add("nitrile", (i,))

        if el == "N":
            if is_nitro_nitrogen(mol, i):
                add("nitro", (i,))
                if any(mol.atoms[n].aromatic for n in mol.neighbors(i)):
                    add("nitroaromatic", (i,))
            elif atom.aromatic:
                add("aromatic_n", (i,))
            else:
                heavy = mol.degree(i)
                if is_amide_nitrogen(mol, i):
                    pass
                elif any(is_sulfonyl_sulfur(mol, n) for n in mol.neighbors(i)):
                    add("sulfonamide_n", (i,))
                elif any(mol.atoms[n].aromatic for n in mol.neighbors(i)):
                    add("aniline", (i,))
                elif atom.charge > 0 and heavy == 4:
                    add("quaternary_ammonium", (i,))
                elif heavy == 1:
                    add("primary_amine", (i,))
                elif heavy == 2:
                    add("secondary_amine", (i,))
                elif heavy == 3:
                    add("tertiary_amine", (i,))
                nn = [n for n in mol.neighbors(i) if mol.atoms[n].element == "N"]
                for n in nn:
                    if n > i:
                        bond = mol.bond_between(i, n)
                        if bond and bond.order == 1.0:
                            add("hydrazine", (i, n))
                        elif bond and bond.order == 2.0:
                            add("azo", (i, n))
            if len([n for n in mol.neighbors(i) if mol.atoms[n].element == "N"]) and any(
                b.order >= 3 for n in mol.neighbors(i) for b in mol.bonds_of(n)
            ):
                nn = [n for n in mol.neighbors(i) if mol.atoms[n].element == "N"]
                for n in nn:
                    if any(
                        b.order >= 3 and mol.atoms[b.other(n)].element == "N" for b in mol.bonds_of(n)
                    ):
                        add("azide", (i, n))
            if double_bonded(mol, i, "C") and any(
                double_bonded(mol, c, "O") for c in double_bonded(mol, i, "C")
            ):
                add("isocyanate", (i,))
            if is_basic_amine(mol, i):
                add("basic_center", (i,))

        if el == "O":
            if atom.total_h and not any(is_carbonyl(mol, n) for n in mol.neighbors(i)):
                if any(mol.atoms[n].aromatic for n in mol.neighbors(i)):
                    add("phenol", (i,))
                else:
                    add("alcohol", (i,))
            elif mol.degree(i) == 2 and not atom.aromatic:
                if all(mol.atoms[n].element == "C" and not is_carbonyl(mol, n) for n in mol.neighbors(i)):
                    add("ether", (i,))
            elif atom.aromatic:
                add("aromatic_o", (i,))

        if el == "S":
            if is_sulfonyl_sulfur(mol, i):
                if single_bonded(mol, i, "N"):
                    add("sulfonamide", (i,))
                else:
                    add("sulfone", (i,))
            elif len(double_bonded(mol, i, "O")) == 1:
                add("sulfoxide", (i,))
            elif atom.total_h:
                add("thiol", (i,))
            elif atom.aromatic:
                add("thiophene_s", (i,))
            elif mol.degree(i) == 2:
                if any(mol.atoms[n].element == "S" for n in mol.neighbors(i)):
                    add("disulfide", (i,))
                else:
                    add("thioether", (i,))

        if el in HALOGENS:
            nbrs = mol.neighbors(i)
            if nbrs and mol.atoms[nbrs[0]].aromatic:
                add("aryl_halide", (i,))
            else:
                add("alkyl_halide", (i,))

        if is_acidic(mol, i):
            add("acidic_center", (i,))

    for ring in ring_sets:
        elements = [mol.atoms[i].element for i in ring]
        if len(ring) == 3 and elements.count("O") == 1:
            add("epoxide", tuple(sorted(ring)))
        if len(ring) == 3 and elements.count("N") == 1:
            add("aziridine", tuple(sorted(ring)))
        if len(ring) >= 12:
            add("macrocycle", tuple(sorted(ring)))
        if len(ring) == 6 and ring not in aromatic_rings:
            oxo = [i for i in ring if is_carbonyl(mol, i)]
            if len(oxo) >= 2:
                add("quinone", tuple(sorted(ring)))

    for ring in aromatic_rings:
        hydroxyls = [i for i in ring if _has_ring_substituent(mol, i, "O", require_h=True)]
        if len(hydroxyls) >= 2:
            for a in hydroxyls:
                for b in hydroxyls:
                    if a < b and mol.bond_between(a, b) is not None:
                        add("catechol", tuple(sorted(ring)))
                        break
    if "catechol" in g:
        g["catechol"] = sorted(set(g["catechol"]))
    return g


def _has_ring_substituent(mol: Molecule, idx: int, element: str, require_h: bool = False) -> bool:
    for n in mol.neighbors(idx):
        atom = mol.atoms[n]
        if atom.element != element:
            continue
        if require_h and not atom.total_h:
            continue
        return True
    return False


def group_counts(mol: Molecule) -> dict[str, int]:
    return {name: len(matches) for name, matches in sorted(find_groups(mol).items())}
