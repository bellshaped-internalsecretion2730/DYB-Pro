"""Deterministic evaluation of a candidate design: structure, developability, binding."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.toolkit import developability as dev
from app.toolkit import docking as dock
from app.toolkit import folding
from app.toolkit import sequence as seqlib
from app.toolkit import structure as structlib

CITATIONS = [
    "Kyte & Doolittle 1982 (hydropathy)",
    "Guruprasad et al. 1990 (instability index)",
    "Bjellqvist et al. 1993 (pI)",
    "Henikoff & Henikoff 1992 (BLOSUM62)",
    "Chou & Fasman 1978 (secondary structure propensity)",
    "Guerois et al. 2002 (empirical stability terms)",
    "Sormanni et al. 2015 (CamSol solubility)",
    "Tartaglia & Vendruscolo 2008 (aggregation propensity)",
    "Kim & Hummer 2008 (coarse-grained interaction potential)",
    "Katchalski-Katzir et al. 1992 (rigid-body shape matching)",
]


@dataclass
class Candidate:
    """A proposed design, either as explicit mutations on a parent or as a full sequence."""

    label: str
    parent_sequence: str
    mutations: list[str] = field(default_factory=list)
    sequence: str | None = None
    rationale: str = ""
    agent_role: str = "sequence"
    citations: list[str] = field(default_factory=list)

    def resolved(self) -> tuple[str, list[dict]]:
        if self.mutations:
            seq, applied = seqlib.apply_mutations(self.parent_sequence, self.mutations)
            return seq, applied
        if self.sequence:
            seq = seqlib.clean_sequence(self.sequence)
            return seq, seqlib.diff_sequences(self.parent_sequence, seq)
        raise ValueError(f"candidate '{self.label}' has neither mutations nor a sequence")


@dataclass
class Evaluation:
    label: str
    sequence: str
    mutations: list[dict]
    scores: dict
    uncertainty: dict
    filters: dict
    profile: dict
    structure_pdb: str
    structure_source: str
    citations: list[str]
    rationale: str

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "sequence": self.sequence,
            "mutations": self.mutations,
            "scores": self.scores,
            "uncertainty": self.uncertainty,
            "filters": self.filters,
            "profile": self.profile,
            "structure_source": self.structure_source,
            "citations": self.citations,
            "rationale": self.rationale,
        }


def evaluate_candidate(
    candidate: Candidate,
    target_structure: structlib.Structure | None = None,
    parent_structure: structlib.Structure | None = None,
    filter_config: dict | None = None,
) -> Evaluation:
    seq, mutations = candidate.resolved()
    model = folding.fold_sequence(seq, template=parent_structure, name=candidate.label)
    prof = dev.profile(seq, structure=model, mutations=mutations)
    geometry = structlib.summary(model)
    relaxation = dock.relax(model, steps=12)

    binding = None
    if target_structure is not None:
        binding = dock.dock(model, target_structure)

    scores = {
        "ddg_proxy": prof["ddg"]["value"],
        "solubility": prof["solubility"]["value"],
        "aggregation": prof["aggregation"]["value"],
        "immunogenicity": prof["immunogenicity"]["value"],
        "instability_index": prof["descriptors"]["instability_index"],
        "isoelectric_point": prof["descriptors"]["isoelectric_point"],
        "gravy": prof["descriptors"]["gravy"],
        "compactness": geometry["compactness"],
        "strain_energy": relaxation["final_energy"],
    }
    uncertainty = {
        "ddg_proxy": round(0.35 + 0.15 * len(mutations), 3),
        "solubility": 0.12,
        "aggregation": 0.05,
        "immunogenicity": 0.4,
    }
    if binding is not None:
        scores["binding_score"] = binding.binding_score
        scores["interface_contacts"] = float(binding.contacts)
        uncertainty["binding_score"] = binding.uncertainty

    filters = dev.apply_filters(prof, filter_config)
    return Evaluation(
        label=candidate.label,
        sequence=seq,
        mutations=mutations,
        scores=scores,
        uncertainty=uncertainty,
        filters=filters,
        profile={
            **prof,
            "geometry": geometry,
            "relaxation": relaxation,
            "docking": binding.as_dict() if binding is not None else None,
        },
        structure_pdb=model.to_pdb(),
        structure_source=model.source,
        citations=sorted(set(candidate.citations) | set(CITATIONS)),
        rationale=candidate.rationale,
    )


def evaluate_all(
    candidates: list[Candidate],
    target_structure: structlib.Structure | None = None,
    parent_structure: structlib.Structure | None = None,
    filter_config: dict | None = None,
) -> list[Evaluation]:
    out: list[Evaluation] = []
    for cand in candidates:
        try:
            out.append(
                evaluate_candidate(
                    cand,
                    target_structure=target_structure,
                    parent_structure=parent_structure,
                    filter_config=filter_config,
                )
            )
        except (seqlib.SequenceError, ValueError) as exc:
            # A malformed proposal is an observation, not a crash.
            out.append(
                Evaluation(
                    label=cand.label,
                    sequence="",
                    mutations=[],
                    scores={},
                    uncertainty={},
                    filters={"passed": False, "failed": ["invalid_proposal"], "checks": []},
                    profile={"error": str(exc)},
                    structure_pdb="",
                    structure_source="none",
                    citations=[],
                    rationale=f"rejected: {exc}",
                )
            )
    return out
