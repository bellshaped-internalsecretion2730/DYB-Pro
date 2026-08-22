"""Wet-lab pack: constructs, mutagenesis primers, assay plan, cost/risk model.

The pack says what to build and what to measure. It deliberately does **not** predict assay
outcomes (no KD, no Tm shift, no % monomer, no expected yield) and does not estimate a probability
that a design validates: nothing in the in-silico stack is calibrated against measurements, so any
such number would be invented. Each assay instead states which in-silico proxy it puts to the test
and the decision it feeds.
"""

from __future__ import annotations

from app.toolkit import sequence as seqlib

# Indicative list prices (USD) for a European/US academic core facility, per candidate. Order of
# magnitude only: real quotes vary several-fold by vendor, volume and country.
COSTS = {
    "gene_synthesis_per_bp": 0.09,
    "primer_per_base": 0.25,
    "site_directed_mutagenesis_reaction": 45.0,
    "transformation_and_plasmid_prep": 35.0,
    "sequence_verification": 18.0,
    "expression_purification_small_scale": 320.0,
    "binding_assay_spr": 180.0,
    "thermal_stability_dsf": 60.0,
    "aggregation_sec": 95.0,
    "endotoxin_qc": 40.0,
}

# Cost drivers the model does not attempt to price.
COST_EXCLUSIONS = [
    "scientist and technician time",
    "instrument access/booking fees and service contracts",
    "shared consumables, media, columns and chips",
    "shipping, customs and vendor minimum-order charges",
    "failed builds, repeats and troubleshooting",
    "target/reagent protein production for the binding assay",
]

ASSAYS = [
    {
        "assay": "SPR / BLI kinetics",
        "readout": "KD, kon, koff vs target (measured)",
        "tests": "binding_score",
        "decision": "keep designs whose measured KD is at or better than the parent control",
        "cost_key": "binding_assay_spr",
    },
    {
        "assay": "nanoDSF thermal ramp",
        "readout": "Tm and Tagg onset (measured)",
        "tests": "ddg_proxy",
        "decision": "compare measured Tm against the parent run in the same plate",
        "cost_key": "thermal_stability_dsf",
    },
    {
        "assay": "SEC-HPLC / SEC-MALS",
        "readout": "% monomer and HMW species (measured)",
        "tests": "aggregation",
        "decision": "reject designs with more HMW species than the parent",
        "cost_key": "aggregation_sec",
    },
    {
        "assay": "Small-scale expression + A280",
        "readout": "soluble yield in mg/L (measured)",
        "tests": "solubility",
        "decision": "gate the rest of the panel on expressing at all",
        "cost_key": "expression_purification_small_scale",
    },
]

# Agilent QuikChange II guidance (manual 200521): 25-45 nt, mutation centred with 10-15 correct
# bases either side, >=40% GC, ending in G or C, and Tm >= 78 C by their formula.
PRIMER_MIN_LENGTH = 25
PRIMER_MAX_LENGTH = 45
PRIMER_MIN_TM = 78.0
PRIMER_MIN_GC = 0.40


def quikchange_tm(primer: str, mismatches: int = 3) -> float:
    """Tm by the Agilent QuikChange formula: 81.5 + 0.41*%GC - 675/N - %mismatch."""
    n = len(primer)
    if n == 0:
        return 0.0
    gc_pct = seqlib.gc_content(primer) * 100.0
    mismatch_pct = 100.0 * mismatches / n
    return round(81.5 + 0.41 * gc_pct - 675.0 / n - mismatch_pct, 1)


def mutagenesis_primers(
    parent_dna: str,
    mutation: dict,
    flank: int = 15,
    template_source: str = "dyb-pro-generated ORF",
) -> dict:
    """QuikChange-style complementary primer pair centred on the mutated codon.

    `parent_dna` must be the template that will actually be in the mutagenesis reaction. When it is
    a DYB Pro back-translated ORF rather than the user's plasmid, the primers cannot anneal to
    their plasmid at all -- the codons are ours, not theirs -- so `orderable` stays False and the
    pair is a design template to regenerate against the real sequence.
    """
    pos = int(mutation["position"])
    codon_start = (pos - 1) * 3
    new_codon = seqlib.PREFERRED_CODONS[mutation["mt"]]
    mutated = parent_dna[:codon_start] + new_codon + parent_dna[codon_start + 3 :]
    start = max(0, codon_start - flank)
    end = min(len(mutated), codon_start + 3 + flank)
    fwd = mutated[start:end]
    rev = seqlib.reverse_complement(fwd)
    tm = quikchange_tm(fwd)
    gc = seqlib.gc_content(fwd)
    checks = {
        "length_in_range": PRIMER_MIN_LENGTH <= len(fwd) <= PRIMER_MAX_LENGTH,
        "tm_at_least_78c": tm >= PRIMER_MIN_TM,
        "gc_at_least_40pct": gc >= PRIMER_MIN_GC,
        "ends_in_g_or_c": bool(fwd) and fwd[0] in "GC" and fwd[-1] in "GC",
        "template_is_users_plasmid": template_source == "user-supplied template",
    }
    return {
        "mutation": mutation.get("mutation") or f"{mutation['wt']}{pos}{mutation['mt']}",
        "forward": fwd,
        "reverse": rev,
        "length": len(fwd),
        "tm_c": tm,
        "tm_method": "Agilent QuikChange formula (not nearest-neighbour)",
        "gc_fraction": gc,
        "codon_change": f"{parent_dna[codon_start:codon_start + 3]}->{new_codon}",
        "template_source": template_source,
        "checks": checks,
        "orderable": all(checks.values()),
        "method": "QuikChange-style complementary primers, E. coli preferred codons",
    }


