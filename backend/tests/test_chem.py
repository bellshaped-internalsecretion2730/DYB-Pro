"""Cheminformatics engine: parsing, descriptors, alerts, PK and the binding proxy.

The reference numbers below are experimental values from the literature (see
``app/chem/CITATIONS.md``); the assertions are deliberately loose bands, because these estimators
are proxies and pinning them to three decimals would only test today's coefficients.
"""

from __future__ import annotations

import math

import pytest

from app.chem import (
    admet_panel,
    alert_report,
    descriptors,
    dose_projection,
    enumerate_analogs,
    evaluate_molecule,
    molecule_hash,
    parse_smiles,
    pocket_from_sequence,
    rank_molecules,
    simulate_pk,
    synthesis_report,
)
from app.chem.pk import pk_parameters
from app.chem.smiles import SmilesError

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
CAFFEINE = "Cn1cnc2c1c(=O)n(C)c(=O)n2C"
IBUPROFEN = "CC(C)Cc1ccc(cc1)C(C)C(=O)O"
BENZENE = "c1ccccc1"
DEMO_TARGET = "MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTE"


def test_parses_aromatic_and_aliphatic_rings():
    mol = parse_smiles(ASPIRIN)
    assert mol.formula == "C9H8O4"
    assert len(mol.rings) == 1
    assert descriptors(mol)["heavy_atoms"] == 13


@pytest.mark.parametrize("smiles", ["C[Q]", "c1ccccc", "C(", "", "C1CC"])
def test_rejects_malformed_smiles(smiles):
    with pytest.raises(SmilesError):
        parse_smiles(smiles)


def test_hash_is_canonical_not_textual():
    """Two spellings of the same molecule must commit to the same content address."""
    assert molecule_hash(parse_smiles("OC(=O)c1ccccc1")) == molecule_hash(
        parse_smiles("c1ccccc1C(O)=O")
    )
    assert molecule_hash(parse_smiles(ASPIRIN)) != molecule_hash(parse_smiles(IBUPROFEN))


@pytest.mark.parametrize(
    ("smiles", "logp", "tpsa"),
    [
        (ASPIRIN, 1.19, 63.6),
        (CAFFEINE, -0.07, 58.4),
        (IBUPROFEN, 3.97, 37.3),
        (BENZENE, 2.13, 0.0),
    ],
)
def test_descriptors_track_measured_values(smiles, logp, tpsa):
    desc = descriptors(parse_smiles(smiles))
    assert abs(desc["clogp"] - logp) < 1.0
    assert abs(desc["tpsa"] - tpsa) < 6.0
    assert 0.0 <= desc["qed_like"] <= 1.0


def test_structural_alerts_flag_a_known_toxicophore():
    clean = alert_report(parse_smiles(CAFFEINE))
    nitroaromatic = alert_report(parse_smiles("O=[N+]([O-])c1ccccc1"))
    assert clean["count"] == 0
    assert nitroaromatic["count"] >= 1
    assert nitroaromatic["penalty"] > clean["penalty"]


def test_admet_panel_is_bounded_and_ordered_by_lipophilicity():
    greasy = admet_panel(parse_smiles("CCCCCCCCCCCCCCCCc1ccccc1"))
    polar = admet_panel(parse_smiles(CAFFEINE))
    assert 0.0 <= greasy["admet_score"] <= 1.0
    assert 0.0 <= polar["admet_score"] <= 1.0
    # A C16 alkylbenzene is a textbook hERG/plasma-protein-binding liability next to caffeine.
    assert greasy["toxicity"]["herg"]["risk"] > polar["toxicity"]["herg"]["risk"]
    assert greasy["distribution"]["plasma_protein_binding"] > (
        polar["distribution"]["plasma_protein_binding"]
    )


