"""Structure parsing and geometric descriptors (CA-level, dependency light)."""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field

THREE_TO_ONE = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E",
    "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F",
    "PRO": "P", "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V", "MSE": "M",
    "SEC": "C", "PYL": "K",
}


class StructureError(ValueError):
    pass


@dataclass
class Structure:
    """CA-trace representation: enough for coarse geometry, docking and viewing."""

    name: str
    sequence: str
    coords: list[tuple[float, float, float]]
    chain_ids: list[str] = field(default_factory=list)
    source: str = "parsed"

    def __post_init__(self) -> None:
        if len(self.sequence) != len(self.coords):
            raise StructureError("sequence/coordinate length mismatch")
        if not self.chain_ids:
            self.chain_ids = ["A"] * len(self.coords)

    def to_pdb(self) -> str:
        lines = [f"REMARK  DYB Pro CA trace ({self.source}) {self.name}"]
        for i, ((x, y, z), aa, chain) in enumerate(
            zip(self.coords, self.sequence, self.chain_ids, strict=False), start=1
        ):
            res = next((k for k, v in THREE_TO_ONE.items() if v == aa), "GLY")
            lines.append(
                f"ATOM  {i:5d}  CA  {res:>3s} {chain}{i:4d}    "
                f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00           C"
            )
        lines.append("END")
        return "\n".join(lines) + "\n"


def parse_pdb(text: str, name: str = "structure") -> Structure:
    seq: list[str] = []
    coords: list[tuple[float, float, float]] = []
    chains: list[str] = []
    seen: set[tuple[str, str]] = set()
    for line in text.splitlines():
        if not (line.startswith("ATOM") or line.startswith("HETATM")):
            continue
        if line[12:16].strip() != "CA":
            continue
        resname = line[17:20].strip().upper()
        if resname not in THREE_TO_ONE:
            continue
        chain = line[21].strip() or "A"
        resseq = line[22:27].strip()
        if (chain, resseq) in seen:
            continue
        seen.add((chain, resseq))
        try:
            xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        except ValueError as exc:
            raise StructureError(f"bad coordinate line: {line[:60]!r}") from exc
        seq.append(THREE_TO_ONE[resname])
        coords.append(xyz)
        chains.append(chain)
    if not coords:
        raise StructureError("no CA atoms found in PDB text")
    return Structure(name=name, sequence="".join(seq), coords=coords, chain_ids=chains)


def parse_cif(text: str, name: str = "structure") -> Structure:
    """mmCIF atom_site loop parser (CA atoms only)."""
    lines = text.splitlines()
    seq: list[str] = []
    coords: list[tuple[float, float, float]] = []
    chains: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "loop_":
            headers: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("_"):
                headers.append(lines[j].strip())
                j += 1
            if not any(h.startswith("_atom_site.") for h in headers):
                i = j
                continue
            idx = {h: k for k, h in enumerate(headers)}

            def col(field_name: str, columns: dict[str, int] = idx) -> int | None:
                return columns.get(f"_atom_site.{field_name}")

            c_atom, c_res = col("label_atom_id"), col("label_comp_id")
            c_chain = col("auth_asym_id") or col("label_asym_id")
            c_x, c_y, c_z = col("Cartn_x"), col("Cartn_y"), col("Cartn_z")
            if None in (c_atom, c_res, c_x, c_y, c_z):
                raise StructureError("mmCIF atom_site loop missing required columns")
            while j < len(lines) and lines[j].strip() and not lines[j].strip().startswith("#"):
                parts = lines[j].split()
                if len(parts) <= max(c_atom, c_res, c_x, c_y, c_z):
                    j += 1
                    continue
                if parts[c_atom].strip('"') == "CA":
                    resname = parts[c_res].strip('"').upper()
                    if resname in THREE_TO_ONE:
                        seq.append(THREE_TO_ONE[resname])
                        coords.append((float(parts[c_x]), float(parts[c_y]), float(parts[c_z])))
                        chains.append(parts[c_chain] if c_chain is not None else "A")
                j += 1
            i = j
        else:
            i += 1
    if not coords:
        raise StructureError("no CA atoms found in mmCIF text")
    return Structure(name=name, sequence="".join(seq), coords=coords, chain_ids=chains)


def load_structure(text: str, filename: str = "", name: str = "structure") -> Structure:
    lower = filename.lower()
    if lower.endswith(".cif") or lower.endswith(".mmcif") or "_atom_site." in text:
        return parse_cif(text, name=name)
    return parse_pdb(text, name=name)


# --------------------------------------------------------------------------- geometry


