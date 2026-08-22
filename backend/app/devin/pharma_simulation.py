"""Explicitly-labelled offline fallback for the Pharmakon agent roles.

This is NOT Devin and never pretends to be. It runs only when `ALLOW_LOCAL_SIMULATION=true` and no
Devin credentials are configured, so the drug-discovery demo works on a laptop with no API key.
Every run produced this way records `provider="local-simulation"`.

The outputs are deterministic derivations from the same cheminformatics engine the real agents are
shown — analog enumeration, structural alerts, the ADMET panel, the PK model and the synthesis
heuristic. There is no language model here, and it is not a stand-in for one: it cannot search
literature, so citation-bearing roles return empty evidence rather than invented references.
"""

from __future__ import annotations

from app.chem.admet import admet_panel
from app.chem.alerts import alert_report
from app.chem.evaluate import evaluate_molecule
from app.chem.library import enumerate_analogs
from app.chem.pk import dose_projection
from app.chem.smiles import SmilesError, parse_smiles, scaffold_key
from app.chem.synthesis import synthesis_report

PROVIDER = "local-simulation"

SIM_NOTE = (
    "Generated offline by the deterministic local simulation, not by a Devin agent. No literature "
    "was searched; no citation is claimed."
)


def simulate_program_plan(*, stage: dict, gate: dict, evaluations: list[dict]) -> dict:
    """Plan a round by mapping the failing gate criteria onto the roles that own them."""
    owner = {
        "safety": "tox",
        "validity": "ip",
        "coverage": "medchem",
        "optimizable": "medchem",
    }
    metric_owner = {
        "best_admet_score": "admet",
        "best_hia": "admet",
        "best_half_life_h": "dmpk",
        "best_oral_dose_mg": "dmpk",
        "best_tox_margin": "tox",
        "best_herg_risk": "tox",
        "best_ames_risk": "tox",
        "best_hepatotoxicity_risk": "tox",
        "best_cyp_risk": "tox",
        "best_sa_score": "synthesis",
        "best_cost_per_gram_usd": "synthesis",
        "freedom_to_operate": "ip",
        "target_evidence_score": "target",
        "citation_count": "target",
        "starting_dose_defined": "clinical",
        "dossier_completeness": "clinical",
        "best_composite_score": "triage",
        "backup_composite_score": "triage",
    }
    failing = [c for c in gate.get("criteria", []) if not c.get("passed")]
    roles: list[str] = []
    for criterion in failing:
        role = metric_owner.get(criterion["key"]) or owner.get(criterion["kind"], "medchem")
        if role not in roles:
            roles.append(role)
    for role in stage.get("roles", []):
        if role in {"medchem", "triage"} and role not in roles:
            roles.append(role)
    roles = roles[:6] or ["medchem", "triage"]
    tasks = {
        "medchem": "Propose analogs of the leading molecules that move the failing gate metrics.",
        "admet": "Assess ADME liabilities of the portfolio and propose concrete fixes.",
        "tox": "Review the portfolio for safety liabilities and name the assay that settles each.",
        "dmpk": "Propose a dose regimen per leading molecule and name the dose-limiting property.",
        "synthesis": "Propose a route per leading molecule and score scale-up feasibility.",
        "ip": "Assess freedom to operate for the current chemistry and identify whitespace.",
        "clinical": "Derive a starting dose and stopping rules from the projected exposure.",
        "target": "Assemble the target-disease evidence and locate the ligand-binding site.",
        "triage": "Order the portfolio and recommend a candidate and a back-up.",
    }
    return {
        "strategy": (
            f"[{PROVIDER}] Stage {stage.get('key')}: gate score {gate.get('score')}. Close the "
            f"{len(failing)} open criteria by assigning each to the role that owns it."
        ),
        "hypotheses": [
            f"{c['label']} can be moved from {c['observed']} to {c['requirement']}" for c in failing[:4]
        ],
        "gate_focus": [c["key"] for c in failing],
        "agents": [{"role": r, "task": tasks[r], "acu_limit": 6} for r in roles],
        "success_criteria": [f"{c['key']} {c['requirement']}" for c in failing],
        "experiments_requested": [
            c["key"] for c in failing if c.get("blocking") and c.get("kind") == "coverage"
        ],
        "open_questions": [SIM_NOTE],
    }