def test_one_compartment_pk_is_dose_proportional_and_decays():
    params = pk_parameters(parse_smiles(IBUPROFEN))
    low = simulate_pk(200.0, params)
    high = simulate_pk(400.0, params)
    concs = [p["conc_ng_ml"] for p in low["curve"]]
    assert low["cmax_ng_ml"] > 0
    assert max(concs) >= low["cmax_ng_ml"]  # cmax is measured over the last interval only
    # Linear kinetics: doubling the dose doubles exposure, it does not change the shape.
    assert high["cmax_ng_ml"] == pytest.approx(2 * low["cmax_ng_ml"], rel=1e-4)
    assert high["tmax_h"] == pytest.approx(low["tmax_h"], rel=1e-6)
    assert low["curve"][-1]["conc_ng_ml"] < low["cmax_ng_ml"]  # elimination between doses


def test_dose_projection_scales_with_potency():
    mol = parse_smiles(IBUPROFEN)
    panel = admet_panel(mol)
    weak = dose_projection(mol, potency_nm=1000.0, panel=panel)
    strong = dose_projection(mol, potency_nm=1.0, panel=panel)
    assert strong["projected_dose_mg"] < weak["projected_dose_mg"]
    assert strong["regimen"]
    assert strong["assumptions"]


def test_binding_proxy_stays_inside_physical_bounds():
    pocket = pocket_from_sequence(DEMO_TARGET)
    assert pocket["residues"]
    for smiles in (ASPIRIN, CAFFEINE, IBUPROFEN, BENZENE):
        binding = evaluate_molecule(smiles, target_sequence=DEMO_TARGET)["binding"]
        # 4 - 11 pKd covers everything from a weak fragment to the tightest drug-like binders;
        # a sequence-derived proxy claiming more than that would be nonsense.
        assert 3.0 <= binding["pkd"] <= 11.0
        assert binding["kd_nm"] == pytest.approx(10 ** (9 - binding["pkd"]), rel=1e-6)
        assert binding["method"]


def test_evaluation_is_deterministic():
    first = evaluate_molecule(IBUPROFEN, target_sequence=DEMO_TARGET)
    second = evaluate_molecule(IBUPROFEN, target_sequence=DEMO_TARGET)
    assert first == second


def test_blocking_alert_collapses_the_composite_score():
    reactive = evaluate_molecule("O=C(Cl)c1ccccc1", target_sequence=DEMO_TARGET)
    clean = evaluate_molecule(CAFFEINE, target_sequence=DEMO_TARGET)
    assert reactive["liabilities"]["blocking"]
    assert reactive["verdict"].startswith("reject")
    assert reactive["composite_score"] < clean["composite_score"]


def test_analog_enumeration_produces_parseable_distinct_molecules():
    analogs = enumerate_analogs(BENZENE, max_analogs=12)
    assert analogs
    hashes = set()
    for analog in analogs:
        mol = parse_smiles(analog["smiles"])  # must not raise: invalid analogs are useless
        hashes.add(molecule_hash(mol))
        assert analog["transform"]
    assert len(hashes) == len(analogs)
    assert molecule_hash(parse_smiles(BENZENE)) not in hashes


def test_synthesis_report_penalises_complexity():
    simple = synthesis_report(parse_smiles(BENZENE))
    complex_ = synthesis_report(parse_smiles("C1CC2CCC3C(CCC4C3CCC5C4CCC5O)C2C1"))
    assert 1.0 <= simple["sa_score"] <= 10.0
    assert complex_["sa_score"] > simple["sa_score"]
    assert complex_["cost_per_gram_usd"] > simple["cost_per_gram_usd"]
    assert simple["route"]


def test_ranking_orders_by_composite_and_reports_failures():
    ranked = rank_molecules(
        [ASPIRIN, CAFFEINE, IBUPROFEN, "not-a-molecule"], target_sequence=DEMO_TARGET
    )
    scores = [e["composite_score"] for e in ranked["ranked"]]
    assert scores == sorted(scores, reverse=True)
    assert [e["rank"] for e in ranked["ranked"]] == list(range(1, len(scores) + 1))
    assert len(ranked["failures"]) == 1
    assert all(math.isfinite(s) for s in scores)
