"""Sequence-level analysis: descriptors, alignment/homology, mutations, codon design."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from app.toolkit.constants import (
    AA,
    DIPEPTIDE_INSTABILITY,
    HELIX_PROPENSITY,
    HYDROPATHY,
    NEGATIVE,
    PKA_CTERM,
    PKA_NTERM,
    PKA_SIDE,
    POSITIVE,
    PREFERRED_CODONS,
    RESIDUE_MASS,
    SHEET_PROPENSITY,
    WATER_MASS,
)

MUTATION_RE = re.compile(r"^([A-Z])(\d+)([A-Z])$")


class SequenceError(ValueError):
    pass


def clean_sequence(seq: str) -> str:
    """Uppercase, strip whitespace/gaps and validate the alphabet."""
    cleaned = re.sub(r"[\s\-\*]", "", (seq or "")).upper()
    if not cleaned:
        raise SequenceError("empty sequence")
    bad = sorted(set(cleaned) - set(AA))
    if bad:
        raise SequenceError(f"non-standard residues: {''.join(bad)}")
    return cleaned


def parse_fasta(text: str) -> list[tuple[str, str]]:
    """Minimal FASTA parser -> [(header, sequence)]. Tolerates bare sequences."""
    records: list[tuple[str, str]] = []
    header: str | None = None
    chunks: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(">"):
            if header is not None and chunks:
                records.append((header, "".join(chunks)))
            header = line[1:].strip() or f"seq{len(records) + 1}"
            chunks = []
        else:
            chunks.append(line)
    if header is not None and chunks:
        records.append((header, "".join(chunks)))
    if not records and chunks:
        records.append(("seq1", "".join(chunks)))
    if not records:
        stripped = re.sub(r"\s", "", text or "")
        if stripped:
            records.append(("seq1", stripped))
    return [(h, clean_sequence(s)) for h, s in records]


def to_fasta(records: list[tuple[str, str]], width: int = 60) -> str:
    out: list[str] = []
    for header, seq in records:
        out.append(f">{header}")
        out.extend(seq[i : i + width] for i in range(0, len(seq), width))
    return "\n".join(out) + "\n"


def molecular_weight(seq: str) -> float:
    return round(sum(RESIDUE_MASS[a] for a in seq) + WATER_MASS, 2)


def gravy(seq: str) -> float:
    return round(sum(HYDROPATHY[a] for a in seq) / len(seq), 4)


def net_charge(seq: str, ph: float = 7.4) -> float:
    charge = 0.0
    for aa in POSITIVE:
        n = seq.count(aa)
        if n:
            charge += n * (1.0 / (1.0 + 10 ** (ph - PKA_SIDE[aa])))
    for aa in NEGATIVE:
        n = seq.count(aa)
        if n:
            charge -= n * (1.0 / (1.0 + 10 ** (PKA_SIDE[aa] - ph)))
    charge += 1.0 / (1.0 + 10 ** (ph - PKA_NTERM))
    charge -= 1.0 / (1.0 + 10 ** (PKA_CTERM - ph))
    return round(charge, 3)


def isoelectric_point(seq: str) -> float:
    lo, hi = 0.0, 14.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if net_charge(seq, mid) > 0:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 2)


def instability_index(seq: str) -> float:
    """Guruprasad et al. (1990) instability index; >40 flagged as unstable *in vivo*.

    Dipeptides missing from the published weight table contribute the neutral weight 1.0, which
    biases the index downward when coverage is low; `instability_coverage` reports how much of the
    sequence was actually covered.
    """
    if len(seq) < 2:
        return 0.0
    total = sum(DIPEPTIDE_INSTABILITY.get(seq[i : i + 2], 1.0) for i in range(len(seq) - 1))
    return round(10.0 / len(seq) * total, 2)


def instability_coverage(seq: str) -> float:
    """Fraction of dipeptides present in the published instability weight table."""
    if len(seq) < 2:
        return 0.0
    pairs = [seq[i : i + 2] for i in range(len(seq) - 1)]
    known = sum(1 for p in pairs if p in DIPEPTIDE_INSTABILITY)
    return round(known / len(pairs), 4)


def aromaticity(seq: str) -> float:
    return round(sum(seq.count(a) for a in "FWY") / len(seq), 4)


def extinction_coefficient(seq: str, disulfides: bool = True) -> int:
    """Molar extinction coefficient at 280 nm, Pace et al. (1995).

    `disulfides=True` assumes every cysteine pair forms a cystine (the 125 M-1cm-1 term); with
    fully reduced cysteines that term is zero. Which value applies depends on the redox state of
    the purified protein, so both are reported by `descriptors`.
    """
    cystine = 125 * (seq.count("C") // 2) if disulfides else 0
    return 5500 * seq.count("W") + 1490 * seq.count("Y") + cystine


def secondary_structure_propensity(seq: str) -> dict[str, float | str]:
    """Mean Chou-Fasman helix/sheet propensities (1.0 = average residue).

    These are composition averages, not predicted secondary-structure content: Chou-Fasman is a
    per-position method and even then only ~50-60% accurate. Earlier versions renormalized the two
    means into helix/sheet/coil "fractions" that summed to 1, which invented a coil term and made
    a composition average look like a structure prediction.
    """
    helix = sum(HELIX_PROPENSITY[a] for a in seq) / len(seq)
    sheet = sum(SHEET_PROPENSITY[a] for a in seq) / len(seq)
    return {
        "helix_propensity": round(helix, 4),
        "sheet_propensity": round(sheet, 4),
        "method": "mean Chou-Fasman propensity (composition average, not predicted content)",
    }


def composition(seq: str) -> dict[str, float]:
    return {a: round(seq.count(a) / len(seq), 4) for a in AA if seq.count(a)}


def hydrophobic_moment(seq: str, window: int = 18, angle: float = 100.0) -> float:
    """Eisenberg (1982) mean hydrophobic moment over the most amphipathic window."""
    if len(seq) < window:
        window = len(seq)
    best = 0.0
    rad = math.radians(angle)
    for start in range(0, len(seq) - window + 1):
        sub = seq[start : start + window]
        sin_sum = sum(HYDROPATHY[a] * math.sin(i * rad) for i, a in enumerate(sub))
        cos_sum = sum(HYDROPATHY[a] * math.cos(i * rad) for i, a in enumerate(sub))
        best = max(best, math.hypot(sin_sum, cos_sum) / window)
    return round(best, 4)


def descriptors(seq: str) -> dict:
    seq = clean_sequence(seq)
    return {
        "length": len(seq),
        "molecular_weight": molecular_weight(seq),
        "gravy": gravy(seq),
        "net_charge_ph74": net_charge(seq),
        "isoelectric_point": isoelectric_point(seq),
        "instability_index": instability_index(seq),
        "instability_coverage": instability_coverage(seq),
        "aromaticity": aromaticity(seq),
        "extinction_coefficient": extinction_coefficient(seq),
        "extinction_coefficient_reduced": extinction_coefficient(seq, disulfides=False),
        "hydrophobic_moment": hydrophobic_moment(seq),
        "secondary_structure": secondary_structure_propensity(seq),
        "composition": composition(seq),
    }


# --------------------------------------------------------------------------- mutations


@dataclass(frozen=True)
class Mutation:
    wt: str
    position: int  # 1-based
    mt: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.wt}{self.position}{self.mt}"


def parse_mutation(token: str) -> Mutation:
    m = MUTATION_RE.match(token.strip().upper())
    if not m:
        raise SequenceError(f"malformed mutation '{token}' (expected e.g. A123V)")
    wt, pos, mt = m.group(1), int(m.group(2)), m.group(3)
    if wt not in AA or mt not in AA:
        raise SequenceError(f"mutation '{token}' uses a non-standard residue")
    return Mutation(wt=wt, position=pos, mt=mt)


def apply_mutations(seq: str, mutations: list[str]) -> tuple[str, list[dict]]:
    """Apply `A123V`-style mutations. Returns (sequence, applied) and raises on mismatch."""
    seq = clean_sequence(seq)
    chars = list(seq)
    applied: list[dict] = []
    for token in mutations:
        mut = parse_mutation(token)
        idx = mut.position - 1
        if idx < 0 or idx >= len(chars):
            raise SequenceError(f"mutation {mut} outside sequence of length {len(chars)}")
        if chars[idx] != mut.wt:
            raise SequenceError(
                f"mutation {mut} expects {mut.wt} at {mut.position} but sequence has {chars[idx]}"
            )
        chars[idx] = mut.mt
        applied.append({"mutation": str(mut), "wt": mut.wt, "position": mut.position, "mt": mut.mt})
    return "".join(chars), applied


def diff_sequences(a: str, b: str) -> list[dict]:
    """Position-wise substitution list for equal-length sequences; otherwise aligned diff."""
    a, b = clean_sequence(a), clean_sequence(b)
    if len(a) != len(b):
        aln = align(a, b)
        a, b = aln.aligned_a, aln.aligned_b
    out: list[dict] = []
    pos = 0
    for ca, cb in zip(a, b, strict=False):
        if ca != "-":
            pos += 1
        if ca != cb:
            out.append(
                {
                    "position": pos,
                    "wt": ca,
                    "mt": cb,
                    "mutation": f"{ca}{pos}{cb}" if ca != "-" and cb != "-" else None,
                    "kind": "substitution" if ca != "-" and cb != "-" else "indel",
                }
            )
    return out


# --------------------------------------------------------------------------- alignment


@dataclass
class Alignment:
    """Global alignment. `identity`/`similarity` are over aligned columns (gaps included in the
    denominator), so an alignment of a short fragment to a long protein cannot reach 100%."""

    score: float
    identity: float
    similarity: float
    aligned_a: str
    aligned_b: str
    coverage: float = 1.0
    method: str = "needleman-wunsch/blosum62"


def _substitution_matrix():
    try:
        from Bio.Align import substitution_matrices

        return substitution_matrices.load("BLOSUM62")
    except Exception:  # pragma: no cover - biopython always ships BLOSUM62
        return None


_MATRIX = _substitution_matrix()


MATRIX_SOURCE = "blosum62" if _MATRIX is not None else "identity-fallback"


def sub_score(a: str, b: str) -> float:
    """BLOSUM62 substitution score, or an identity fallback when the matrix is unavailable.

    The fallback changes alignment behaviour, so `MATRIX_SOURCE` records which one is live rather
    than letting the substitution model degrade silently.
    """
    if _MATRIX is not None:
        try:
            return float(_MATRIX[a, b])
        except Exception:  # pragma: no cover
            pass
    return 4.0 if a == b else -1.0


def align(a: str, b: str, gap_open: float = -10.0, gap_extend: float = -0.5) -> Alignment:
    """Global alignment (affine gaps). Pure python so it is deterministic and dependency-light."""
    a, b = clean_sequence(a), clean_sequence(b)
    n, m = len(a), len(b)
    neg = float("-inf")
    match = [[neg] * (m + 1) for _ in range(n + 1)]
    gap_a = [[neg] * (m + 1) for _ in range(n + 1)]  # gap in b (consume a)
    gap_b = [[neg] * (m + 1) for _ in range(n + 1)]
    ptr = [[0] * (m + 1) for _ in range(n + 1)]
    match[0][0] = 0.0
    for i in range(1, n + 1):
        gap_a[i][0] = gap_open + gap_extend * (i - 1)
    for j in range(1, m + 1):
        gap_b[0][j] = gap_open + gap_extend * (j - 1)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            prev = max(match[i - 1][j - 1], gap_a[i - 1][j - 1], gap_b[i - 1][j - 1])
            match[i][j] = prev + sub_score(a[i - 1], b[j - 1])
            gap_a[i][j] = max(match[i - 1][j] + gap_open, gap_a[i - 1][j] + gap_extend)
            gap_b[i][j] = max(match[i][j - 1] + gap_open, gap_b[i][j - 1] + gap_extend)
            best = max(match[i][j], gap_a[i][j], gap_b[i][j])
            ptr[i][j] = 0 if best == match[i][j] else (1 if best == gap_a[i][j] else 2)
    i, j = n, m
    out_a: list[str] = []
    out_b: list[str] = []
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            move = ptr[i][j]
        elif i > 0:
            move = 1
        else:
            move = 2
        if move == 0:
            out_a.append(a[i - 1])
            out_b.append(b[j - 1])
            i, j = i - 1, j - 1
        elif move == 1:
            out_a.append(a[i - 1])
            out_b.append("-")
            i -= 1
        else:
            out_a.append("-")
            out_b.append(b[j - 1])
            j -= 1
    aligned_a = "".join(reversed(out_a))
    aligned_b = "".join(reversed(out_b))
    pairs = [(x, y) for x, y in zip(aligned_a, aligned_b, strict=False) if x != "-" and y != "-"]
    identical = sum(1 for x, y in pairs if x == y)
    similar = sum(1 for x, y in pairs if sub_score(x, y) > 0)
    denom = max(1, len(aligned_a))
    score = max(match[n][m], gap_a[n][m], gap_b[n][m])
    return Alignment(
        score=round(float(score), 2),
        identity=round(identical / denom, 4),
        similarity=round(similar / denom, 4),
        aligned_a=aligned_a,
        aligned_b=aligned_b,
        coverage=round(len(pairs) / denom, 4),
    )


@dataclass
class Hit:
    name: str
    identity: float
    similarity: float
    score: float
    annotation: str = ""
    metadata: dict = field(default_factory=dict)


def homology_search(query: str, corpus: list[dict], top_k: int = 5) -> list[Hit]:
    """Rank a reference corpus by global alignment identity.

    `corpus` entries: {"name": str, "sequence": str, "annotation": str, ...}
    """
    hits: list[Hit] = []
    for entry in corpus:
        try:
            aln = align(query, entry["sequence"])
        except SequenceError:
            continue
        hits.append(
            Hit(
                name=entry.get("name", "unknown"),
                identity=aln.identity,
                similarity=aln.similarity,
                score=aln.score,
                annotation=entry.get("annotation", ""),
                metadata={k: v for k, v in entry.items() if k not in {"sequence"}},
            )
        )
    hits.sort(key=lambda h: (-h.identity, -h.score))
    return hits[:top_k]


# --------------------------------------------------------------------------- DNA design


def back_translate(seq: str, stop: bool = True) -> str:
    """Back-translate to DNA using one preferred E. coli codon per amino acid.

    This is codon *replacement*, not codon optimization: always picking the same codon ignores
    tRNA pool balance, local GC/mRNA structure, ramp effects and internal restriction sites, and
    can create long homopolymer or repeat runs that synthesis vendors reject. Treat the output as
    a starting ORF to hand to a vendor's optimizer, not a finished construct.
    """
    seq = clean_sequence(seq)
    dna = "".join(PREFERRED_CODONS[a] for a in seq)
    if stop:
        dna += PREFERRED_CODONS["*"]
    return dna


def gc_content(dna: str) -> float:
    dna = dna.upper()
    return round((dna.count("G") + dna.count("C")) / max(1, len(dna)), 4)


def melting_temp(dna: str) -> float:
    """Approximate Tm: Wallace rule below 14 nt, else a salt-adjusted GC% formula.

    Both are sequence-composition rules with no nearest-neighbour thermodynamics, so expect
    several degrees of error against a vendor's NN calculator.
    """
    dna = dna.upper()
    n = len(dna)
    if n == 0:
        return 0.0
    gc = (dna.count("G") + dna.count("C")) / n
    if n < 14:
        return round(2 * (n - int(gc * n)) + 4 * int(gc * n), 1)
    return round(81.5 + 16.6 * math.log10(0.05) + 41 * gc - 500 / n, 1)


def reverse_complement(dna: str) -> str:
    table = str.maketrans("ACGTacgt", "TGCAtgca")
    return dna.translate(table)[::-1]
