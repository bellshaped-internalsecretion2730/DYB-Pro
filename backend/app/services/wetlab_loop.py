"""The wet-lab loop: predict -> propose -> measure (or honestly simulate) -> learn -> recalibrate.

Every number in here is produced by a skill and carries a standard deviation, because the whole
point of the loop is to compare a *distribution* with a measurement and shrink the gap.

The simulator is deliberately honest: it samples from the predicted distribution widened by the
published assay noise, records the exact error model it used, and stamps the row
``source="simulator"`` so nothing downstream can mistake it for a real measurement.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import skills
from app.models import (
    Artifact,
    DriftModel,
    Project,
    ProteinCommit,
    ResearchProject,
    WetlabPlan,
    WetlabResult,
    utcnow,
)
from app.services import cycle as cycle_svc
from app.services import lab_research as research_svc
from app.services import wetlab as packlib
from app.skills import physics as physics_skill
from app.skills import wetlab_metrics as wm
from app.storage import store
from app.toolkit import sequence as seqlib

logger = logging.getLogger(__name__)

# Prediction (not assay) uncertainty, from published benchmark performance. See SKILLS docs.
PREDICTION_SD = {
    "melting_temperature": 4.0,      # ddG proxy r~0.26-0.59 -> >= 4 C on Tm
    "delta_tm": 2.5,
    "soluble_fraction": 20.0,        # sequence-only solubility predictors: AUC ~0.62
    "expression_yield": 0.6,         # relative
    "aggregation_hmw": 3.0,
    "purification_recovery": 15.0,
    "kd": 1.0,                       # log10
    "activity": 20.0,
}

# Expression host presets. Temperature/media matter more than most in-silico work admits.
HOSTS = {
    "E. coli BL21(DE3)": {"temperature_c": 18, "media": "TB autoinduction",
                          "yield_factor": 1.0, "notes": "cytoplasmic, IPTG or autoinduction"},
    "E. coli SHuffle T7": {"temperature_c": 25, "media": "LB + IPTG",
                           "yield_factor": 0.7, "notes": "oxidising cytoplasm for disulfides"},
    "E. coli periplasm (pelB)": {"temperature_c": 25, "media": "TB",
                                 "yield_factor": 0.4, "notes": "disulfide formation, lower yield"},
    "HEK293-F transient": {"temperature_c": 37, "media": "serum-free suspension",
                           "yield_factor": 0.5, "notes": "glycosylation competent, higher cost"},
}

BASELINE_YIELD_MG_L = 45.0


@dataclass
class Prediction:
    metric: str
    value: float | bool
    sd: float
    method: str
    skill: str
    calibrated: bool = False
    raw_value: float | bool | None = None

    def as_dict(self) -> dict:
        return {
            "metric": self.metric,
            "value": self.value,
            "sd": round(self.sd, 4) if isinstance(self.sd, int | float) else self.sd,
            "unit": wm.CANONICAL[self.metric]["unit"],
            "method": self.method,
            "skill": self.skill,
            "calibrated": self.calibrated,
            "raw_value": self.raw_value,
        }


# ------------------------------------------------------------------- prediction


def _structure_text(db: Session, commit: ProteinCommit) -> str | None:
    if not commit.structure_key:
        return None
    artifact = db.scalar(select(Artifact).where(Artifact.key == commit.structure_key))
    try:
        raw = store.get(commit.structure_key, backend=artifact.backend if artifact else "s3")
    except OSError as exc:  # a storage miss must not break the loop
        logger.warning("structure %s unavailable: %s", commit.structure_key, exc)
        return None
    return raw.decode("utf-8", "replace")


def predict_metrics(
    db: Session,
    rp: ResearchProject,
    commit: ProteinCommit,
    *,
    host: str = "E. coli BL21(DE3)",
    target_pdb: str | None = None,
) -> dict:
    """Predicted wet-lab distributions for one version, calibrated by measured drift so far."""
    sequence = seqlib.clean_sequence(commit.sequence)
    structure_pdb = _structure_text(db, commit)
    physics = skills.run(
        "skill.physics",
        {"sequence": sequence, "structure_pdb": structure_pdb, "target_pdb": target_pdb,
         "mutations": commit.mutations or []},
    )
    chemistry = skills.run("skill.chemistry", {"sequence": sequence})
    drug = skills.run(
        "skill.drug_discovery",
        {"candidates": [{"label": commit.label or commit.id[:8], "sequence": sequence,
                         "mutations": commit.mutations or []}]},
    )
    pm, cm = physics["metrics"], chemistry["metrics"]
    assessment = drug["assessments"][0]

    solubility = float(cm.get("solubility", {}).get("value", 0.5) or 0.5)
    aggregation = float(cm.get("aggregation_propensity", {}).get("value", 0.3) or 0.3)
    tm = float(pm.get("predicted_tm", {}).get("value", 55.0) or 55.0)
    ddg = float(pm.get("ddg_proxy", {}).get("value", 0.0) or 0.0)
    dtm = round(physics_skill.ddg_to_dtm(ddg, len(sequence)), 2)
    if host not in HOSTS:
        host = "E. coli BL21(DE3)"  # never predict against a preset the caller did not ask for
    host_cfg = HOSTS[host]
    length_penalty = max(0.35, min(1.0, 220.0 / max(len(sequence), 60)))
    p_expression = max(
        0.05,
        min(0.97, 0.35 + 0.45 * solubility + 0.15 * length_penalty - 0.25 * aggregation),
    )
    yield_mg_l = round(
        BASELINE_YIELD_MG_L * host_cfg["yield_factor"] * solubility * length_penalty * p_expression,
        2,
    )

    preds: list[Prediction] = [
        Prediction("expression", p_expression >= 0.5, 0.0,
                   f"solubility/length/aggregation logistic proxy, p={p_expression:.2f} in {host}",
                   "skill.chemistry"),
        Prediction("expression_yield", yield_mg_l, PREDICTION_SD["expression_yield"] * yield_mg_l,
                   f"baseline {BASELINE_YIELD_MG_L} mg/L scaled by host, solubility and length",
                   "skill.chemistry"),
        Prediction("soluble_fraction", round(solubility * 100.0, 1), PREDICTION_SD["soluble_fraction"],
                   "sequence-only solubility proxy (weak predictor, wide sd)", "skill.chemistry"),
        Prediction("melting_temperature", round(tm, 2), PREDICTION_SD["melting_temperature"],
                   "IVYWREL baseline + Becktel-Schellman conversion of the ddG proxy", "skill.physics"),
        Prediction("delta_tm", round(dtm, 2), PREDICTION_SD["delta_tm"],
                   "ddG proxy converted with dTm = ddG / dS_m", "skill.physics"),
        Prediction("aggregation_hmw", round(min(aggregation * 20.0, 60.0), 2),
                   PREDICTION_SD["aggregation_hmw"],
                   "hydrophobic-patch aggregation propensity mapped onto SEC HMW %", "skill.chemistry"),
        Prediction("purification_recovery", round(35.0 + 40.0 * solubility, 1),
                   PREDICTION_SD["purification_recovery"],
                   "IMAC+SEC recovery proxy from solubility", "skill.chemistry"),
        Prediction("activity", round(100.0 * float(assessment["developability_index"]), 1),
                   PREDICTION_SD["activity"],
                   "developability index as a fraction-of-wild-type activity prior",
                   "skill.drug_discovery"),
    ]
    if "predicted_log10_kd_shift" in pm:
        shift = float(pm["predicted_log10_kd_shift"]["value"])
        kd_nm = round(10 ** (2.0 + shift), 3)
        preds.append(
            Prediction("kd", kd_nm, PREDICTION_SD["kd"] * kd_nm * math.log(10) / 2.0,
                       "docking proxy log10 shift anchored at 100 nM (relative, not absolute)",
                       "skill.physics")
        )

    calibrated = {p.metric: apply_calibration(db, rp, p) for p in preds}
    predictions = {m: p.as_dict() for m, p in calibrated.items()}
    return {
        "predictions": predictions,
        "physics": physics,
        "chemistry": chemistry,
        "drug_discovery": drug,
        "host": {"name": host, **host_cfg},
        "skills_used": ["skill.physics", "skill.chemistry", "skill.drug_discovery"],
        "citations": sorted(
            set(physics.get("citations", []) + chemistry.get("citations", []) + drug.get("citations", []))
        ),
    }


def apply_calibration(db: Session, rp: ResearchProject, prediction: Prediction) -> Prediction:
    """Shift the prediction by the measured bias and widen it by the residual spread."""
    model = next(
        (d for d in research_svc.drift_models(db, rp) if d.metric == prediction.metric), None
    )
    if model is None or model.n_observations == 0 or not isinstance(prediction.value, int | float):
        return prediction
    raw = float(prediction.value)
    corrected = raw + float(model.bias)
    sd = math.sqrt(prediction.sd**2 + float(model.residual_sd) ** 2)
    if model.n_observations >= 3 and model.residual_sd < prediction.sd:
        sd = float(model.residual_sd)  # measured spread beats the generic prior once we have data
    return Prediction(
        metric=prediction.metric,
        value=round(corrected, 4),
        sd=round(sd, 4),
        method=f"{prediction.method}; recalibrated with bias {model.bias:+.3f} over "
        f"{model.n_observations} measurement(s)",
        skill=prediction.skill,
        calibrated=True,
        raw_value=round(raw, 4),
    )


# ------------------------------------------------------------------------ risk


def risk_report(prediction: dict, commit: ProteinCommit) -> dict:
    """Named wet-lab risks with the failure mode each one produces."""
    preds = prediction["predictions"]
    chem = prediction["chemistry"]
    host = prediction["host"]
    cys = int(chem["metrics"].get("cysteine_count", {}).get("value", 0) or 0)
    pi = float(chem["metrics"].get("isoelectric_point", {}).get("value", 7.0) or 7.0)
    ptm = chem.get("ptm_sites", {})

    def level(metric: str) -> str:
        spec = wm.CANONICAL[metric]
        value = preds.get(metric, {}).get("value")
        if value is None:
            return "unknown"
        passed, _ = wm.evaluate(metric, value)
        if passed is None:
            return "unknown"
        if passed:
            margin = abs(float(value) - float(next(iter(spec["pass"].values()))))
            return "low" if margin > preds[metric]["sd"] else "medium"
        return "high"

    risks = [
        {"risk": "expression", "level": level("expression_yield"),
         "detail": f"predicted {preds['expression_yield']['value']} mg/L in {host['name']} at "
                   f"{host['temperature_c']} C, {host['media']}",
         "mitigation": "drop induction temperature, try autoinduction, or move to SHuffle/periplasm"},
        {"risk": "solubility", "level": level("soluble_fraction"),
         "detail": f"predicted soluble fraction {preds['soluble_fraction']['value']}%",
         "mitigation": "add solubility tag, raise ionic strength, keep pH >= 1 unit from pI"},
        {"risk": "purification", "level": level("purification_recovery"),
         "detail": f"pI {pi:.2f}; predicted recovery {preds['purification_recovery']['value']}%",
         "mitigation": chem.get("buffer_recommendation", {}).get("rationale", "")},
        {"risk": "stability", "level": level("melting_temperature"),
         "detail": f"predicted Tm {preds['melting_temperature']['value']} +/- "
                   f"{preds['melting_temperature']['sd']} C",
         "mitigation": "DSF screen of buffer/additives before committing to the full assay panel"},
        {"risk": "aggregation", "level": level("aggregation_hmw"),
         "detail": f"predicted HMW {preds['aggregation_hmw']['value']}%",
         "mitigation": "SEC after concentration; store dilute, avoid freeze-thaw"},
        {"risk": "disulfides_and_ptm", "level": "medium" if cys >= 2 or ptm else "low",
         "detail": f"{cys} cysteine(s); PTM motifs: "
                   + (", ".join(f"{k}@{v}" for k, v in ptm.items() if v) or "none"),
         "mitigation": "reducing buffer with TCEP, or oxidising host if disulfides are wanted"},
    ]
    if "kd" in preds:
        risks.append(
            {"risk": "binding_assay", "level": level("kd"),
             "detail": f"predicted KD {preds['kd']['value']} nM (relative anchor, ~1 log10 sd)",
             "mitigation": "BLI single-cycle kinetics with an orthogonal SPR confirmation"}
        )
    failure_modes = [
        f"{r['risk']}: {r['detail']}" for r in risks if r["level"] in {"high", "medium"}
    ]
    return {
        "risks": risks,
        "failure_modes": failure_modes,
        "host": host,
        "recommended_assays": recommended_assays(preds),
        "mutations": [m.get("mutation") for m in (commit.mutations or []) if m.get("mutation")],
    }


def recommended_assays(preds: dict) -> list[str]:
    """Only assays that can actually move a decision for this design."""
    chosen = ["expression_screen"]
    if "melting_temperature" in preds:
        chosen.append("dsf")
    if preds.get("aggregation_hmw", {}).get("value", 0) >= 2.0:
        chosen.append("sec")
    if "kd" in preds:
        chosen.append("bli")
    if preds.get("activity"):
        chosen.append("activity")
    return chosen


# ------------------------------------------------------------------------ plan


def build_plan(
    db: Session,
    rp: ResearchProject,
    project: Project,
    commit: ProteinCommit,
    *,
    host: str = "E. coli BL21(DE3)",
    event_id: str | None = None,
    provider: str = "local-simulation",
    devin_session_url: str | None = None,
    max_assays: int = 4,
) -> WetlabPlan:
    """A minimal pack ranked by information per dollar, with accept/reject thresholds."""
    target = cycle_svc.target_structure(project)
    target_pdb = target.to_pdb() if target is not None else None
    prediction = predict_metrics(db, rp, commit, host=host, target_pdb=target_pdb)
    preds = prediction["predictions"]
    risk = risk_report(prediction, commit)

    sequence = seqlib.clean_sequence(commit.sequence)
    parent_dna = seqlib.back_translate(sequence)
    primers = [
        packlib.mutagenesis_primers(parent_dna, m)
        for m in (commit.mutations or [])
        if m.get("mt") and m.get("position")
    ]
    cons = packlib.construct(sequence, commit.label or commit.id[:8])
    constructs = [
        {
            **cons,
            "route": "site-directed mutagenesis" if primers else "de-novo gene synthesis",
            "primers": primers,
            "build_cost_usd": round(
                (len(primers) * 105.0) if primers
                else packlib.COSTS["gene_synthesis_per_bp"] * cons["orf_length_bp"],
                2,
            ),
        }
    ]

    # rank assays by (information + exploration bonus) per dollar, using skill.math
    items = []
    for name in risk["recommended_assays"][: max_assays + 1]:
        spec = wm.ASSAY_CATALOG[name]
        measured = [m for m in spec["measures"] if m in preds]
        if not measured:
            continue
        # information ~ how uncertain we are, in units of the assay's own noise
        info = 0.0
        for metric in measured:
            assay_sd = max(wm.noise_sd(metric, preds[metric].get("value")), 1e-6)
            info += min(float(preds[metric]["sd"]) / assay_sd, 8.0)
        items.append(
            {"label": name, "value": round(info, 4), "sd": round(info * 0.25, 4),
             "cost_usd": spec["cost_usd"]}
        )
    ranking = skills.run(
        "skill.math", {"operation": "rank_under_cost", "items": items, "exploration": 1.0}
    )
    ranked = ranking["result"]["ranked"][:max_assays]

    assays = []
    thresholds = []
    for row in ranked:
        spec = wm.ASSAY_CATALOG[row["label"]]
        measured = [m for m in spec["measures"] if m in preds]
        assays.append(
            {
                "assay": row["label"],
                "readout": spec["readout"],
                "measures": measured,
                "cost_usd": spec["cost_usd"],
                "days": spec["days"],
                "rank": row["rank"],
                "information_per_usd": row["utility_per_usd"],
                "predicted": {m: preds[m] for m in measured},
            }
        )
        for metric in measured:
            spec_m = wm.CANONICAL[metric]
            rule = spec_m["pass"]
            thresholds.append(
                {
                    "metric": metric,
                    "unit": spec_m["unit"],
                    "accept": (
                        f"{'==' if 'equals' in rule else ('>=' if 'min' in rule else '<=')} "
                        f"{next(iter(rule.values()))}" if rule else "no hard rule"
                    ),
                    "predicted": preds[metric]["value"],
                    "predicted_sd": preds[metric]["sd"],
                    "assay_sd": round(wm.noise_sd(metric, preds[metric].get("value")), 4),
                    "decision": "reject and re-design if the measurement misses the rule by "
                                "more than two combined sigma",
                }
            )
    controls = [
        {"control": "parent/wild-type construct", "why": "paired comparison removes plate and "
         "operator effects; delta_tm is only meaningful on the same plate"},
        {"control": "buffer-only blank", "why": "baseline for DSF and SEC"},
        {"control": "known-expressing positive (GFP or MBP)", "why": "separates host failure from "
         "design failure"},
    ]
    total_cost = round(
        sum(a["cost_usd"] for a in assays) + sum(c["build_cost_usd"] for c in constructs), 2
    )
    info_total = sum(a["information_per_usd"] * a["cost_usd"] for a in assays)
    plan = WetlabPlan(
        research_project_id=rp.id,
        project_id=project.id,
        commit_id=commit.id,
        event_id=event_id,
        risk=risk,
        predictions=preds,
        constructs=constructs,
        assays=assays,
        controls=controls,
        thresholds=thresholds,
        failure_modes=risk["failure_modes"],
        total_cost_usd=total_cost,
        information_per_usd=round(info_total / total_cost, 6) if total_cost else 0.0,
        rationale=(
            f"{len(assays)} assay(s) chosen out of {len(wm.ASSAY_CATALOG)} because they are the "
            f"only ones whose readouts are still uncertain for this design; ranked by "
            f"(information + exploration bonus) / cost. Testing the full panel would cost "
            f"${sum(s['cost_usd'] for s in wm.ASSAY_CATALOG.values()):.0f} for "
            f"{len(wm.ASSAY_CATALOG)} assays."
        ),
        skills_used=prediction["skills_used"] + ["skill.math", "skill.wetlab_metrics"],
        citations=prediction["citations"] + list(wm.CITATIONS),
        provider=provider,
        devin_session_url=devin_session_url,
    )
    db.add(plan)
    db.flush()
    return plan


# ------------------------------------------------------------------- simulator


def simulate_results(
    db: Session,
    rp: ResearchProject,
    plan: WetlabPlan,
    *,
    seed: int | None = None,
    operator: str = "simulator",
    systematic_bias: dict[str, float] | None = None,
) -> WetlabResult:
    """Sample a measurement from the predicted distribution plus published assay noise.

    The error model is recorded on the row: total sd = sqrt(prediction sd^2 + assay sd^2), sampled
    from a normal (log-normal for KD and yield, Bernoulli for expression). This is a *simulation*,
    labelled as such everywhere it is displayed.

    ``systematic_bias`` adds a fixed per-metric offset on top of the draw, i.e. a virtual lab whose
    measurements sit consistently below the in-silico prediction. It is recorded verbatim in the
    error model so the offset is never mistaken for a real effect; the seeded demo campaign uses it
    to show that recalibration actually removes a systematic in-silico bias.
    """
    rng = random.Random(seed if seed is not None else 20240917 + len(plan.commit_id))
    bias = dict(systematic_bias or {})
    raw_rows: list[dict] = []
    error_model: dict = {"description": "measurement = prediction + N(0, sqrt(pred_sd^2 + assay_sd^2))",
                         "per_metric": {}, "seed": seed, "citations": list(wm.CITATIONS)}
    if bias:
        error_model["systematic_bias"] = {
            "offsets": bias,
            "note": "fixed offset injected by the simulator to represent in-silico optimism; "
                    "not a measurement and not a real-world effect",
        }
    metrics_to_sample: list[str] = []
    for assay in plan.assays:
        metrics_to_sample.extend(assay["measures"])
    for metric in dict.fromkeys(metrics_to_sample):
        pred = plan.predictions.get(metric)
        if not pred:
            continue
        value = pred["value"]
        assay_sd = wm.noise_sd(metric, value if isinstance(value, int | float) else None)
        pred_sd = float(pred.get("sd") or 0.0)
        total_sd = math.sqrt(pred_sd**2 + assay_sd**2)
        if metric == "expression":
            p = 0.85 if value else 0.35
            drawn: float | bool = rng.random() < p
            error_model["per_metric"][metric] = {"model": "bernoulli", "p": p}
        elif metric in {"kd", "expression_yield"}:
            # strictly positive quantities are perturbed multiplicatively
            rel = total_sd / max(abs(float(value)), 1e-6)
            drawn = round(max(float(value) * math.exp(rng.gauss(0.0, min(rel, 1.5))), 1e-6), 4)
            error_model["per_metric"][metric] = {"model": "log-normal", "sigma": round(rel, 4),
                                                "prediction_sd": pred_sd, "assay_sd": assay_sd}
        else:
            drawn = round(rng.gauss(float(value), total_sd), 3)
            error_model["per_metric"][metric] = {"model": "normal", "sd": round(total_sd, 4),
                                                "prediction_sd": pred_sd, "assay_sd": assay_sd}
        offset = float(bias.get(metric, 0.0))
        if offset and not isinstance(drawn, bool):
            floor = 1e-6 if metric in {"kd", "expression_yield"} else -1e9
            drawn = round(max(float(drawn) + offset, floor), 3)
            error_model["per_metric"][metric]["systematic_offset"] = offset
        raw_rows.append({"metric": metric, "value": drawn, "unit": pred.get("unit")})
    return ingest_results(
        db,
        rp,
        plan.commit_id,
        raw_rows,
        source="simulator",
        plan=plan,
        notes="honest simulator: sampled from the predicted distribution widened by published "
        "assay noise; not a real measurement",
        operator=operator,
        error_model=error_model,
    )


# ---------------------------------------------------------------- result intake


def parse_result_rows(text: str) -> list[dict]:
    """Accept a pasted CSV (metric,value,unit[,label]) or a JSON list/dict of measurements."""
    import csv
    import io
    import json

    text = (text or "").strip()
    if not text:
        return []
    if text[0] in "[{":
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("measurements") or [
                {"metric": k, "value": v} for k, v in data.items()
            ]
        return [dict(row) for row in data]
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames and "metric" in {(f or "").strip().lower() for f in reader.fieldnames}:
        rows = []
        for raw in reader:
            row = {(k or "").strip().lower(): v for k, v in raw.items() if k}
            rows.append(
                {
                    "metric": row.get("metric", ""),
                    "value": row.get("value"),
                    "unit": row.get("unit") or None,
                    "label": row.get("label") or "",
                    "notes": row.get("notes") or "",
                }
            )
        return rows
    # headerless "metric,value,unit" lines
    rows = []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0]:
            rows.append({"metric": parts[0], "value": parts[1],
                         "unit": parts[2] if len(parts) > 2 else None})
    return rows


def ingest_results(
    db: Session,
    rp: ResearchProject,
    commit_id: str,
    rows: list[dict],
    *,
    source: str = "scientist",
    plan: WetlabPlan | None = None,
    notes: str = "",
    operator: str = "",
    construct_label: str = "",
    error_model: dict | None = None,
    artifact_id: str | None = None,
) -> WetlabResult:
    """Normalise, residualise and commit results back onto the version they belong to."""
    normalised = skills.run(
        "skill.wetlab_metrics",
        {"measurements": [{"metric": r.get("metric", ""), "value": r.get("value"),
                           "unit": r.get("unit"), "label": r.get("label", "") or construct_label,
                           "notes": r.get("notes", "")} for r in rows]},
    )
    plan = plan or next(iter(research_svc.plans_for(db, rp, commit_id)), None)
    predictions = dict(plan.predictions) if plan else {}
    residuals = compute_residuals(predictions, normalised["measurements"])
    result = WetlabResult(
        research_project_id=rp.id,
        project_id=rp.project_id,
        commit_id=commit_id,
        plan_id=plan.id if plan else None,
        source=source,
        construct_label=construct_label or (plan.constructs[0]["label"] if plan and plan.constructs else ""),
        measurements=normalised["measurements"],
        residuals=residuals,
        error_model=error_model or {"description": "reported by the scientist; no error model applied"},
        notes=notes,
        raw={"rows": rows, "unknown_metrics": normalised["unknown"]},
        artifact_id=artifact_id,
        operator=operator,
    )
    db.add(result)
    if plan is not None:
        plan.status = "complete"
    db.flush()
    return result


def compute_residuals(predictions: dict, measurements: list[dict]) -> dict:
    """predicted vs measured for every metric, in raw units and in combined sigma."""
    residuals: dict[str, dict] = {}
    for row in measurements:
        metric, value = row["metric"], row["value"]
        pred = predictions.get(metric)
        if pred is None or value is None:
            continue
        if isinstance(value, bool) or isinstance(pred["value"], bool):
            residuals[metric] = {
                "predicted": pred["value"], "measured": value,
                "agreement": bool(pred["value"]) == bool(value), "residual": None, "z": None,
            }
            continue
        residual = float(value) - float(pred["value"])
        residuals[metric] = {
            "predicted": float(pred["value"]),
            "measured": float(value),
            "residual": round(residual, 4),
            "assay_sd": round(wm.noise_sd(metric, value), 4),
            "prediction_sd": float(pred.get("sd") or 0.0),
            "z": wm.z_score(metric, float(pred["value"]), float(value), float(pred.get("sd") or 0.0)),
        }
    return residuals


# ----------------------------------------------------------------- calibration


def update_drift(db: Session, rp: ResearchProject, result: WetlabResult) -> list[dict]:
    """Bayesian bias update plus an OLS calibration once enough pairs exist (skill.math)."""
    updated: list[dict] = []
    for metric, res in (result.residuals or {}).items():
        if res.get("residual") is None:
            continue
        model = next((d for d in research_svc.drift_models(db, rp) if d.metric == metric), None)
        if model is None:
            model = DriftModel(
                research_project_id=rp.id,
                metric=metric,
                prior_sd=float(res.get("prediction_sd") or 1.0) or 1.0,
                bias_sd=float(res.get("prediction_sd") or 1.0) or 1.0,
                method="normal-normal conjugate update of the bias; OLS calibration from 3 pairs",
            )
            db.add(model)
            db.flush()
        obs_sd = max(float(res.get("assay_sd") or 0.0), 1e-3)
        bayes = skills.run(
            "skill.math",
            {
                "operation": "bayesian_update",
                "prior_mean": float(model.bias),
                "prior_sd": float(model.bias_sd or model.prior_sd or 1.0),
                "observation": float(res["residual"]),
                "observation_sd": obs_sd,
            },
        )["result"]
        history = list(model.history or [])
        history.append(
            {
                "commit_id": result.commit_id,
                "source": result.source,
                "predicted": res["predicted"],
                "measured": res["measured"],
                "residual": res["residual"],
                "z": res.get("z"),
                "at": utcnow().isoformat(),
            }
        )
        model.history = history[-40:]
        model.n_observations = len(model.history)
        model.bias = round(float(bayes["posterior_mean"]), 4)
        model.bias_sd = round(float(bayes["posterior_sd"]), 4)
        pairs = [{"predicted": h["predicted"], "measured": h["measured"]} for h in model.history]
        if len(pairs) >= 3:
            cal = skills.run("skill.math", {"operation": "calibrate", "pairs": pairs})["result"]
            model.slope = round(float(cal["slope"]), 4)
            model.intercept = round(float(cal["intercept"]), 4)
            model.residual_sd = round(float(cal["residual_sd"]), 4)
            model.rmse = round(float(cal["rmse"]), 4)
        else:
            summary = skills.run(
                "skill.math",
                {"operation": "residual_summary",
                 "residuals": [h["residual"] for h in model.history]},
            )["result"]
            model.residual_sd = round(float(summary["sd"]), 4)
            model.rmse = round(float(summary["rmse"]), 4)
        model.updated_at = utcnow()
        updated.append(
            {
                "metric": metric,
                "n": model.n_observations,
                "bias": model.bias,
                "residual_sd": model.residual_sd,
                "rmse": model.rmse,
                "latest_residual": res["residual"],
            }
        )
    db.flush()
    return updated
