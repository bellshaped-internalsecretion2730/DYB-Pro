"""SMILES parsing, a light molecule graph, ring perception and content-addressed hashing.

The parser covers the subset of SMILES that medicinal-chemistry design work actually
emits: the organic subset, bracket atoms with isotope/charge/explicit H, aromatic
lower-case atoms, branches, ring-closure digits (including ``%nn``), bond symbols,
disconnected components and wildcard attachment points (``*``). Stereo markers are
parsed and counted (for stereocentre bookkeeping) but not used for connectivity.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from app.chem.constants import (
    AROMATIC_SUBSET,
    ATOMIC_WEIGHT,
    HETEROATOMS,
    ORGANIC_SUBSET,
    VALENCES,
)

BOND_ORDERS: dict[str, float] = {"-": 1.0, "=": 2.0, "#": 3.0, "$": 4.0, ":": 1.5, "/": 1.0, "\\": 1.0}
TWO_LETTER = ("Cl", "Br", "Si", "Se", "Na", "Mg", "Ca")


class SmilesError(ValueError):
    """Raised when a SMILES string cannot be parsed into a valid molecule."""


@dataclass
class Atom:
    index: int
    element: str
    aromatic: bool = False
    charge: int = 0
    isotope: int = 0
    explicit_h: int | None = None
    implicit_h: int = 0
    stereo: str = ""
    bracket: bool = False

    @property
    def total_h(self) -> int:
        return self.explicit_h if self.explicit_h is not None else self.implicit_h

    @property
    def is_hetero(self) -> bool:
        return self.element in HETEROATOMS

    @property
    def symbol(self) -> str:
        return self.element


@dataclass(frozen=True)
class Bond:
    a: int
    b: int
    order: float
    aromatic: bool = False

    def other(self, idx: int) -> int:
        return self.b if idx == self.a else self.a


@dataclass
class Molecule:
    smiles: str
    atoms: list[Atom] = field(default_factory=list)
    bonds: list[Bond] = field(default_factory=list)
    _adj: dict[int, list[Bond]] = field(default_factory=dict, repr=False)
    _rings: list[frozenset[int]] | None = field(default=None, repr=False)

    # ------------------------------------------------------------------ topology
    def bonds_of(self, idx: int) -> list[Bond]:
        return self._adj.get(idx, [])

    def neighbors(self, idx: int) -> list[int]:
        return [b.other(idx) for b in self.bonds_of(idx)]

    def degree(self, idx: int) -> int:
        return len(self.bonds_of(idx))

    def bond_order_sum(self, idx: int) -> float:
        return sum(b.order for b in self.bonds_of(idx))

    def bond_between(self, a: int, b: int) -> Bond | None:
        for bond in self.bonds_of(a):
            if bond.other(a) == b:
                return bond
        return None

    def element_count(self, element: str) -> int:
        return sum(1 for a in self.atoms if a.element == element)

    @property
    def heavy_atom_count(self) -> int:
        return sum(1 for a in self.atoms if a.element not in ("H", "*"))

    @property
    def hydrogen_count(self) -> int:
        return sum(a.total_h for a in self.atoms) + self.element_count("H")

    @property
    def formal_charge(self) -> int:
        return sum(a.charge for a in self.atoms)

    @property
    def stereocenters(self) -> int:
        return sum(1 for a in self.atoms if a.stereo)

    @property
    def components(self) -> int:
        seen: set[int] = set()
        count = 0
        for atom in self.atoms:
            if atom.index in seen:
                continue
            count += 1
            stack = [atom.index]
            while stack:
                cur = stack.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                stack.extend(self.neighbors(cur))
        return count

    # ------------------------------------------------------------------ rings
    @property
    def rings(self) -> list[frozenset[int]]:
        """Approximate SSSR: the smallest cycle through every ring bond."""
        if self._rings is None:
            self._rings = _perceive_rings(self)
        return self._rings

    @property
    def ring_count(self) -> int:
        """Cyclomatic ring count (bonds - atoms + components)."""
        return len(self.bonds) - len(self.atoms) + self.components

    def ring_atoms(self) -> set[int]:
        out: set[int] = set()
        for ring in self.rings:
            out |= set(ring)
        return out

    def in_ring(self, idx: int) -> bool:
        return idx in self.ring_atoms()

    def bond_in_ring(self, bond: Bond) -> bool:
        return any(bond.a in ring and bond.b in ring for ring in self.rings)

    def aromatic_rings(self) -> list[frozenset[int]]:
        return [r for r in self.rings if all(self.atoms[i].aromatic for i in r)]

    def carbocyclic_aromatic_rings(self) -> list[frozenset[int]]:
        return [r for r in self.aromatic_rings() if all(self.atoms[i].element == "C" for i in r)]

    # ------------------------------------------------------------------ misc
    @property
    def molecular_weight(self) -> float:
        mass = sum(ATOMIC_WEIGHT.get(a.element, 0.0) for a in self.atoms)
        mass += sum(a.total_h for a in self.atoms) * ATOMIC_WEIGHT["H"]
        return round(mass, 3)

    @property
    def formula(self) -> str:
        counts: dict[str, int] = {}
        for atom in self.atoms:
            if atom.element == "*":
                continue
            counts[atom.element] = counts.get(atom.element, 0) + 1
        h = sum(a.total_h for a in self.atoms)
        if h:
            counts["H"] = counts.get("H", 0) + h
        parts: list[str] = []
        for element in ("C", "H"):  # Hill order, then alphabetical
            n = counts.pop(element, 0)
            if n:
                parts.append(element if n == 1 else f"{element}{n}")
        for element in sorted(counts):
            n = counts[element]
            parts.append(element if n == 1 else f"{element}{n}")
        return "".join(parts)


# --------------------------------------------------------------------------- parsing


def parse_smiles(smiles: str) -> Molecule:
    """Parse a SMILES string into a :class:`Molecule`. Raises :class:`SmilesError`."""
    text = (smiles or "").strip()
    if not text:
        raise SmilesError("empty SMILES")
    mol = Molecule(smiles=text)
    branch_stack: list[int] = []
    ring_open: dict[int, tuple[int, float | None]] = {}
    prev: int | None = None
    pending_bond: float | None = None
    pending_aromatic = False
    i = 0
    n = len(text)

    def add_atom(atom: Atom) -> int:
        atom.index = len(mol.atoms)
        mol.atoms.append(atom)
        mol._adj.setdefault(atom.index, [])
        return atom.index

    def add_bond(a: int, b: int, order: float | None, aromatic: bool) -> None:
        if a == b:
            raise SmilesError("self bond")
        if mol.bond_between(a, b) is not None:
            raise SmilesError(f"duplicate bond between atoms {a} and {b}")
        if order is None:
            both_aromatic = mol.atoms[a].aromatic and mol.atoms[b].aromatic
            order = 1.5 if both_aromatic else 1.0
            aromatic = both_aromatic
        bond = Bond(a=a, b=b, order=order, aromatic=aromatic or order == 1.5)
        mol.bonds.append(bond)
        mol._adj[a].append(bond)
        mol._adj[b].append(bond)

    while i < n:
        ch = text[i]
        if ch == "[":
            end = text.find("]", i)
            if end == -1:
                raise SmilesError("unclosed '[' in SMILES")
            atom = _parse_bracket_atom(text[i + 1 : end])
            idx = add_atom(atom)
            if prev is not None:
                add_bond(prev, idx, pending_bond, pending_aromatic)
            prev, pending_bond, pending_aromatic = idx, None, False
            i = end + 1
            continue
        if ch == "(":
            if prev is None:
                raise SmilesError("branch opened before an atom")
            branch_stack.append(prev)
            i += 1
            continue
        if ch == ")":
            if not branch_stack:
                raise SmilesError("unbalanced ')' in SMILES")
            prev = branch_stack.pop()
            i += 1
            continue
        if ch in BOND_ORDERS:
            pending_bond = BOND_ORDERS[ch]
            pending_aromatic = ch == ":"
            i += 1
            continue
        if ch == ".":
            prev, pending_bond, pending_aromatic = None, None, False
            i += 1
            continue
        if ch == "%" or ch.isdigit():
            if ch == "%":
                if i + 2 >= n or not text[i + 1 : i + 3].isdigit():
                    raise SmilesError("malformed '%nn' ring closure")
                label = int(text[i + 1 : i + 3])
                i += 3
            else:
                label = int(ch)
                i += 1
            if prev is None:
                raise SmilesError("ring closure before an atom")
            if label in ring_open:
                partner, order = ring_open.pop(label)
                add_bond(partner, prev, order if order is not None else pending_bond, pending_aromatic)
            else:
                ring_open[label] = (prev, pending_bond)
            pending_bond, pending_aromatic = None, False
            continue
        if ch == "*":
            idx = add_atom(Atom(index=0, element="*", explicit_h=0))
            if prev is not None:
                add_bond(prev, idx, pending_bond, pending_aromatic)
            prev, pending_bond, pending_aromatic = idx, None, False
            i += 1
            continue
        two = text[i : i + 2]
        if two in TWO_LETTER:
            idx = add_atom(Atom(index=0, element=two))
            if prev is not None:
                add_bond(prev, idx, pending_bond, pending_aromatic)
            prev, pending_bond, pending_aromatic = idx, None, False
            i += 2
            continue
        if two == "se":
            idx = add_atom(Atom(index=0, element="Se", aromatic=True))
            if prev is not None:
                add_bond(prev, idx, pending_bond, pending_aromatic)
            prev, pending_bond, pending_aromatic = idx, None, False
            i += 2
            continue
        if ch.upper() in ORGANIC_SUBSET and (ch in ORGANIC_SUBSET or ch in AROMATIC_SUBSET):
            aromatic = ch.islower()
            idx = add_atom(Atom(index=0, element=ch.upper(), aromatic=aromatic))
            if prev is not None:
                add_bond(prev, idx, pending_bond, pending_aromatic)
            prev, pending_bond, pending_aromatic = idx, None, False
            i += 1
            continue
        raise SmilesError(f"unsupported SMILES character '{ch}' at position {i}")

    if branch_stack:
        raise SmilesError("unbalanced '(' in SMILES")
    if ring_open:
        raise SmilesError(f"unclosed ring bond(s): {sorted(ring_open)}")
    if not mol.atoms:
        raise SmilesError("no atoms parsed")
    _assign_implicit_hydrogens(mol)
    return mol


def _parse_bracket_atom(body: str) -> Atom:
    if not body:
        raise SmilesError("empty bracket atom '[]'")
    i = 0
    isotope = 0
    while i < len(body) and body[i].isdigit():
        isotope = isotope * 10 + int(body[i])
        i += 1
    if i >= len(body):
        raise SmilesError(f"bracket atom '[{body}]' has no element")
    element = ""
    if body[i : i + 2] in TWO_LETTER or body[i : i + 2] == "se":
        element = body[i : i + 2]
        i += 2
    elif body[i] == "*":
        element = "*"
        i += 1
    elif body[i].isalpha():
        element = body[i]
        i += 1
    else:
        raise SmilesError(f"bracket atom '[{body}]' has no element")
    aromatic = element[0].islower()
    element = element[0].upper() + element[1:].lower() if len(element) > 1 else element.upper()
    if element != "*" and element not in ATOMIC_WEIGHT:
        raise SmilesError(f"unknown element '{element}' in bracket atom '[{body}]'")
    atom = Atom(index=0, element=element, aromatic=aromatic, isotope=isotope, bracket=True)
    explicit_h = 0
    while i < len(body):
        ch = body[i]
        if ch == "@":
            atom.stereo = "@@" if body[i : i + 2] == "@@" else "@"
            i += 2 if atom.stereo == "@@" else 1
            continue
        if ch == "H":
            i += 1
            count = ""
            while i < len(body) and body[i].isdigit():
                count += body[i]
                i += 1
            explicit_h = int(count) if count else 1
            continue
        if ch in "+-":
            sign = 1 if ch == "+" else -1
            i += 1
            count = ""
            while i < len(body) and body[i].isdigit():
                count += body[i]
                i += 1
            if count:
                atom.charge += sign * int(count)
            else:
                repeats = 1
                while i < len(body) and body[i] == ch:
                    repeats += 1
                    i += 1
                atom.charge += sign * repeats
            continue
        if ch == ":":
            i += 1
            while i < len(body) and body[i].isdigit():
                i += 1
            continue
        raise SmilesError(f"unsupported token '{ch}' in bracket atom '[{body}]'")
    atom.explicit_h = explicit_h
    return atom


def _assign_implicit_hydrogens(mol: Molecule) -> None:
    for atom in mol.atoms:
        if atom.bracket or atom.element == "*":
            continue
        aromatic_atom = atom.aromatic or any(b.aromatic for b in mol.bonds_of(atom.index))
        if aromatic_atom:
            # Aromatic bonds are counted as single sigma bonds and one valence is reserved
            # for the delocalised pi system: benzene CH keeps one H, a fused carbon none.
            order_sum = int(
                round(sum(1.0 if b.aromatic else b.order for b in mol.bonds_of(atom.index)) + 1e-9)
            )
            reserved = 1
        else:
            order_sum = int(round(mol.bond_order_sum(atom.index) + 1e-9))
            reserved = 0
        valences = VALENCES.get(atom.element, (0,))
        target = valences[-1]
        for valence in valences:
            if valence >= order_sum:
                target = valence
                break
        atom.implicit_h = max(0, int(target - reserved + atom.charge - order_sum))


# --------------------------------------------------------------------------- rings


def _bridges(mol: Molecule) -> set[tuple[int, int]]:
    """Iterative Tarjan bridge finding; a bridge bond cannot be part of a ring."""
    disc: dict[int, int] = {}
    low: dict[int, int] = {}
    bridges: set[tuple[int, int]] = set()
    timer = 0
    for root in range(len(mol.atoms)):
        if root in disc:
            continue
        disc[root] = low[root] = timer
        timer += 1
        stack: list[tuple[int, int | None, list[int]]] = [(root, None, list(mol.neighbors(root)))]
        while stack:
            node, parent, pending = stack[-1]
            if pending:
                nxt = pending.pop()
                if nxt == parent:
                    continue
                if nxt in disc:
                    low[node] = min(low[node], disc[nxt])
                    continue
                disc[nxt] = low[nxt] = timer
                timer += 1
                stack.append((nxt, node, list(mol.neighbors(nxt))))
            else:
                stack.pop()
                if stack:
                    up = stack[-1][0]
                    low[up] = min(low[up], low[node])
                    if low[node] > disc[up]:
                        bridges.add((min(up, node), max(up, node)))
    return bridges


def _perceive_rings(mol: Molecule) -> list[frozenset[int]]:
    """Smallest cycle through each non-bridge bond (approximate SSSR)."""
    bridges = _bridges(mol)
    seen_pairs: set[tuple[int, int]] = set()
    rings: list[frozenset[int]] = []
    ring_keys: set[frozenset[int]] = set()
    for bond in mol.bonds:
        key = (min(bond.a, bond.b), max(bond.a, bond.b))
        if key in bridges or key in seen_pairs:
            continue
        seen_pairs.add(key)
        path = _shortest_path(mol, bond.a, bond.b, forbid=key)
        if not path:
            continue
        ring = frozenset(path)
        if len(ring) < 3 or ring in ring_keys:
            continue
        ring_keys.add(ring)
        rings.append(ring)
    rings.sort(key=lambda r: (len(r), sorted(r)))
    return rings


def _shortest_path(mol: Molecule, start: int, goal: int, forbid: tuple[int, int]) -> list[int]:
    from collections import deque

    queue: deque[list[int]] = deque([[start]])
    visited = {start}
    while queue:
        path = queue.popleft()
        node = path[-1]
        for bond in mol.bonds_of(node):
            nxt = bond.other(node)
            if (min(node, nxt), max(node, nxt)) == forbid:
                continue
            if nxt == goal:
                return [*path, goal]
            if nxt in visited:
                continue
            visited.add(nxt)
            queue.append([*path, nxt])
    return []


# --------------------------------------------------------------------------- hashing


def morgan_invariants(mol: Molecule, rounds: int = 3) -> list[str]:
    """Refined per-atom invariants (Morgan relaxation) used for graph-invariant hashing."""
    ring_atoms = mol.ring_atoms()
    current = [
        "|".join(
            (
                a.element,
                str(int(a.aromatic)),
                str(a.charge),
                str(mol.degree(a.index)),
                str(a.total_h),
                f"{mol.bond_order_sum(a.index):.1f}",
                str(int(a.index in ring_atoms)),
            )
        )
        for a in mol.atoms
    ]
    for _ in range(rounds):
        nxt: list[str] = []
        for atom in mol.atoms:
            env = sorted(
                f"{bond.order:.1f}:{current[bond.other(atom.index)]}" for bond in mol.bonds_of(atom.index)
            )
            digest = hashlib.sha1(f"{current[atom.index]}>{'/'.join(env)}".encode()).hexdigest()[:16]
            nxt.append(digest)
        current = nxt
    return current


def graph_key(mol: Molecule) -> str:
    """Canonical-ish, order-independent key for the molecular graph."""
    inv = morgan_invariants(mol)
    atom_part = ",".join(sorted(inv))
    bond_part = ",".join(
        sorted(
            "-".join(sorted((inv[b.a], inv[b.b]))) + f"#{b.order:.1f}" for b in mol.bonds
        )
    )
    return f"{mol.formula}|{atom_part}|{bond_part}"


def scaffold_atoms(mol: Molecule) -> set[int]:
    """Bemis-Murcko framework: iteratively strip terminal acyclic atoms.

    What remains is the ring systems plus the linkers between them, which is the working
    definition of a "chemical series" used by the program gates.
    """
    ring_atoms = mol.ring_atoms()
    if not ring_atoms:
        return set()
    keep = {a.index for a in mol.atoms}
    changed = True
    while changed:
        changed = False
        for idx in sorted(keep):
            if idx in ring_atoms:
                continue
            if sum(1 for n in mol.neighbors(idx) if n in keep) <= 1:
                keep.discard(idx)
                changed = True
    return keep


def scaffold_key(target: Molecule | str) -> str:
    """Order-independent identity of the molecule's Murcko framework.

    Molecules that share a framework share a series; acyclic molecules get their own key.
    """
    mol = target if isinstance(target, Molecule) else parse_smiles(target)
    keep = scaffold_atoms(mol)
    if not keep:
        return f"acyclic:{molecule_hash(mol)}"
    # Invariants are relaxed over the framework only: substituents outside it must not change
    # the key, otherwise every analog would look like its own series.
    bonds = [b for b in mol.bonds if b.a in keep and b.b in keep]
    ring_atoms = mol.ring_atoms()
    inv = {
        i: "|".join(
            (
                mol.atoms[i].element,
                str(int(mol.atoms[i].aromatic)),
                str(sum(1 for b in bonds if i in (b.a, b.b))),
                str(int(i in ring_atoms)),
            )
        )
        for i in keep
    }
    for _ in range(3):
        nxt: dict[int, str] = {}
        for i in keep:
            env = sorted(
                f"{b.order:.1f}:{inv[b.other(i)]}" for b in bonds if i in (b.a, b.b)
            )
            nxt[i] = hashlib.sha1(f"{inv[i]}>{'/'.join(env)}".encode()).hexdigest()[:16]
        inv = nxt
    atom_part = ",".join(sorted(inv.values()))
    bond_part = ",".join(
        sorted("-".join(sorted((inv[b.a], inv[b.b]))) + f"#{b.order:.1f}" for b in bonds)
    )
    return hashlib.sha256(f"{len(keep)}|{atom_part}|{bond_part}".encode()).hexdigest()[:24]


def molecule_hash(smiles: str) -> str:
    """Deterministic content address for a molecule (graph identity, not string identity)."""
    mol = smiles if isinstance(smiles, Molecule) else parse_smiles(smiles)
    return hashlib.sha256(graph_key(mol).encode()).hexdigest()[:40]


def is_valid_smiles(smiles: str) -> bool:
    try:
        parse_smiles(smiles)
    except SmilesError:
        return False
    return True
