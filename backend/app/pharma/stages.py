"""The Pharmakon program ladder: stages, gate criteria and autonomy rules.

A drug program is a state machine over eight stages. Each stage declares
  * the specialist agent roles that work it,
  * the deterministic gate criteria that must hold before the program may advance,
  * the autonomy level Pharmakon needs before it may act on its own decision, and
  * the human-team cost/duration baseline used by :mod:`app.pharma.economics`.

Criteria are evaluated against the metric dictionary produced by
:func:`app.pharma.metrics.program_metrics`, so a gate is a pure function of recorded
evidence — never of agent prose. ``kind`` decides what a failure means:

``optimizable``  the property can plausibly be fixed by another design round -> recycle
``safety``       a safety liability that disqualifies the current chemistry -> kill/recycle
``validity``     the premise of the program itself is unsupported -> kill
``coverage``     not enough evidence has been gathered yet -> recycle (keep working)
"""

from __future__ import annotations

from dataclasses import dataclass

# Autonomy levels. The program's level is the *maximum* Pharmakon may do unattended.
AUTONOMY_LEVELS: dict[int, str] = {
    0: "L0 advisory — Pharmakon recommends, a human executes every decision",
    1: "L1 self-recycling — may re-run design rounds, never changes stage alone",
    2: "L2 discovery-autonomous — may advance itself up to lead optimisation",
    3: "L3 preclinical-autonomous — may advance itself up to candidate selection",
    4: "L4 program-autonomous — may advance every stage that is not human-facing",
}

# Stages that always need a human signature, whatever the autonomy level: they commit the
# organisation to regulatory filings or to exposing humans to a compound.
HUMAN_SIGNED_STAGES = ("ind_enabling", "trial_design")

DECISIONS = ("go", "no_go", "recycle", "kill")


@dataclass(frozen=True)
class Criterion:
    """One deterministic gate check over the program metric dictionary."""

    key: str
    label: str
    comparator: str  # ">=" | "<=" | "==" | ">" | "<"
    threshold: float
    blocking: bool = False
    weight: float = 1.0
    kind: str = "optimizable"  # optimizable|safety|validity|coverage
    unit: str = ""
    rationale: str = ""

    def check(self, metrics: dict[str, float]) -> dict:
        value = metrics.get(self.key)
        if value is None:
            passed = False
            observed: float | None = None
        else:
            observed = float(value)
            passed = _compare(observed, self.comparator, self.threshold)
        return {
            "key": self.key,
            "label": self.label,
            "requirement": f"{self.comparator} {self.threshold}{(' ' + self.unit) if self.unit else ''}",
            "observed": observed,
            "passed": passed,
            "blocking": self.blocking,
            "weight": self.weight,
            "kind": self.kind,
            "rationale": self.rationale,
            "missing_evidence": observed is None,
        }


def _compare(value: float, comparator: str, threshold: float) -> bool:
    if comparator == ">=":
        return value >= threshold
    if comparator == "<=":
        return value <= threshold
    if comparator == ">":
        return value > threshold
    if comparator == "<":
        return value < threshold
    if comparator == "==":
        return abs(value - threshold) < 1e-9
    raise ValueError(f"unsupported comparator {comparator!r}")


@dataclass(frozen=True)
class Stage:
    key: str
    order: int
    name: str
    objective: str
    roles: tuple[str, ...]
    criteria: tuple[Criterion, ...]
    min_autonomy: int
    scoring_stage: str  # weight set used by app.chem.evaluate
    human_team_cost_usd: float
    human_team_months: float
    historical_pos: float  # industry probability of surviving this stage
    exit_deliverable: str

    @property
    def human_signature_required(self) -> bool:
        return self.key in HUMAN_SIGNED_STAGES