def simulate(role: str, *, evaluations: list[dict], pocket: dict | None = None) -> dict:
    """Deterministic structured output for one Pharmakon child role."""
    if role == "medchem":
        return _medchem(evaluations, pocket)
    if role == "admet":
        return _liabilities(evaluations, safety_only=False)
    if role == "tox":
        return _liabilities(evaluations, safety_only=True)
    if role == "dmpk":
        return _dmpk(evaluations)
    if role == "synthesis":
        return _synthesis(evaluations)
    if role == "ip":
        return _ip(evaluations)
    if role == "clinical":
        return _clinical(evaluations)
    if role == "triage":
        return _triage(evaluations)
    if role == "target":
        return _target(pocket)
    raise ValueError(f"unknown pharma role {role!r}")


def _leads(evaluations: list[dict], n: int = 3) -> list[dict]:
    return sorted(evaluations, key=lambda e: -(e.get("composite_score") or 0.0))[:n]


def _medchem(evaluations: list[dict], pocket: dict | None) -> dict:
    molecules: list[dict] = []
    seen: set[str] = set()
    for parent in _leads(evaluations, 2):
        parent_smiles = parent.get("smiles")
        if not parent_smiles:
            continue
        for analog in enumerate_analogs(parent_smiles, max_analogs=8):
            smiles = analog["smiles"]
            if smiles in seen:
                continue
            seen.add(smiles)
            molecules.append(
                {
                    "label": f"{analog['molecule_hash'][:8]}-{analog['transform']}"[:60],
                    "smiles": smiles,
                    "parent_smiles": parent_smiles,
                    "series": scaffold_key(smiles),
                    "rationale": f"[{PROVIDER}] {analog['transform']}: {analog['rationale']}",
                    "confidence": 0.3,
                }
            )
    if not molecules:
        molecules = [
            {
                "label": "unchanged-parent",
                "smiles": (evaluations[0] if evaluations else {}).get("smiles", "c1ccccc1"),
                "rationale": f"[{PROVIDER}] no enumerable modification site found",
                "confidence": 0.1,
            }
        ]
    return {
        "molecules": molecules[:12],
        "sar_analysis": (
            f"[{PROVIDER}] Deterministic analog enumeration (substituent scan, bioisosteres, "
            "scaffold decoration) around the top-scoring molecules. No SAR model was learned."
        ),
        "open_questions": [SIM_NOTE],
    }


def _liabilities(evaluations: list[dict], *, safety_only: bool) -> dict:
    assessments = []
    for ev in _leads(evaluations, 4):
        smiles = ev.get("smiles")
        if not smiles:
            continue
        alerts = ev.get("liabilities") or alert_report(smiles)
        panel = ev.get("admet") or admet_panel(smiles)
        blocking_codes = set(alerts.get("blocking") or [])
        items = []
        for alert in alerts.get("alerts", []):
            items.append(
                {
                    "issue": alert.get("label", alert.get("code", "structural alert")),
                    "severity": (
                        "blocking" if alert.get("code") in blocking_codes else alert.get("severity", "medium")
                    ),
                    "mechanism": alert.get("category", ""),
                    "mitigation": alert.get("mitigation", "remove or mask the flagged substructure"),
                }
            )
        if safety_only:
            herg = (panel.get("toxicity") or {}).get("herg") or {}
            if (herg.get("risk") or 0) > 0.3:
                items.append(
                    {
                        "issue": "hERG inhibition risk",
                        "severity": "high" if herg.get("risk", 0) > 0.5 else "medium",
                        "mechanism": "basic centre plus lipophilic aromatic surface",
                        "mitigation": "lower logP or reduce basicity of the amine",
                    }
                )
        else:
            absorption = panel.get("absorption") or {}
            if (absorption.get("human_intestinal_absorption") or 1.0) < 0.6:
                items.append(
                    {
                        "issue": "low predicted intestinal absorption",
                        "severity": "medium",
                        "mechanism": "polar surface area and hydrogen-bond donor count",
                        "mitigation": "cap donors, reduce TPSA, or consider a prodrug",
                    }
                )
        assessments.append(
            {
                "smiles": smiles,
                "liabilities": items,
                "recommended_modifications": [
                    i["mitigation"] for i in items if i.get("mitigation")
                ][:4],
                "confidence": 0.35,
            }
        )
    if not assessments:
        assessments = [{"smiles": "", "liabilities": [], "confidence": 0.0}]
    return {
        "assessments": assessments,
        "analysis": f"[{PROVIDER}] Derived from structural alerts and the deterministic ADMET panel.",
        "open_questions": [SIM_NOTE],
    }


