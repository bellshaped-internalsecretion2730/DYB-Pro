"""Every skill must be typed, provenance-stamped and honest about uncertainty."""

from __future__ import annotations

import math

import pytest

from app import skills
from app.skills import literature as lit
from app.skills import wetlab_metrics as wm

SEQ = "MTYKLILNGKTLKGETTTEAVDAATAEKVFKQYANDNGVDGEWTYDDATKTFTVTECCKMS"


def test_registry_exposes_all_six_skills():
    assert set(skills.SKILL_NAMES) <= set(skills.registry())
    for name in skills.SKILL_NAMES:
        spec = skills.get(name)
        assert spec.input_schema()["type"] == "object"
        assert spec.output_schema()["type"] == "object"
        assert spec.version


def test_every_metric_carries_skill_provenance_and_method():
    out = skills.run("skill.physics", {"sequence": SEQ})
    assert out["metrics"]
    for name, metric in out["metrics"].items():
        assert metric["skill"] == "skill.physics", name
        assert metric["method"], name
    skills.require_skill_metrics(out["metrics"])


def test_require_skill_metrics_rejects_unstamped_claims():
    with pytest.raises(skills.SkillError):
        skills.require_skill_metrics({"melting_temperature": {"value": 70.0}})


def test_chemistry_flags_unpaired_cysteine_and_reports_pi():
    out = skills.run("skill.chemistry", {"sequence": SEQ[:-3] + "C"})
    assert any("cysteine" in flag for flag in out["flags"])
    assert 0 < out["metrics"]["isoelectric_point"]["value"] < 14


def test_math_ranks_information_per_dollar_not_raw_value():
    ranked = skills.run(
        "skill.math",
        {
            "operation": "rank_under_cost",
            "items": [
                {"label": "cheap", "value": 1.0, "sd": 0.1, "cost_usd": 10.0},
                {"label": "pricey", "value": 1.4, "sd": 0.1, "cost_usd": 400.0},
            ],
        },
    )["result"]["ranked"]
    assert ranked[0]["label"] == "cheap"


def test_math_bayesian_update_shrinks_uncertainty():
    out = skills.run(
        "skill.math",
        {
            "operation": "bayesian_update",
            "prior_mean": 0.0,
            "prior_sd": 4.0,
            "observation": 2.0,
            "observation_sd": 1.0,
        },
    )["result"]
    assert out["posterior_sd"] < 4.0
    assert 0.0 < out["posterior_mean"] <= 2.0
    assert out["variance_reduction"] > 0.0


def test_wetlab_metrics_normalises_units_and_booleans():
    out = skills.run(
        "skill.wetlab_metrics",
        {
            "measurements": [
                {"metric": "KD", "value": 0.8, "unit": "uM"},
                {"metric": "yield", "value": 2.5, "unit": "mg/mL"},
                {"metric": "expressed", "value": "yes"},
                {"metric": "Tm", "value": 62.5, "unit": "C"},
            ]
        },
    )
    by_metric = {m["metric"]: m for m in out["measurements"]}
    assert by_metric["kd"]["value"] == pytest.approx(800.0)
    assert by_metric["kd"]["unit"] == "nM"
    assert by_metric["expression_yield"]["value"] == pytest.approx(2500.0)
    assert by_metric["expression"]["value"] is True
    assert by_metric["melting_temperature"]["assay_sd"] > 0


def test_wetlab_metrics_strict_mode_rejects_unknown_metric():
    payload = {"measurements": [{"metric": "vibes", "value": 1.0}], "strict": True}
    with pytest.raises(skills.SkillError):
        skills.run("skill.wetlab_metrics", payload)
    lenient = skills.run("skill.wetlab_metrics", {"measurements": payload["measurements"]})
    assert lenient["unknown"] == ["vibes"]


def test_wetlab_metrics_assay_noise_is_documented_for_every_canonical_metric():
    for metric in wm.CANONICAL:
        assert wm.noise_sd(metric, 10.0) >= 0.0
        assert wm.CANONICAL[metric]["assay"]


def test_drug_discovery_penalises_liabilities_but_keeps_uncertainty():
    out = skills.run(
        "skill.drug_discovery",
        {
            "candidates": [
                {"label": "clean", "sequence": SEQ, "affinity_score": -8.0, "affinity_sd": 1.0},
                {
                    "label": "sticky",
                    "sequence": "M" + "W" * 40 + "C",
                    "affinity_score": -9.0,
                    "affinity_sd": 1.0,
                },
            ]
        },
    )
    scores = {a["label"]: a for a in out["assessments"]}
    assert scores["clean"]["developability_index"] > scores["sticky"]["developability_index"]
    assert scores["sticky"]["developability_sd"] > 0
    assert scores["sticky"]["risk_flags"] or scores["sticky"]["failed_filters"]


def test_literature_offline_corpus_is_used_when_network_is_disabled():
    corpus = [
        {
            "id": "cached-1",
            "title": "Differential scanning fluorimetry repeatability of lysozyme Tm",
            "abstract": "The melting temperature was 72.3 °C with a standard deviation of 0.17 °C.",
            "doi": "10.1000/dsf",
            "year": 2021,
            "source": "cache",
        }
    ]
    out = skills.run(
        "skill.literature",
        {"query": "lysozyme melting temperature repeatability", "sources": [], "offline_corpus": corpus},
    )
    assert out["papers"]
    assert out["sources_used"] == ["offline-cache"]  # nothing live was contacted, and we say so
    assert out["papers"][0]["citation"]
    metrics = out["papers"][0]["extracted_metrics"]
    assert any(m["name"] == "melting_temperature" for m in metrics)


def test_literature_extracts_numbers_and_never_invents_them():
    papers = lit.extract_metrics("No numbers here at all.")
    assert papers == []
    found = lit.extract_metrics("KD of 12.5 nM and a Tm of 58 °C were measured.")
    assert {m.name for m in found} >= {"kd_nm", "melting_temperature"}


def test_physics_ddg_to_dtm_is_monotonic_and_bounded():
    a = skills.physics.ddg_to_dtm(0.0, 120)
    b = skills.physics.ddg_to_dtm(-1.5, 120)
    assert b > a
    assert math.isfinite(b)
