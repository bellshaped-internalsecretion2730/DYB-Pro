"""Wet-lab pack: constructs, mutagenesis primers, assay plan, cost/risk model."""

from __future__ import annotations

from app.toolkit import sequence as seqlib

# Indicative 2025 list prices (USD) for a European/US academic core facility. Configurable.
COSTS = {
    "gene_synthesis_per_bp": 0.09,
    "primer_per_base": 0.25,
    "site_directed_mutagenesis_reaction": 45.0,
    "sequence_verification": 18.0,
    "expression_purification_small_scale": 320.0,
    "binding_assay_spr": 180.0,
    "thermal_stability_dsf": 60.0,
    "aggregation_sec": 95.0,
    "endotoxin_qc": 40.0,
}

ASSAYS = [
    {
        "assay": "SPR / BLI kinetics",
        "readout": "KD, kon, koff vs target",
        "predicts": "binding_score",
        "cost_key": "binding_assay_spr",
    },
    {
        "assay": "nanoDSF thermal ramp",
        "readout": "Tm, Tagg onset",
        "predicts": "ddg_proxy",
        "cost_key": "thermal_stability_dsf",
    },
    {
        "assay": "SEC-HPLC / SEC-MALS",
        "readout": "% monomer, HMW species",
        "predicts": "aggregation",
        "cost_key": "aggregation_sec",
    },
    {
        "assay": "Small-scale expression + A280 solubility",
        "readout": "mg/L soluble yield",
        "predicts": "solubility",
        "cost_key": "expression_purification_small_scale",
    },
]


def mutagenesis_primers(parent_dna: str, mutation: dict, flank: int = 15) -> dict:
    """QuikChange-style complementary primer pair centred on the mutated codon."""
    pos = int(mutation["position"])
    codon_start = (pos - 1) * 3
    new_codon = seqlib.PREFERRED_CODONS[mutation["mt"]]
    mutated = parent_dna[:codon_start] + new_codon + parent_dna[codon_start + 3 :]
    start = max(0, codon_start - flank)
    end = min(len(mutated), codon_start + 3 + flank)
    fwd = mutated[start:end]
    rev = seqlib.reverse_complement(fwd)
    return {
        "mutation": mutation.get("mutation") or f"{mutation['wt']}{pos}{mutation['mt']}",
        "forward": fwd,
        "reverse": rev,
        "length": len(fwd),
        "tm_c": seqlib.melting_temp(fwd),
        "gc_fraction": seqlib.gc_content(fwd),
        "codon_change": f"{parent_dna[codon_start:codon_start + 3]}->{new_codon}",
        "method": "QuikChange-style complementary primers, E. coli optimal codons",
    }


def construct(sequence: str, label: str, vector: str = "pET-28a(+)") -> dict:
    orf = seqlib.back_translate(sequence)
    return {
        "label": label,
        "vector": vector,
        "expression_host": "E. coli BL21(DE3)",
        "tags": ["N-terminal His6", "TEV cleavage site"],
        "orf_length_bp": len(orf),
        "orf": orf,
        "gc_fraction": seqlib.gc_content(orf),
        "protein_length": len(sequence),
        "molecular_weight_da": seqlib.molecular_weight(sequence),
        "extinction_coefficient_m1cm1": seqlib.extinction_coefficient(sequence),
        "notes": "codon-optimized for E. coli high expression (CAI-preferred codons)",
    }


def _candidate_cost(mutation_count: int, synthesize: bool, orf_bp: int) -> dict:
    if synthesize or mutation_count > 3:
        build = COSTS["gene_synthesis_per_bp"] * orf_bp
        route = "de-novo gene synthesis"
    else:
        build = mutation_count * (
            COSTS["site_directed_mutagenesis_reaction"] + 2 * 30 * COSTS["primer_per_base"]
        )
        route = "site-directed mutagenesis"
    qc = COSTS["sequence_verification"] + COSTS["endotoxin_qc"]
    assays = sum(COSTS[a["cost_key"]] for a in ASSAYS)
    return {
        "route": route,
        "build_usd": round(build, 2),
        "qc_usd": round(qc, 2),
        "assay_usd": round(assays, 2),
        "total_usd": round(build + qc + assays, 2),
    }