def _dmpk(evaluations: list[dict]) -> dict:
    assessments = []
    for ev in _leads(evaluations, 3):
        smiles = ev.get("smiles")
        binding = ev.get("binding") or {}
        if not smiles:
            continue
        dose = ev.get("dose_projection")
        if dose is None and binding.get("kd_nm"):
            dose = dose_projection(smiles, potency_nm=binding["kd_nm"])
        panel = ev.get("admet") or admet_panel(smiles)
        excretion = panel.get("excretion") or {}
        cl = excretion.get("cl_total_l_per_h_per_kg") or 0.0
        band = "low" if cl < 0.3 else "moderate" if cl < 0.9 else "high"
        risks: list[str] = []
        if dose and not dose.get("feasible_oral_dose", True):
            risks.append("projected efficacious dose exceeds a practical oral dose")
        if (excretion.get("half_life_h") or 0.0) < 3.0:
            risks.append("short predicted half-life: once-daily coverage unlikely")
        assessments.append(
            {
                "smiles": smiles,
                "regimen": dose.get("regimen") if dose else "not projectable without a potency estimate",
                "clearance_band": band,
                "exposure_risks": risks,
                "dose_limiting_property": (
                    "clearance" if band == "high" else "potency" if dose else "unknown"
                ),
                "confidence": 0.3,
            }
        )
    return {
        "assessments": assessments or [{"smiles": "", "regimen": "no molecules to assess"}],
        "analysis": (
            f"[{PROVIDER}] One-compartment first-order model over predicted physicochemical "
            "parameters. Not a validated human PK prediction."
        ),
        "open_questions": [SIM_NOTE],
    }


def _synthesis(evaluations: list[dict]) -> dict:
    routes = []
    for ev in _leads(evaluations, 3):
        smiles = ev.get("smiles")
        if not smiles:
            continue
        report = ev.get("synthesis") or synthesis_report(smiles)
        steps = report.get("route") or []
        risk_band = report.get("scale_up_risk", "medium")
        routes.append(
            {
                "smiles": smiles,
                "steps": [
                    {
                        "transformation": step.get("reaction", step.get("disconnection", "assembly")),
                        "expected_yield_pct": round(100.0 * report.get("overall_yield", 0.7), 1),
                        "risk": "heuristic estimate, not a retrosynthesis",
                    }
                    for step in steps
                ]
                or [{"transformation": "single-step assembly from a commercial building block"}],
                "building_blocks": [step.get("disconnection", "") for step in steps],
                "scale_up_feasibility": {"low": 0.8, "medium": 0.5, "high": 0.25}.get(risk_band, 0.5),
                "confidence": 0.25,
            }
        )
    return {
        "routes": routes or [{"smiles": "", "steps": [{"transformation": "no molecules"}]}],
        "analysis": (
            f"[{PROVIDER}] Fragment/complexity heuristic, not a reaction-database retrosynthesis."
        ),
        "open_questions": [SIM_NOTE],
    }


def _ip(evaluations: list[dict]) -> dict:
    return {
        "freedom_to_operate": 0.5,
        "prior_art": [],
        "claim_strategy": (
            f"[{PROVIDER}] Cannot be assessed offline: no patent database is reachable. A neutral "
            "0.5 is reported so the gate neither passes nor fails on invented evidence."
        ),
        "whitespace": [f"series {ev.get('scaffold_key')}" for ev in _leads(evaluations, 2)],
        "caveat": "Not legal advice and not a freedom-to-operate opinion. " + SIM_NOTE,
        "open_questions": [SIM_NOTE],
    }