def _dist(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def radius_of_gyration(structure: Structure) -> float:
    n = len(structure.coords)
    cx = sum(c[0] for c in structure.coords) / n
    cy = sum(c[1] for c in structure.coords) / n
    cz = sum(c[2] for c in structure.coords) / n
    return round(math.sqrt(sum(_dist(c, (cx, cy, cz)) ** 2 for c in structure.coords) / n), 3)


def contact_map(structure: Structure, cutoff: float = 8.0) -> list[tuple[int, int, float]]:
    out: list[tuple[int, int, float]] = []
    coords = structure.coords
    for i in range(len(coords)):
        for j in range(i + 3, len(coords)):
            d = _dist(coords[i], coords[j])
            if d <= cutoff:
                out.append((i, j, round(d, 2)))
    return out


# Neighbour count within 10 A that a fully buried CA reaches in a well-packed globular domain.
# Used as a fixed reference so burial is comparable between designs instead of being rescaled to
# each structure's own maximum (which made the most buried residue of every model "fully buried").
REFERENCE_BURIED_NEIGHBOURS = 26


def burial(structure: Structure, cutoff: float = 10.0) -> list[int]:
    """Neighbour count per residue — a cheap, monotone proxy for burial. Not SASA."""
    coords = structure.coords
    counts = []
    for i, ci in enumerate(coords):
        counts.append(sum(1 for j, cj in enumerate(coords) if i != j and _dist(ci, cj) <= cutoff))
    return counts


def relative_exposure(structure: Structure) -> list[float]:
    """Per-residue exposure in [0, 1] from neighbour density, on a fixed reference scale.

    A rank proxy for solvent exposure, not a relative solvent accessibility: it has no atomic
    detail, no side chains and no probe radius.
    """
    counts = burial(structure)
    if not counts:
        return []
    ref = REFERENCE_BURIED_NEIGHBOURS
    return [round(max(0.0, min(1.0, 1.0 - c / ref)), 3) for c in counts]


def secondary_structure(structure: Structure) -> list[str]:
    """CA-geometry assignment (helix/strand/coil) following the i,i+3 / i,i+4 distance rules.

    Helix: d(i, i+3) ~ 5.0-5.6 A and d(i, i+4) ~ 6.0-6.5 A.
    Strand: extended, d(i, i+3) > 9 A — without backbone H-bonds this cannot tell a real beta
    strand from any extended segment, so "strand" here means "locally extended".
    """
    coords = structure.coords
    n = len(coords)
    ss = ["C"] * n
    for i in range(n - 4):
        d3 = _dist(coords[i], coords[i + 3])
        d4 = _dist(coords[i], coords[i + 4])
        if 4.5 <= d3 <= 6.2 and 5.0 <= d4 <= 7.2:
            for k in range(i, i + 5):
                ss[k] = "H"
        elif d3 > 9.0:
            for k in range(i, i + 4):
                if ss[k] == "C":
                    ss[k] = "E"
    return ss


def ss_fractions(structure: Structure) -> dict[str, float]:
    ss = secondary_structure(structure)
    n = max(1, len(ss))
    return {
        "helix": round(ss.count("H") / n, 4),
        "strand": round(ss.count("E") / n, 4),
        "coil": round(ss.count("C") / n, 4),
    }


def compactness(structure: Structure) -> float:
    """Rg divided by the empirical globular Rg ~ 2.2 * N^0.38 (Skolnick, 2011).

    ~1 = globular, >>1 = an extended chain whose burial and contact features are artifacts.
    """
    n = len(structure.coords)
    expected = 2.2 * (n**0.38)
    return round(radius_of_gyration(structure) / expected, 3)


def clash_count(structure: Structure, min_dist: float = 3.4) -> int:
    coords = structure.coords
    clashes = 0
    for i in range(len(coords)):
        for j in range(i + 2, len(coords)):
            if _dist(coords[i], coords[j]) < min_dist:
                clashes += 1
    return clashes


def is_model(structure: Structure) -> bool:
    """True when the coordinates were generated by Foldsmith rather than measured/uploaded."""
    return structure.source.startswith("model:") or structure.source.startswith("threaded:")


def geometry_usable(structure: Structure) -> bool:
    """Whether structure-derived features are worth reading at all.

    An extended or clash-ridden trace yields burial, contact and docking numbers that describe
    the generator, not the protein.
    """
    return compactness(structure) <= 1.6 and clash_count(structure) <= max(
        2, len(structure.coords) // 40
    )


def summary(structure: Structure) -> dict:
    return {
        "residues": len(structure.coords),
        "chains": sorted(set(structure.chain_ids)),
        "radius_of_gyration": radius_of_gyration(structure),
        "compactness": compactness(structure),
        "contacts": len(contact_map(structure)),
        "clashes": clash_count(structure),
        "secondary_structure": ss_fractions(structure),
        "source": structure.source,
        "is_model": is_model(structure),
        "geometry_usable": geometry_usable(structure),
        "caveat": (
            "CA-only geometry: descriptors are rank proxies, not SASA/DSSP/quality measures"
        ),
    }


def structure_to_bytes(structure: Structure) -> bytes:
    buf = io.StringIO()
    buf.write(structure.to_pdb())
    return buf.getvalue().encode("utf-8")