def build_pack(
    ranked: list,
    evaluations: dict,
    parent_sequence: str,
    project_name: str,
    top_n: int = 5,
    total_candidate_pool: int | None = None,
) -> dict:
    """Assemble the orderable wet-lab pack for the top-N ranked candidates."""
    parent_dna = seqlib.back_translate(parent_sequence)
    shortlist: list[dict] = []
    for cand in ranked:
        if len(shortlist) >= top_n:
            break
        if not cand.passed_filters:
            continue
        ev = evaluations[cand.label]
        primers = [mutagenesis_primers(parent_dna, m) for m in ev.mutations if m.get("mt")]
        cons = construct(ev.sequence, cand.label)
        cost = _candidate_cost(len(ev.mutations), synthesize=not primers, orf_bp=cons["orf_length_bp"])
        shortlist.append(
            {
                "rank": cand.rank,
                "label": cand.label,
                "sequence": ev.sequence,
                "mutations": [m.get("mutation") for m in ev.mutations],
                "composite_score": cand.composite,
                "confidence": cand.confidence,
                "pareto_optimal": cand.pareto,
                "scores": cand.scores,
                "uncertainty": cand.uncertainty,
                "why": cand.why,
                "why_not_next": cand.why_not_next,
                "construct": cons,
                "primers": primers,
                "assay_plan": [
                    {
                        "assay": a["assay"],
                        "readout": a["readout"],
                        "predicted_signal": _predicted_signal(a["predicts"], cand.scores),
                        "estimated_cost_usd": COSTS[a["cost_key"]],
                    }
                    for a in ASSAYS
                ],
                "cost": cost,
                "citations": ev.citations,
            }
        )

    pool = total_candidate_pool if total_candidate_pool is not None else len(ranked)
    shortlist_cost = round(sum(c["cost"]["total_usd"] for c in shortlist), 2)
    per_candidate = shortlist_cost / max(1, len(shortlist))
    test_everything = round(per_candidate * pool, 2)
    hit_probability = _hit_probability(shortlist)
    return {
        "project": project_name,
        "parent_sequence": parent_sequence,
        "shortlist": shortlist,
        "economics": {
            "shortlist_size": len(shortlist),
            "candidate_pool": pool,
            "shortlist_cost_usd": shortlist_cost,
            "test_everything_cost_usd": test_everything,
            "savings_usd": round(test_everything - shortlist_cost, 2),
            "savings_pct": round(
                100.0 * (test_everything - shortlist_cost) / max(1e-6, test_everything), 1
            ),
            "expected_hits": round(hit_probability * len(shortlist), 2),
            "expected_hit_probability_per_candidate": round(hit_probability, 3),
            "cost_model": COSTS,
        },
        "risks": _risks(shortlist),
        "citations": sorted({c for item in shortlist for c in item["citations"]}),
    }


def _predicted_signal(objective: str, scores: dict) -> str:
    value = scores.get(objective)
    if value is None:
        return "no in-silico prior; measure as baseline"
    if objective == "binding_score":
        return f"expect measurable binding (docking score {value}); flag if KD > 1 uM"
    if objective == "ddg_proxy":
        direction = "Tm at or above parent" if value <= 0 else "Tm drop of 1-4 C vs parent"
        return f"{direction} (ddG proxy {value})"
    if objective == "aggregation":
        return f"expect >95% monomer if aggregation score stays at {value}"
    if objective == "solubility":
        return f"expect soluble expression (solubility index {value})"
    return f"{objective} = {value}"


def _hit_probability(shortlist: list[dict]) -> float:
    """Confidence- and filter-weighted prior that a shortlisted design validates in vitro."""
    if not shortlist:
        return 0.0
    total = 0.0
    for item in shortlist:
        base = 0.45
        base += 0.25 * item["confidence"]
        if item["pareto_optimal"]:
            base += 0.08
        if len(item["mutations"]) > 4:
            base -= 0.1
        total += min(0.9, max(0.05, base))
    return total / len(shortlist)


def _risks(shortlist: list[dict]) -> list[dict]:
    risks: list[dict] = []
    for item in shortlist:
        notes = []
        if item["confidence"] < 0.6:
            notes.append("wide in-silico uncertainty — order alongside the parent as a control")
        if len(item["mutations"]) >= 3:
            notes.append("multi-mutant: epistasis is not modelled, consider single-mutant panel")
        if item["scores"].get("aggregation", 0) > 0.2:
            notes.append("residual aggregation-prone windows — include SEC in first pass")
        if not notes:
            notes.append("no material in-silico risk flags; standard QC sufficient")
        risks.append({"label": item["label"], "notes": notes})
    return risks


# --------------------------------------------------------------------------- exports


def to_csv(pack: dict) -> str:
    header = [
        "rank",
        "label",
        "mutations",
        "composite_score",
        "confidence",
        "binding_score",
        "ddg_proxy",
        "solubility",
        "aggregation",
        "immunogenicity",
        "route",
        "total_usd",
        "why",
    ]
    rows = [",".join(header)]
    for item in pack["shortlist"]:
        scores = item["scores"]
        rows.append(
            ",".join(
                _csv_cell(v)
                for v in [
                    item["rank"],
                    item["label"],
                    "|".join(m for m in item["mutations"] if m),
                    item["composite_score"],
                    item["confidence"],
                    scores.get("binding_score", ""),
                    scores.get("ddg_proxy", ""),
                    scores.get("solubility", ""),
                    scores.get("aggregation", ""),
                    scores.get("immunogenicity", ""),
                    item["cost"]["route"],
                    item["cost"]["total_usd"],
                    item["why"],
                ]
            )
        )
    return "\n".join(rows) + "\n"


def _csv_cell(value) -> str:
    text = str(value)
    if any(ch in text for ch in [",", '"', "\n"]):
        return '"' + text.replace('"', '""') + '"'
    return text


def to_fasta(pack: dict) -> str:
    records = [
        (
            f"{item['label']} rank={item['rank']} score={item['composite_score']} "
            f"mutations={'|'.join(m for m in item['mutations'] if m) or 'none'}",
            item["sequence"],
        )
        for item in pack["shortlist"]
    ]
    return seqlib.to_fasta(records)
