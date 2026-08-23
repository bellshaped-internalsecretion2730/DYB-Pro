"""Provenance tests: every heuristic must say what it is based on and how wrong it can be.

These tests pin the documented behaviour of the evidence registry plus at least one
literature-derived reference value per skill, so a future edit cannot quietly reintroduce an
uncited number or downgrade a published error bar into false precision.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app import skills
from app.skills import chemistry as chem
from app.skills import drug_discovery as dd
from app.skills import evidence
from app.skills import literature as lit
from app.skills import mathematics as mathskill
from app.skills import physics as phys
from app.skills import wetlab_metrics as wm

SKILL_DOCS = {
    "skill.physics": "physics.md",
    "skill.chemistry": "chemistry.md",
    "skill.drug_discovery": "drug_discovery.md",
    "skill.wetlab_metrics": "wetlab_metrics.md",
    "skill.math": "math.md",
    "skill.literature": "literature.md",
}
CONTRACTS = Path(__file__).resolve().parents[2] / "SKILLS"
SEQ = "MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTECCKMS"


def test_every_skill_registers_evidence():
    assert set(SKILL_DOCS) == set(skills.SKILL_NAMES)
    for skill in SKILL_DOCS:
        assert evidence.for_skill(skill), skill


@pytest.mark.parametrize("skill", sorted(SKILL_DOCS))
def test_records_state_claim_applicability_error_and_calibration(skill):
    records = evidence.for_skill(skill)
    keys = [r.key for r in records]
    assert len(set(keys)) == len(keys)
    for rec in records:
        assert rec.claim and rec.applicability and rec.error, rec.key
        assert rec.calibration in evidence.CALIBRATION_KINDS, rec.key
        # A published claim must be traceable; an unsupported one must say so instead.
        if rec.calibration in (evidence.CALIBRATED, evidence.ANCHORED):
            assert rec.doi, rec.key
            assert rec.source, rec.key
        else:
            assert re.search(
                r"uncalibrat|no empirical|no published|policy|project choice|our choice|"
                r"tuning knob|exact",
                rec.error,
                re.I,
            ), rec.key


def test_uncited_records_cannot_claim_literature_calibration():
    with pytest.raises(ValueError):
        evidence.Evidence(
            key="x.no_doi",
            claim="c",
            applicability="a",
            error="e",
            calibration=evidence.CALIBRATED,
        )
    with pytest.raises(ValueError):
        evidence.Evidence(
            key="x.bad_kind", claim="c", applicability="a", error="e", calibration="vibes"
        )
    with pytest.raises(ValueError):
        evidence.Evidence(
            key="x.no_error", claim="c", applicability="a", error="", calibration=evidence.POLICY
        )


@pytest.mark.parametrize("skill,doc", sorted(SKILL_DOCS.items()))
def test_dois_cited_in_code_also_appear_in_the_contract(skill, doc):
    text = (CONTRACTS / doc).read_text(encoding="utf-8").lower()
    for doi in evidence.dois(skill):
        assert doi.lower() in text, f"{doi} missing from SKILLS/{doc}"


@pytest.mark.parametrize("skill", sorted(SKILL_DOCS))
def test_provenance_is_machine_readable_and_citations_are_deduplicated(skill):
    rows = evidence.provenance(skill)
    assert rows and all(row["key"] and row["calibration"] for row in rows)
    lines = evidence.citations(skill)
    assert len(lines) == len(set(lines))


def test_missing_record_is_an_error_not_a_silent_default():
    with pytest.raises(KeyError):
        evidence.record("skill.physics", "physics.nope")


# --- literature-derived reference values, one or more per skill ------------------------------


def test_physics_pins_published_ddg_and_tm_benchmarks():
    # Potapov et al. 2009: predictors r=0.26-0.59 vs experimental replicates r=0.86.
    assert phys.DDG_PREDICTOR_R_RANGE == (0.26, 0.59)
    assert phys.DDG_EXPERIMENTAL_REPLICATE_R == 0.86
    assert phys.BEST_SEQUENCE_ONLY_TM_RMSE_C == 4.11  # Tijare et al. 2025 validation RMSE
    assert phys.IVYWREL_PROTEOME_R == 0.93  # Zeldovich et al. 2007
    # Our composition-only Tm envelope must stay well above the best published sequence-only RMSE.
    assert phys.TM_UNCERTAINTY_C > phys.BEST_SEQUENCE_ONLY_TM_RMSE_C
    assert phys.DDG_UNCERTAINTY_KCAL > 0
    assert evidence.record("skill.physics", "physics.ivywrel_prior").calibration == evidence.PROXY


def test_chemistry_pins_pace_extinction_and_ipc_error():
    assert (chem.TRP_EXTINCTION_M1CM1, chem.TYROSINE_EXTINCTION_M1CM1,
            chem.CYSTINE_EXTINCTION_M1CM1) == (5500, 1490, 125)
    assert chem.PI_PREDICTION_ERROR_PH == 0.87
    assert chem.PEPTIDE_PI_PREDICTION_ERROR_PH == 0.25
    out = skills.run("skill.chemistry", {"sequence": "WY"})
    assert out["metrics"]["extinction_coefficient_280"]["value"] == pytest.approx(
        chem.TRP_EXTINCTION_M1CM1 + chem.TYROSINE_EXTINCTION_M1CM1
    )
    assert out["metrics"]["isoelectric_point"]["sd"] == pytest.approx(chem.PI_PREDICTION_ERROR_PH)


def test_chemistry_a280_floor_and_pi_margin_are_declared_policy():
    assert chem.MIN_EXTINCTION_FOR_A280 == 1500
    assert chem.MIN_PI_OFFSET_PH == 1.0
    for key in ("chemistry.a280_floor", "chemistry.pi_buffer_margin"):
        assert evidence.record("skill.chemistry", key).calibration == evidence.POLICY


def test_drug_discovery_pins_soluprot_and_clinical_antibody_population():
    assert (dd.SOLUPROT_ACCURACY, dd.SOLUPROT_AUC) == (0.585, 0.62)
    assert dd.JAIN_CLINICAL_ANTIBODIES == 137
    assert dd.DEVELOPABILITY_SD == 0.18
    assert evidence.record(
        "skill.drug_discovery", "drug_discovery.composite_weights"
    ).calibration == evidence.PROXY
    assert evidence.record(
        "skill.drug_discovery", "drug_discovery.immunogenicity_limit"
    ).calibration == evidence.POLICY


def test_wetlab_pins_lysozyme_repeatability_and_biosensor_kinetics():
    # Cohrs et al. 2025: HEWL Tm 74.6 C, sd 0.17 C over 6096 microwell measurements.
    assert (wm.DSF_LYSOZYME_TM_C, wm.DSF_WITHIN_PLATE_TM_SD_C, wm.DSF_LYSOZYME_REPLICATES) == (
        74.6, 0.17, 6096
    )
    # Katsamba et al. 2006, 22 Biacore users on one interaction.
    assert (wm.SPR_REFERENCE_KON_M1S1, wm.SPR_REFERENCE_KOFF_S1, wm.SPR_REFERENCE_USERS) == (
        4.1e4, 4.5e-5, 22
    )
    # Our published Tm noise must be wider than a single plate's repeatability, never narrower.
    assert wm.noise_sd("melting_temperature", 60.0) >= wm.DSF_WITHIN_PLATE_TM_SD_C


def test_math_and_literature_heuristics_declare_their_status():
    assert evidence.record("skill.math", "math.uncertainty_propagation").calibration == evidence.EXACT
    assert evidence.record("skill.math", "math.rank_under_cost").doi == "10.1023/A:1013689704352"
    assert evidence.record(
        "skill.math", "math.value_of_information"
    ).calibration == evidence.PROXY
    assert evidence.record(
        "skill.literature", "literature.metric_extraction"
    ).calibration == evidence.PROXY
    assert {r.key for r in lit.EVIDENCE} >= {"literature.openalex", "literature.pubmed"}
    assert mathskill.CITATIONS


# --- the wet-lab schema must carry its provenance through to each measurement ---------------


def test_every_canonical_metric_names_its_sd_evidence_and_gate_basis():
    for metric, spec in wm.CANONICAL.items():
        record = wm.sd_evidence(metric)
        assert record.key == spec["sd_evidence"], metric
        # Pass/fail rules are Foldsmith gates, never presented as literature criteria.
        assert spec["pass_basis"] == evidence.POLICY, metric


def test_normalised_measurements_expose_sd_provenance_and_rule_basis():
    out = skills.run(
        "skill.wetlab_metrics",
        {
            "measurements": [
                {"metric": "Tm", "value": 62.5, "unit": "C"},
                {"metric": "yield", "value": 12.0, "unit": "mg/L"},
            ]
        },
    )
    by_metric = {m["metric"]: m for m in out["measurements"]}
    tm = by_metric["melting_temperature"]
    assert tm["sd_source"] == "wetlab.tm_assay_sd"
    assert tm["sd_calibration"] == evidence.POLICY
    assert tm["rule_basis"] == evidence.POLICY
    # Yield noise has no published calibration and must say so rather than look measured.
    assert by_metric["expression_yield"]["sd_calibration"] == evidence.PROXY


def test_physics_and_chemistry_metrics_carry_evidence_backed_citations():
    for skill, payload in (
        ("skill.physics", {"sequence": SEQ}),
        ("skill.chemistry", {"sequence": SEQ}),
    ):
        out = skills.run(skill, payload)
        known = set(evidence.citations(skill))
        for name, metric in out["metrics"].items():
            for line in metric["citations"]:
                assert line in known, f"{skill}.{name} cites an unregistered source"