def construct(sequence: str, label: str, vector: str = "pET-28a(+)") -> dict:
    """A default E. coli expression construct. Host, vector and tags are assumptions, not advice:
    they are wrong for any glycosylated, disulfide-rich or membrane protein."""
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
        "extinction_coefficient_reduced_m1cm1": seqlib.extinction_coefficient(
            sequence, disulfides=False
        ),
        "assumptions": [
            "prokaryotic host: no glycosylation and an oxidising-poor cytoplasm",
            "single preferred codon per residue; hand to a vendor optimizer before ordering",
            "tags and vector are defaults, not chosen for this protein",
        ],
        "notes": "codon-replaced for E. coli (one preferred codon per residue), not CAI-optimized",
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
    qc = (
        COSTS["sequence_verification"]
        + COSTS["endotoxin_qc"]
        + COSTS["transformation_and_plasmid_prep"]
    )
    assays = sum(COSTS[a["cost_key"]] for a in ASSAYS)
    return {
        "route": route,
        "build_usd": round(build, 2),
        "qc_usd": round(qc, 2),
        "assay_usd": round(assays, 2),
        "total_usd": round(build + qc + assays, 2),
        "basis": "indicative consumables/service list prices only",
        "excludes": COST_EXCLUSIONS,
    }


def build_pack(
    ranked: list,
    evaluations: dict,
    parent_sequence: str,
    project_name: str,
    top_n: int = 5,
    total_candidate_pool: int | None = None,
    template_dna: str | None = None,
    calibration: dict | None = None,
) -> dict:
    """Assemble the wet-lab pack for the top-N ranked candidates.

    `template_dna` is the user's actual plasmid/template sequence. Without it, primers are designed
    against a DYB Pro-generated ORF and are marked as not orderable.
    """
    template_source = (
        "user-supplied template" if template_dna else "dyb-pro-generated ORF"
    )
    parent_dna = template_dna or seqlib.back_translate(parent_sequence)
    shortlist: list[dict] = []
    for cand in ranked:
        if len(shortlist) >= top_n:
            break
        if not cand.passed_filters:
            continue
        ev = evaluations[cand.label]
        primers = [
            mutagenesis_primers(parent_dna, m, template_source=template_source)
            for m in ev.mutations
            if m.get("mt")
        ]
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
                "geometry_usable": bool(ev.geometry_usable),
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
                        "tests_in_silico_proxy": a["tests"],
                        "in_silico_value": cand.scores.get(a["tests"]),
                        "in_silico_units": "arbitrary units (uncalibrated proxy)",
                        "decision_rule": a["decision"],
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
    not_shortlisted = max(0, pool - len(shortlist))
    # Cost of the builds+assays that were not ordered, at the same per-candidate rate. This is an
    # avoided *spend*, not a validated saving: it says nothing about whether the designs that were
    # dropped would have worked, and comparing it to a "test everything" total that is itself
    # derived from the shortlist size would make the percentage a restatement of top_n.
    per_candidate = shortlist_cost / max(1, len(shortlist))
    return {
        "project": project_name,
        "parent_sequence": parent_sequence,
        "template_source": template_source,
        "primers_orderable": bool(template_dna),
        "shortlist": shortlist,
        "economics": {
            "shortlist_size": len(shortlist),
            "candidate_pool": pool,
            "cost_per_candidate_usd": round(per_candidate, 2),
            "shortlist_cost_usd": shortlist_cost,
            "not_shortlisted": not_shortlisted,
            "spend_avoided_usd": round(per_candidate * not_shortlisted, 2),
            "basis": (
                "consumables/service list prices for the candidates in this cycle's pool; "
                "excludes labour and overheads"
            ),
            "caveat": (
                "avoided spend on designs that were not built. It is not a validated saving and "
                "not a claim about the whole in-silico stage: only measured results can tell you "
                "whether the shortlist was the right subset."
            ),
            "cost_model": COSTS,
            "cost_exclusions": COST_EXCLUSIONS,
        },
        "validation": _validation_status(calibration),
        "risks": _risks(shortlist),
        "citations": sorted({c for item in shortlist for c in item["citations"]}),
    }


def _validation_status(calibration: dict | None) -> dict:
    """What is actually known about this project's in-silico vs measured agreement.

    With no measured results in the project there is no hit rate to report, and confidence plus
    Pareto membership cannot be turned into one: both are properties of the heuristics, not
    evidence about the assay.
    """
    measured = int((calibration or {}).get("measurements", 0))
    if not measured:
        return {
            "measured_results": 0,
            "measured_hit_rate": None,
            "proxy_agreement": {},
            "note": (
                "no measured wet-lab results ingested for this project, so no hit rate or "
                "validation probability can be reported"
            ),
        }
    return {
        "measured_results": measured,
        "measured_hit_rate": (calibration or {}).get("hit_rate"),
        "proxy_agreement": (calibration or {}).get("objectives", {}),
        "note": (
            "observed agreement between proxies and measurements in this project only; "
            "small-sample and project-specific"
        ),
    }


def _risks(shortlist: list[dict]) -> list[dict]:
    risks: list[dict] = []
    for item in shortlist:
        notes = ["always order the parent as a same-plate control; all scores are relative"]
        if item["confidence"] < 0.6:
            notes.append("wide in-silico uncertainty — treat the ordering as weak evidence")
        if len(item["mutations"]) >= 3:
            notes.append("multi-mutant: epistasis is not modelled, consider single-mutant panel")
        if item["scores"].get("aggregation", 0) > 0.2:
            notes.append("residual aggregation-prone windows — include SEC in first pass")
        if not item.get("geometry_usable", True):
            notes.append(
                "coarse model geometry failed its own compactness/clash check: structure-derived "
                "scores (binding, burial-weighted stability) are unreliable for this design"
            )
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
