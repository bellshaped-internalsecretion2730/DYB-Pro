"""Human PK/PD projection: one-compartment model with first-order absorption.

C(t) = (F * D * ka) / (Vd * (ka - ke)) * (exp(-ke*t) - exp(-ka*t))

The model is intentionally simple and explicit: it turns the ADMET panel into the
numbers a project team actually argues about at a gate (predicted dose, Cmax/Ctrough,
coverage of the target concentration and the safety margin).
"""

from __future__ import annotations

import math

from app.chem.admet import admet_panel
from app.chem.smiles import Molecule, parse_smiles

BODY_WEIGHT_KG = 70.0


def _mol(target: Molecule | str) -> Molecule:
    return target if isinstance(target, Molecule) else parse_smiles(target)


def pk_parameters(target: Molecule | str, panel: dict | None = None) -> dict:
    """Derive human PK parameters (F, ka, Vd, CL, ke) from the ADMET panel."""
    mol = _mol(target)
    panel = panel or admet_panel(mol)
    hia = panel["absorption"]["human_intestinal_absorption"]
    pgp = panel["absorption"]["pgp_substrate_probability"]
    cl_per_kg = panel["excretion"]["cl_total_l_per_h_per_kg"]
    vss_per_kg = panel["distribution"]["vss_l_per_kg"]
    # First-pass extraction from the hepatic component of clearance.
    e_hepatic = min(0.95, panel["excretion"]["cl_hepatic_l_per_h_per_kg"] / 1.24)
    bioavailability = round(max(0.02, hia * (1 - e_hepatic) * (1 - 0.35 * pgp)), 3)
    cl = round(cl_per_kg * BODY_WEIGHT_KG, 3)  # L/h
    vd = round(vss_per_kg * BODY_WEIGHT_KG, 2)  # L
    ke = round(cl / vd, 4)
    ka = round(max(0.25, 1.6 - 0.9 * pgp), 3)  # 1/h
    if abs(ka - ke) < 1e-3:
        ka = round(ke * 1.5 + 0.1, 3)
    return {
        "bioavailability": bioavailability,
        "ka_per_h": ka,
        "ke_per_h": ke,
        "cl_l_per_h": cl,
        "vd_l": vd,
        "half_life_h": round(0.693 / ke, 2),
        "fraction_unbound": panel["distribution"]["fraction_unbound"],
        "molecular_weight": mol.molecular_weight,
    }


def simulate_pk(
    dose_mg: float,
    params: dict,
    interval_h: float = 24.0,
    doses: int = 5,
    points_per_interval: int = 24,
) -> dict:
    """Multiple-dose superposition of the one-compartment oral model."""
    ka, ke = params["ka_per_h"], params["ke_per_h"]
    vd, f = params["vd_l"], params["bioavailability"]
    mw = params.get("molecular_weight") or 350.0
    factor = f * dose_mg * 1000.0 * ka / (vd * (ka - ke))  # ug/L == ng/mL

    def concentration(t: float) -> float:
        total = 0.0
        for d in range(doses):
            dt = t - d * interval_h
            if dt < 0:
                continue
            total += factor * (math.exp(-ke * dt) - math.exp(-ka * dt))
        return max(0.0, total)

    horizon = interval_h * doses
    step = interval_h / points_per_interval
    curve = [
        {"t_h": round(step * i, 2), "conc_ng_ml": round(concentration(step * i), 2)}
        for i in range(int(horizon / step) + 1)
    ]
    last_start = interval_h * (doses - 1)
    steady = [p for p in curve if p["t_h"] >= last_start]
    cmax = max((p["conc_ng_ml"] for p in steady), default=0.0)
    tmax = next((p["t_h"] - last_start for p in steady if p["conc_ng_ml"] == cmax), 0.0)
    ctrough = steady[-1]["conc_ng_ml"] if steady else 0.0
    auc_tau = sum(
        (steady[i]["conc_ng_ml"] + steady[i + 1]["conc_ng_ml"]) / 2 * step for i in range(len(steady) - 1)
    )
    single = f * dose_mg * 1000.0 / params["cl_l_per_h"]
    return {
        "dose_mg": round(dose_mg, 2),
        "interval_h": interval_h,
        "cmax_ng_ml": round(cmax, 2),
        "tmax_h": round(tmax, 2),
        "ctrough_ng_ml": round(ctrough, 2),
        "auc_tau_ng_h_ml": round(auc_tau, 1),
        "auc_inf_single_dose_ng_h_ml": round(single, 1),
        "accumulation_ratio": round(auc_tau / max(single, 1e-6), 2),
        "cmax_nm": round(cmax / mw * 1000, 1),
        "ctrough_nm": round(ctrough / mw * 1000, 1),
        "curve": curve,
        "model": "one-compartment, first-order absorption, linear kinetics",
    }


def dose_projection(
    target: Molecule | str,
    potency_nm: float,
    interval_h: float = 24.0,
    coverage_multiple: float = 9.0,
    tox_margin_target: float = 10.0,
    panel: dict | None = None,
) -> dict:
    """Project the human dose needed to keep free trough above the target concentration.

    `potency_nm` is the in-vitro IC50/Kd; `coverage_multiple` converts it to the
    efficacious free concentration (9x IC50 ~ IC90 for a simple Emax relationship).
    """
    mol = _mol(target)
    panel = panel or admet_panel(mol)
    params = pk_parameters(mol, panel=panel)
    fu = max(params["fraction_unbound"], 1e-4)
    mw = mol.molecular_weight
    target_free_nm = potency_nm * coverage_multiple
    target_total_ng_ml = target_free_nm / fu * mw / 1000.0

    probe = simulate_pk(100.0, params, interval_h=interval_h)
    if probe["ctrough_ng_ml"] <= 0:
        dose = float("inf")
    else:
        dose = 100.0 * target_total_ng_ml / probe["ctrough_ng_ml"]
    feasible = math.isfinite(dose) and dose <= 2000.0
    dose_mg = round(min(dose, 5000.0), 1) if math.isfinite(dose) else None
    profile = simulate_pk(dose_mg or 0.0, params, interval_h=interval_h) if dose_mg else {}
    # A crude safety anchor: assume the tolerated exposure scales with the inverse of the
    # aggregate tox burden, then express the margin against the efficacious exposure.
    tox_burden = max(0.05, panel["toxicity"]["alert_burden"] + panel["toxicity"]["herg"]["risk"])
    tolerated_ng_ml = target_total_ng_ml * (tox_margin_target / tox_burden)
    margin = round(tolerated_ng_ml / max(profile.get("cmax_ng_ml", 1e-6), 1e-6), 2) if profile else 0.0
    return {
        "potency_nm": potency_nm,
        "target_free_nm": round(target_free_nm, 2),
        "target_total_ng_ml": round(target_total_ng_ml, 2),
        "projected_dose_mg": dose_mg,
        "interval_h": interval_h,
        "regimen": f"{dose_mg} mg every {int(interval_h)} h" if dose_mg else "not achievable orally",
        "feasible_oral_dose": bool(feasible),
        "predicted_safety_margin": margin,
        "pk_parameters": params,
        "steady_state": {k: v for k, v in profile.items() if k != "curve"},
        "curve": profile.get("curve", []),
        "assumptions": [
            f"{coverage_multiple}x IC50 free-drug coverage at trough",
            "70 kg adult, linear one-compartment kinetics",
            "safety margin derived from structural-alert/hERG burden, not from in-vivo tox data",
        ],
    }