def _clinical(evaluations: list[dict]) -> dict:
    lead = _leads(evaluations, 1)
    dose = (lead[0].get("dose_projection") if lead else None) or {}
    projected = dose.get("projected_dose_mg")
    return {
        "indication": "as specified in the program record",
        "patient_population": "adult patients with the program indication, no competing therapy",
        "starting_dose_mg": round(projected / 10.0, 2) if projected else 0.0,
        "starting_dose_basis": (
            f"[{PROVIDER}] one tenth of the projected efficacious dose "
            f"({projected} mg) as a placeholder safety factor. This is NOT an MRSD derivation: a "
            "real starting dose requires a GLP NOAEL and allometric scaling."
        ),
        "escalation_scheme": "single ascending dose, 3+3 cohorts, doubling until predefined stop",
        "endpoints": ["safety and tolerability", "pharmacokinetics", "target engagement biomarker"],
        "stopping_rules": [
            "any dose-limiting toxicity in two subjects in a cohort",
            "exposure exceeding the projected safety margin",
        ],
        "risks": ["no GLP toxicology exists", "target engagement biomarker unvalidated"],
        "citations": [],
        "open_questions": [SIM_NOTE],
    }


def _triage(evaluations: list[dict]) -> dict:
    ranked = sorted(evaluations, key=lambda e: -(e.get("composite_score") or 0.0))
    ordering = [
        {
            "smiles": ev.get("smiles"),
            "reason": (
                f"[{PROVIDER}] composite {ev.get('composite_score')} "
                f"(verdict: {ev.get('verdict')})"
            ),
            "risk": ", ".join(
                a.get("label", "") for a in (ev.get("liabilities") or {}).get("alerts", [])
            )[:200],
        }
        for ev in ranked
        if not (ev.get("liabilities") or {}).get("blocking")
    ]
    exclusions = [
        {
            "smiles": ev.get("smiles"),
            "reason": "blocking structural alert: "
            + ", ".join((ev.get("liabilities") or {}).get("blocking", [])),
        }
        for ev in ranked
        if (ev.get("liabilities") or {}).get("blocking")
    ]
    return {
        "ordering": ordering or [{"smiles": "", "reason": "no rankable molecules"}],
        "exclusions": exclusions,
        "recommended_candidate": ordering[0]["smiles"] if ordering else "",
        "recommended_backup": ordering[1]["smiles"] if len(ordering) > 1 else "",
        "gate_readiness": (
            f"[{PROVIDER}] Ordering follows the deterministic composite score only; no judgement "
            "about the gate was applied."
        ),
        "analysis": SIM_NOTE,
        "open_questions": [SIM_NOTE],
    }


def _target(pocket: dict | None) -> dict:
    return {
        "target_evidence_score": 0.5,
        "disease_hypothesis": (
            f"[{PROVIDER}] Cannot be assessed offline: no literature source is reachable. A "
            "neutral 0.5 is reported rather than an invented evidence score."
        ),
        "findings": [
            {
                "claim": "No literature search was performed by the offline simulation.",
                "citation": "n/a - local simulation",
                "evidence_class": "other",
                "implication": "the target-assessment gate cannot legitimately pass on this output",
            }
        ],
        "pocket_residues": list((pocket or {}).get("residue_indices") or []),
        "risks": ["target evidence unverified"],
        "open_questions": [SIM_NOTE],
    }


def is_valid_molecule(smiles: str) -> bool:
    try:
        parse_smiles(smiles)
    except SmilesError:
        return False
    return True


def evaluate_proposals(
    molecules: list[dict], *, pocket: dict | None, stage: str
) -> tuple[list[dict], list[dict]]:
    """Evaluate agent-proposed molecules, separating unparseable proposals from valid ones."""
    good: list[dict] = []
    bad: list[dict] = []
    for item in molecules:
        smiles = (item or {}).get("smiles") or ""
        try:
            evaluation = evaluate_molecule(smiles, pocket=pocket, stage=stage)
        except (SmilesError, ValueError) as exc:
            bad.append({"smiles": smiles, "error": str(exc)})
            continue
        evaluation["proposal"] = item
        good.append(evaluation)
    return good, bad