STAGES: tuple[Stage, ...] = (
    Stage(
        key="target_assessment",
        order=1,
        name="Target assessment",
        objective=(
            "Establish that the target is druggable, that the disease hypothesis is supported, "
            "and that there is a defensible position to work in."
        ),
        roles=("target", "ip", "clinical"),
        criteria=(
            Criterion(
                key="target_evidence_score",
                label="Target-disease evidence",
                comparator=">=",
                threshold=0.55,
                blocking=True,
                weight=2.0,
                kind="validity",
                rationale="Genetic/functional/clinical evidence assembled by the literature agent.",
            ),
            Criterion(
                key="druggability_score",
                label="Pocket druggability proxy",
                comparator=">=",
                threshold=0.4,
                blocking=True,
                weight=1.5,
                kind="validity",
                rationale="Deterministic pocket descriptor from the target sequence.",
            ),
            Criterion(
                key="freedom_to_operate",
                label="Freedom-to-operate assessment",
                comparator=">=",
                threshold=0.4,
                weight=1.0,
                kind="validity",
                rationale="IP agent's assessment of composition-of-matter space left open.",
            ),
            Criterion(
                key="citation_count",
                label="Cited evidence items",
                comparator=">=",
                threshold=3,
                weight=0.5,
                kind="coverage",
            ),
        ),
        min_autonomy=2,
        scoring_stage="hit_finding",
        human_team_cost_usd=250_000,
        human_team_months=4,
        historical_pos=0.75,
        exit_deliverable="Target dossier: evidence, druggability, competitive and IP landscape.",
    ),
    Stage(
        key="hit_finding",
        order=2,
        name="Hit finding",
        objective="Find chemically tractable starting points that engage the target pocket.",
        roles=("medchem", "target", "triage"),
        criteria=(
            Criterion(
                key="hit_count",
                label="Distinct hits (pKd proxy >= 6)",
                comparator=">=",
                threshold=3,
                blocking=True,
                weight=2.0,
                kind="coverage",
            ),
            Criterion(
                key="series_count",
                label="Independent chemical series",
                comparator=">=",
                threshold=2,
                weight=1.0,
                kind="coverage",
                rationale="One series is single-point-of-failure risk.",
            ),
            Criterion(
                key="best_ligand_efficiency",
                label="Best ligand efficiency",
                comparator=">=",
                threshold=0.28,
                weight=1.0,
                unit="kcal/mol/HA",
            ),
            Criterion(
                key="clean_molecule_count",
                label="Hits free of blocking structural alerts",
                comparator=">=",
                threshold=2,
                blocking=True,
                weight=1.5,
                kind="safety",
            ),
        ),
        min_autonomy=2,
        scoring_stage="hit_finding",
        human_team_cost_usd=1_800_000,
        human_team_months=10,
        historical_pos=0.6,
        exit_deliverable="Hit list with binding-proxy evidence, alerts triage and series map.",
    ),
    Stage(
        key="hit_to_lead",
        order=3,
        name="Hit to lead",
        objective="Turn hits into lead series with real potency, selectivity and a synthetic route.",
        roles=("medchem", "admet", "synthesis", "triage"),
        criteria=(
            Criterion(
                key="best_pkd",
                label="Best potency proxy (pKd)",
                comparator=">=",
                threshold=7.0,
                blocking=True,
                weight=2.0,
            ),
            Criterion(
                key="best_selectivity_fold",
                label="Selectivity over profiled off-targets",
                comparator=">=",
                threshold=10.0,
                weight=1.5,
                unit="fold",
            ),
            Criterion(
                key="best_qed_like",
                label="Drug-likeness (QED-like)",
                comparator=">=",
                threshold=0.5,
                weight=1.0,
            ),
            Criterion(
                key="best_sa_score",
                label="Synthetic accessibility of the lead",
                comparator="<=",
                threshold=6.0,
                weight=1.0,
            ),
            Criterion(
                key="blocking_alert_free",
                label="Lead free of blocking alerts",
                comparator=">=",
                threshold=1,
                blocking=True,
                weight=2.0,
                kind="safety",
            ),
        ),
        min_autonomy=2,
        scoring_stage="hit_to_lead",
        human_team_cost_usd=3_500_000,
        human_team_months=12,
        historical_pos=0.55,
        exit_deliverable="Lead series with SAR table, selectivity profile and route sketch.",
    ),
    Stage(
        key="lead_optimization",
        order=4,
        name="Lead optimisation",
        objective="Optimise potency, ADME and safety simultaneously into a development-ready lead.",
        roles=("medchem", "admet", "dmpk", "tox", "triage"),
        criteria=(
            Criterion(
                key="best_pkd",
                label="Best potency proxy (pKd)",
                comparator=">=",
                threshold=7.8,
                blocking=True,
                weight=2.0,
            ),
            Criterion(
                key="best_admet_score",
                label="Composite ADMET score",
                comparator=">=",
                threshold=0.6,
                blocking=True,
                weight=2.0,
            ),
            Criterion(
                key="best_herg_risk",
                label="hERG risk of the lead",
                comparator="<=",
                threshold=0.35,
                blocking=True,
                weight=1.5,
                kind="safety",
            ),
            Criterion(
                key="best_half_life_h",
                label="Projected human half-life",
                comparator=">=",
                threshold=4.0,
                weight=1.0,
                unit="h",
            ),
            Criterion(
                key="best_hia",
                label="Predicted human intestinal absorption",
                comparator=">=",
                threshold=0.6,
                weight=1.0,
            ),
            Criterion(
                key="assay_confirmed_count",
                label="Designs with ingested experimental results",
                comparator=">=",
                threshold=1,
                weight=1.0,
                kind="coverage",
                rationale="Lead optimisation without a single measured data point is unanchored.",
            ),
        ),
        min_autonomy=3,
        scoring_stage="lead_optimization",
        human_team_cost_usd=8_000_000,
        human_team_months=18,
        historical_pos=0.5,
        exit_deliverable="Optimised lead with in-vitro/in-vivo package and a back-up series.",
    ),
    Stage(
        key="dmpk_safety",
        order=5,
        name="DMPK & safety",
        objective="Establish exposure, dose projection and a defensible safety margin.",
        roles=("dmpk", "tox", "admet"),
        criteria=(
            Criterion(
                key="best_tox_margin",
                label="Projected therapeutic margin",
                comparator=">=",
                threshold=10.0,
                blocking=True,
                weight=2.5,
                kind="safety",
                unit="fold",
            ),
            Criterion(
                key="best_oral_dose_mg",
                label="Projected human oral dose",
                comparator="<=",
                threshold=800.0,
                weight=1.5,
                unit="mg/day",
            ),
            Criterion(
                key="best_ames_risk",
                label="Genotoxicity (Ames) risk",
                comparator="<=",
                threshold=0.25,
                blocking=True,
                weight=2.0,
                kind="safety",
            ),
            Criterion(
                key="best_cyp_risk",
                label="Worst CYP inhibition probability",
                comparator="<=",
                threshold=0.5,
                weight=1.0,
                kind="safety",
            ),
            Criterion(
                key="best_hepatotoxicity_risk",
                label="Hepatotoxicity risk proxy",
                comparator="<=",
                threshold=0.35,
                blocking=True,
                weight=1.5,
                kind="safety",
            ),
        ),
        min_autonomy=3,
        scoring_stage="candidate_selection",
        human_team_cost_usd=4_500_000,
        human_team_months=10,
        historical_pos=0.6,
        exit_deliverable="DMPK/tox package: exposure, margins, dose projection, liabilities.",
    ),
    Stage(
        key="candidate_selection",
        order=6,
        name="Candidate selection",
        objective="Pick one development candidate and one back-up, on the record, with reasons.",
        roles=("triage", "dmpk", "tox", "synthesis", "ip", "clinical"),
        criteria=(
            Criterion(
                key="best_composite_score",
                label="Composite candidate score",
                comparator=">=",
                threshold=0.62,
                blocking=True,
                weight=2.0,
            ),
            Criterion(
                key="backup_composite_score",
                label="Back-up candidate score",
                comparator=">=",
                threshold=0.5,
                weight=1.5,
                kind="coverage",
            ),
            Criterion(
                key="best_cost_per_gram_usd",
                label="Projected API cost",
                comparator="<=",
                threshold=1500.0,
                weight=0.75,
                unit="USD/g",
            ),
            Criterion(
                key="freedom_to_operate",
                label="Freedom to operate on the candidate",
                comparator=">=",
                threshold=0.5,
                blocking=True,
                weight=1.5,
                kind="validity",
            ),
            Criterion(
                key="prediction_drift_rmse",
                label="Prediction drift vs measured data (pKd RMSE)",
                comparator="<=",
                threshold=1.5,
                weight=1.0,
                kind="coverage",
                rationale="A model that cannot predict its own assays cannot pick a candidate.",
            ),
        ),
        min_autonomy=4,
        scoring_stage="candidate_selection",
        human_team_cost_usd=2_000_000,
        human_team_months=6,
        historical_pos=0.7,
        exit_deliverable="Candidate selection memo: candidate, back-up, risks, kill criteria.",
    ),
    Stage(
        key="ind_enabling",
        order=7,
        name="IND-enabling package",
        objective="Assemble the nonclinical package and CMC plan needed to file.",
        roles=("tox", "dmpk", "synthesis", "clinical", "ip"),
        criteria=(
            Criterion(
                key="best_tox_margin",
                label="Safety margin at the projected clinical dose",
                comparator=">=",
                threshold=15.0,
                blocking=True,
                weight=2.5,
                kind="safety",
                unit="fold",
            ),
            Criterion(
                key="dossier_completeness",
                label="Dossier section completeness",
                comparator=">=",
                threshold=0.85,
                blocking=True,
                weight=2.0,
                kind="coverage",
            ),
            Criterion(
                key="scale_up_feasibility",
                label="CMC scale-up feasibility",
                comparator=">=",
                threshold=0.5,
                weight=1.0,
            ),
            Criterion(
                key="assay_confirmed_count",
                label="Experimentally confirmed designs",
                comparator=">=",
                threshold=3,
                blocking=True,
                weight=2.0,
                kind="coverage",
                rationale="No filing package may rest on predictions alone.",
            ),
        ),
        min_autonomy=4,
        scoring_stage="candidate_selection",
        human_team_cost_usd=12_000_000,
        human_team_months=16,
        historical_pos=0.65,
        exit_deliverable="IND-style dossier draft: CMC, pharmacology, DMPK, toxicology, plan.",
    ),
    Stage(
        key="trial_design",
        order=8,
        name="First-in-human trial design",
        objective="Propose a first-in-human design with starting dose and stopping rules.",
        roles=("clinical", "dmpk", "tox"),
        criteria=(
            Criterion(
                key="starting_dose_defined",
                label="Starting dose derived from exposure and margins",
                comparator=">=",
                threshold=1,
                blocking=True,
                weight=2.0,
                kind="coverage",
            ),
            Criterion(
                key="best_tox_margin",
                label="Margin at the proposed starting dose",
                comparator=">=",
                threshold=15.0,
                blocking=True,
                weight=2.5,
                kind="safety",
                unit="fold",
            ),
            Criterion(
                key="dossier_completeness",
                label="Dossier completeness",
                comparator=">=",
                threshold=0.9,
                blocking=True,
                weight=1.5,
                kind="coverage",
            ),
        ),
        min_autonomy=4,
        scoring_stage="candidate_selection",
        human_team_cost_usd=6_000_000,
        human_team_months=8,
        historical_pos=0.9,
        exit_deliverable="Protocol synopsis: population, dose escalation, endpoints, stopping rules.",
    ),
)

STAGE_BY_KEY: dict[str, Stage] = {s.key: s for s in STAGES}
STAGE_KEYS: tuple[str, ...] = tuple(s.key for s in STAGES)
FIRST_STAGE = STAGES[0].key
TERMINAL_STAGE = STAGES[-1].key


def stage(key: str) -> Stage:
    try:
        return STAGE_BY_KEY[key]
    except KeyError as exc:
        raise ValueError(f"unknown program stage {key!r}") from exc


def next_stage(key: str) -> str | None:
    current = stage(key)
    if current.order >= len(STAGES):
        return None
    return STAGES[current.order].key


def ladder() -> list[dict]:
    """Serialisable description of the ladder for the API and the UI."""
    return [
        {
            "key": s.key,
            "order": s.order,
            "name": s.name,
            "objective": s.objective,
            "roles": list(s.roles),
            "min_autonomy": s.min_autonomy,
            "human_signature_required": s.human_signature_required,
            "exit_deliverable": s.exit_deliverable,
            "human_team_cost_usd": s.human_team_cost_usd,
            "human_team_months": s.human_team_months,
            "historical_pos": s.historical_pos,
            "criteria": [
                {
                    "key": c.key,
                    "label": c.label,
                    "requirement": f"{c.comparator} {c.threshold}",
                    "blocking": c.blocking,
                    "kind": c.kind,
                    "unit": c.unit,
                    "rationale": c.rationale,
                }
                for c in s.criteria
            ],
        }
        for s in STAGES
    ]
