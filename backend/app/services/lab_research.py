"""The Research Module: one campaign per project, append-only knowledge, never discarded.

Everything the daemon learns lands here as immutable rows:

* :class:`LabResearchEvent` — an ordered, append-only ledger of every research pass;
* :class:`ResearchPaper` — the paper cache, written once per (campaign, paper) and never rewritten;
* :class:`Hypothesis` — falsifiable predictions that wet-lab results later resolve;
* :class:`DriftModel` — per-metric in-silico vs wet-lab calibration.

The digest built here is what makes the loop *continuous*: a research pass on version N reads the
whole path v1..vN plus the cached corpus, so the next proposal is informed by everything before it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import skills
from app.models import (
    DriftModel,
    Hypothesis,
    LabResearchEvent,
    MetricDefinition,
    Project,
    ProteinCommit,
    ResearchPaper,
    ResearchProject,
    ResidueLabel,
    WetlabPlan,
    WetlabResult,
    utcnow,
)
from app.skills import seed_corpus
from app.skills import wetlab_metrics as wm
from app.toolkit import sequence as seqlib

logger = logging.getLogger(__name__)

EVENT_KINDS = (
    "campaign_opened",
    "version_created",
    "label_added",
    "diff_detected",
    "handoff",
    "literature",
    "metrics",
    "wetlab_plan",
    "results",
    "learn",
    "insight",
    "proposal",
    "queued",
    "degraded",
)


# --------------------------------------------------------------------- campaign


def ensure_research_project(db: Session, project: Project) -> ResearchProject:
    rp = db.scalar(select(ResearchProject).where(ResearchProject.project_id == project.id))
    if rp is not None:
        return rp
    rp = ResearchProject(
        project_id=project.id,
        name=f"{project.name} campaign",
        question=project.goal,
    )
    db.add(rp)
    db.flush()
    append_event(
        db,
        rp,
        kind="campaign_opened",
        summary=f"Research campaign opened for {project.name}",
        payload={"goal": project.goal, "target": project.target_name},
        trigger="system",
    )
    upsert_metric_definitions(db)
    return rp


def upsert_metric_definitions(db: Session) -> int:
    """Publish the canonical metric vocabulary from skill.wetlab_metrics into the database."""
    written = 0
    for name, spec in wm.CANONICAL.items():
        row = db.get(MetricDefinition, name)
        if row is None:
            row = MetricDefinition(name=name)
            db.add(row)
            written += 1
        row.unit = spec["unit"]
        row.higher_is_better = bool(spec["higher_is_better"])
        row.assay = spec["assay"]
        row.assay_sd = float(spec["assay_sd"])
        row.sd_kind = spec["sd_kind"]
        row.pass_rule = dict(spec["pass"])
        row.citations = list(wm.CITATIONS)
    db.flush()
    return written


# ------------------------------------------------------------------ event ledger


def append_event(
    db: Session,
    rp: ResearchProject,
    *,
    kind: str,
    summary: str,
    payload: dict | None = None,
    commit_id: str | None = None,
    role: str = "research-daemon",
    provider: str = "local-simulation",
    trigger: str = "daemon",
    skills_used: list[str] | None = None,
    citations: list[str] | None = None,
    devin_session_id: str | None = None,
    devin_session_url: str | None = None,
    parent_event_id: str | None = None,
) -> LabResearchEvent:
    rp.event_sequence = int(rp.event_sequence or 0) + 1
    event = LabResearchEvent(
        research_project_id=rp.id,
        project_id=rp.project_id,
        commit_id=commit_id,
        sequence_no=rp.event_sequence,
        kind=kind,
        trigger=trigger,
        role=role,
        summary=summary[:4000],
        payload=payload or {},
        skills_used=skills_used or [],
        citations=citations or [],
        provider=provider,
        devin_session_id=devin_session_id,
        devin_session_url=devin_session_url,
        parent_event_id=parent_event_id,
    )
    db.add(event)
    db.flush()
    return event


def events(db: Session, rp: ResearchProject, limit: int = 60) -> list[LabResearchEvent]:
    return list(
        db.scalars(
            select(LabResearchEvent)
            .where(LabResearchEvent.research_project_id == rp.id)
            .order_by(LabResearchEvent.sequence_no.desc())
            .limit(limit)
        )
    )


# ------------------------------------------------------------------ paper cache


def cache_papers(
    db: Session,
    rp: ResearchProject,
    literature: dict,
    *,
    query: str = "",
    commit_id: str | None = None,
    event_id: str | None = None,
) -> dict:
    """Write new papers into the immutable cache. Existing rows are kept exactly as first seen."""
    new_keys: list[str] = []
    seen_keys: list[str] = []
    for paper in literature.get("papers", []):
        key = paper.get("paper_key") or paper.get("id") or ""
        if not key:
            continue
        existing = db.scalar(
            select(ResearchPaper).where(
                ResearchPaper.research_project_id == rp.id, ResearchPaper.paper_key == key
            )
        )
        if existing is not None:
            seen_keys.append(key)
            continue
        db.add(
            ResearchPaper(
                research_project_id=rp.id,
                paper_key=key,
                doi=paper.get("doi"),
                title=paper.get("title", "")[:2000],
                year=paper.get("year"),
                venue=(paper.get("venue") or "")[:255],
                authors=paper.get("authors", []),
                abstract=(paper.get("abstract") or "")[:20000],
                url=paper.get("url"),
                source=paper.get("source", "openalex"),
                citation_count=int(paper.get("citation_count") or 0),
                relevance=float(paper.get("relevance") or 0.0),
                extracted_metrics=paper.get("extracted_metrics", []),
                query=query[:2000],
                citation=paper.get("citation", "")[:2000],
                commit_id=commit_id,
                event_id=event_id,
            )
        )
        new_keys.append(key)
    db.flush()
    return {"new": len(new_keys), "already_cached": len(seen_keys), "new_keys": new_keys}


def papers(db: Session, rp: ResearchProject, limit: int = 100) -> list[ResearchPaper]:
    return list(
        db.scalars(
            select(ResearchPaper)
            .where(ResearchPaper.research_project_id == rp.id)
            .order_by(ResearchPaper.relevance.desc(), ResearchPaper.citation_count.desc())
            .limit(limit)
        )
    )


def cached_corpus(db: Session, rp: ResearchProject, limit: int = 60) -> list[dict]:
    """The cache rendered as an offline corpus, so a degraded network still reuses prior research."""
    return [
        {
            "title": p.title,
            "abstract": p.abstract,
            "doi": p.doi,
            "year": p.year,
            "venue": p.venue,
            "authors": p.authors,  # without these a replayed record cites "Anon et al."
            "url": p.url,
            "citation_count": p.citation_count,
            "source": f"cache:{p.source}",
        }
        for p in papers(db, rp, limit)
    ]


# ---------------------------------------------------------------------- digest


def version_path(db: Session, rp: ResearchProject) -> list[ProteinCommit]:
    return list(
        db.scalars(
            select(ProteinCommit)
            .where(ProteinCommit.project_id == rp.project_id)
            .order_by(ProteinCommit.created_at.asc())
        )
    )


def labels_for(db: Session, commit_id: str) -> list[ResidueLabel]:
    return list(
        db.scalars(
            select(ResidueLabel)
            .where(ResidueLabel.commit_id == commit_id, ResidueLabel.superseded_by.is_(None))
            .order_by(ResidueLabel.created_at.asc())
        )
    )


def drift_models(db: Session, rp: ResearchProject) -> list[DriftModel]:
    return list(
        db.scalars(
            select(DriftModel)
            .where(DriftModel.research_project_id == rp.id)
            .order_by(DriftModel.metric.asc())
        )
    )


def results_for(db: Session, rp: ResearchProject, commit_id: str | None = None) -> list[WetlabResult]:
    stmt = select(WetlabResult).where(WetlabResult.research_project_id == rp.id)
    if commit_id:
        stmt = stmt.where(WetlabResult.commit_id == commit_id)
    return list(db.scalars(stmt.order_by(WetlabResult.created_at.asc())))


def plans_for(db: Session, rp: ResearchProject, commit_id: str | None = None) -> list[WetlabPlan]:
    stmt = select(WetlabPlan).where(WetlabPlan.research_project_id == rp.id)
    if commit_id:
        stmt = stmt.where(WetlabPlan.commit_id == commit_id)
    return list(db.scalars(stmt.order_by(WetlabPlan.created_at.desc())))


def hypotheses_for(db: Session, rp: ResearchProject) -> list[Hypothesis]:
    return list(
        db.scalars(
            select(Hypothesis)
            .where(Hypothesis.research_project_id == rp.id)
            .order_by(Hypothesis.created_at.asc())
        )
    )


def research_digest(db: Session, rp: ResearchProject, commit_id: str | None = None) -> dict:
    """Merged knowledge base: the whole version path plus every cached observation about it.

    This is deliberately the *merge* of (a) prior-version knowledge and (b) the paper/metric cache,
    which is what the daemon re-researches before proposing the next version.
    """
    path = version_path(db, rp)
    head = None
    if commit_id:
        head = next((c for c in path if c.id == commit_id), None)
    head = head or (path[-1] if path else None)
    all_results = results_for(db, rp)
    by_commit: dict[str, list[WetlabResult]] = {}
    for r in all_results:
        by_commit.setdefault(r.commit_id, []).append(r)

    def measured(commit: ProteinCommit) -> dict:
        out: dict[str, float | bool] = {}
        for res in by_commit.get(commit.id, []):
            for m in res.measurements:
                if m.get("value") is not None:
                    out[m["metric"]] = m["value"]
        return out

    versions = [
        {
            "commit_id": c.id,
            "short": c.id[:12],
            "label": c.label or c.id[:8],
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "mutations": [m.get("mutation") for m in (c.mutations or []) if m.get("mutation")],
            "scores": c.scores or {},
            "uncertainty": c.uncertainty or {},
            "filters": c.filters or {},
            "measured": measured(c),
            "labels": [
                {"kind": lb.kind, "name": lb.name, "residues": lb.residues, "note": lb.note}
                for lb in labels_for(db, c.id)
            ],
            "provider": c.provider,
        }
        for c in path
    ]
    cached = papers(db, rp, 40)
    return {
        "campaign": {
            "id": rp.id,
            "project_id": rp.project_id,
            "name": rp.name,
            "question": rp.question,
            "knowledge_version": rp.knowledge_version,
            "daemon_status": rp.daemon_status,
        },
        "head": versions[-1] if versions else None,
        "focus": next((v for v in versions if head and v["commit_id"] == head.id), None),
        "versions": versions,
        "version_count": len(versions),
        "papers": [
            {
                "title": p.title,
                "doi": p.doi,
                "year": p.year,
                "citation": p.citation,
                "metrics": p.extracted_metrics,
            }
            for p in cached[:12]
        ],
        "paper_count": len(cached),
        "hypotheses": [
            {
                "statement": h.statement,
                "metric": h.metric,
                "predicted": h.predicted_value,
                "sd": h.predicted_sd,
                "status": h.status,
            }
            for h in hypotheses_for(db, rp)
        ],
        "drift": [
            {
                "metric": d.metric,
                "n": d.n_observations,
                "bias": d.bias,
                "residual_sd": d.residual_sd,
                "rmse": d.rmse,
            }
            for d in drift_models(db, rp)
        ],
        "result_count": len(all_results),
        "events": [
            {
                "kind": e.kind,
                "summary": e.summary[:220],
                "at": e.created_at.isoformat() if e.created_at else None,
                "no": e.sequence_no,
            }
            for e in events(db, rp, 20)
        ],
    }


def digest_prompt(digest: dict) -> str:
    """Deterministic rendering of the merged knowledge base for agent prompts."""
    lines = [
        f"Campaign: {digest['campaign']['name']}",
        f"Research question: {digest['campaign']['question'] or 'not stated'}",
        f"Versions on the path: {digest['version_count']}; cached papers: {digest['paper_count']};"
        f" wet-lab result records: {digest['result_count']}",
        "",
        "Version path v1..vN (in-silico predictions and any measured values):",
    ]
    for idx, v in enumerate(digest["versions"], start=1):
        muts = "+".join(v["mutations"]) or "root"
        measured = ", ".join(f"{k}={val}" for k, val in v["measured"].items()) or "not measured yet"
        labels = ", ".join(f"{lb['kind']}:{lb['name']}" for lb in v["labels"]) or "no labels"
        lines.append(
            f"  v{idx} {v['label']} [{muts}] predicted={_score_line(v['scores'])} "
            f"measured=({measured}) labels=({labels})"
        )
    if digest["drift"]:
        lines.append("\nCalibration (predicted vs measured so far):")
        for d in digest["drift"]:
            lines.append(
                f"  - {d['metric']}: n={d['n']} bias={d['bias']:+.3f} residual_sd={d['residual_sd']:.3f}"
            )
    if digest["papers"]:
        lines.append("\nCached literature (never discarded, reused every pass):")
        for p in digest["papers"][:8]:
            nums = ", ".join(
                f"{m.get('name')}={m.get('value')}{m.get('unit', '')}"
                for m in (p["metrics"] or [])[:3]
            )
            lines.append(f"  - {p['citation'] or p['title']}{' | ' + nums if nums else ''}")
    if digest["hypotheses"]:
        lines.append("\nHypotheses:")
        for h in digest["hypotheses"][-8:]:
            lines.append(
                f"  - [{h['status']}] {h['statement']} ({h['metric']} ~ {h['predicted']})"
            )
    return "\n".join(lines)


def _score_line(scores: dict) -> str:
    keys = ("composite_score", "ddg_proxy", "predicted_tm", "solubility", "binding_score")
    parts = [f"{k}={scores[k]}" for k in keys if k in scores]
    return ", ".join(parts) or "no scores"


# ---------------------------------------------------------------- learned view


def learned_summary(db: Session, rp: ResearchProject) -> dict:
    """"What we learned v1 -> latest": deltas, resolved hypotheses and shrinking drift."""
    digest = research_digest(db, rp)
    versions = digest["versions"]
    lessons: list[str] = []
    if len(versions) >= 2:
        first, last = versions[0], versions[-1]
        for metric in ("composite_score", "predicted_tm", "solubility", "aggregation_propensity"):
            a, b = first["scores"].get(metric), last["scores"].get(metric)
            if isinstance(a, int | float) and isinstance(b, int | float):
                direction = "improved" if b > a else "regressed"
                lessons.append(
                    f"{metric} {direction} {a:.3f} -> {b:.3f} "
                    f"from {first['label']} to {last['label']}"
                )
        if not lessons:
            # An uploaded wild type carries no in-silico scores, so fall back to what the lab
            # actually measured on both ends of the path.
            for metric in sorted(set(first["measured"]) & set(last["measured"])):
                a, b = first["measured"][metric], last["measured"][metric]
                if isinstance(a, int | float) and isinstance(b, int | float) and not (
                    isinstance(a, bool) or isinstance(b, bool)
                ):
                    direction = "improved" if b > a else "regressed"
                    lessons.append(
                        f"measured {metric} {direction} {a:.3f} -> {b:.3f} "
                        f"from {first['label']} to {last['label']}"
                    )
    drift_trend: list[dict] = []
    for d in drift_models(db, rp):
        history = list(d.history or [])
        first_abs = abs(float(history[0].get("residual", 0.0))) if history else None
        last_abs = abs(float(history[-1].get("residual", 0.0))) if history else None
        drift_trend.append(
            {
                "metric": d.metric,
                "n": d.n_observations,
                "bias": round(d.bias, 4),
                "residual_sd": round(d.residual_sd, 4),
                "rmse": round(d.rmse, 4),
                "first_abs_residual": first_abs,
                "latest_abs_residual": last_abs,
                "shrinking": bool(
                    first_abs is not None and last_abs is not None and last_abs < first_abs
                ),
            }
        )
        if first_abs is not None and last_abs is not None:
            trend = "shrank" if last_abs < first_abs else "grew"
            lessons.append(
                f"{d.metric} prediction error {trend}: |residual| {first_abs:.2f} -> {last_abs:.2f}"
            )
    supported = [h for h in hypotheses_for(db, rp) if h.status == "supported"]
    refuted = [h for h in hypotheses_for(db, rp) if h.status == "refuted"]
    for h in refuted[-3:]:
        lessons.append(f"refuted: {h.statement}")
    for h in supported[-3:]:
        lessons.append(f"supported: {h.statement}")
    shrinking = [d for d in drift_trend if d["shrinking"]]
    headline = (
        f"{len(versions)} versions, {digest['paper_count']} cached papers, "
        f"{len(shrinking)}/{len(drift_trend)} metrics with shrinking in-silico->wet-lab drift"
        if versions
        else "no versions in this campaign yet"
    )
    return {
        "campaign_id": rp.id,
        "headline": headline,
        "versions": [
            {"label": v["label"], "commit_id": v["commit_id"], "mutations": v["mutations"],
             "scores": v["scores"], "measured": v["measured"]}
            for v in versions
        ],
        "lessons": lessons,
        "drift": drift_trend,
        "paper_count": digest["paper_count"],
        "hypotheses": {"supported": len(supported), "refuted": len(refuted),
                       "open": len(hypotheses_for(db, rp)) - len(supported) - len(refuted)},
        "generated_at": datetime.now(tz=UTC).isoformat(),
    }


# ------------------------------------------------------------- research passes


def literature_queries(
    project: Project, commit: ProteinCommit, labels: list[ResidueLabel], drift: list[DriftModel]
) -> list[str]:
    """Deterministic query set: goal + what actually changed + where drift is worst."""
    queries: list[str] = []
    goal = (project.goal or "protein stability").strip()
    queries.append(f"{goal} thermostability mutation melting temperature")
    muts = [m.get("mutation") for m in (commit.mutations or []) if m.get("mutation")]
    if muts:
        queries.append(
            f"protein engineering {' '.join(muts[:3])} surface charge mutation stability solubility"
        )
    for lb in labels[:2]:
        if lb.kind == "liability":
            queries.append("protein aggregation liability hydrophobic patch mitigation solubility")
        elif lb.kind == "active_site":
            queries.append("active site mutation activity stability trade-off protein engineering")
        elif lb.kind == "epitope":
            queries.append("epitope binding interface affinity maturation KD SPR")
        elif lb.kind == "mutation_intent":
            queries.append(f"{lb.name} mutation protein stability experimental Tm measurement")
        else:
            queries.append(f"{lb.name} protein engineering wet lab measurement")
    worst = sorted(drift, key=lambda d: -abs(d.bias or 0.0))[:1]
    for d in worst:
        queries.append(
            f"{d.metric.replace('_', ' ')} prediction accuracy experimental correlation protein"
        )
    if not muts:
        queries.append("recombinant protein expression E. coli soluble yield optimisation")
    # dedupe, keep order
    seen: set[str] = set()
    unique = []
    for q in queries:
        if q.lower() not in seen:
            seen.add(q.lower())
            unique.append(q)
    return unique[:4]


def run_literature_pass(
    db: Session,
    rp: ResearchProject,
    commit: ProteinCommit,
    queries: list[str],
    *,
    limit: int = 6,
    allow_network: bool = True,
) -> dict:
    """Search the open metadata APIs, always falling back to the cache — never to invention."""
    corpus = cached_corpus(db, rp) + seed_corpus.records()
    aggregate: dict = {"papers": [], "queries": [], "degraded": False, "degraded_reason": "",
                       "sources_used": [], "citations": []}
    for query in queries:
        payload = {
            "query": query,
            "limit": limit,
            "offline_corpus": corpus,
            "sources": ["openalex", "semanticscholar"] if allow_network else [],
        }
        try:
            out = skills.run("skill.literature", payload)
        except Exception as exc:  # a failing search must not stop the loop
            logger.warning("literature pass failed for %r: %s", query, exc)
            aggregate["degraded"] = True
            aggregate["degraded_reason"] = f"{type(exc).__name__}: {exc}"[:400]
            continue
        aggregate["queries"].append(query)
        aggregate["papers"].extend(out.get("papers", []))
        aggregate["citations"].extend(out.get("citations", []))
        for src in out.get("sources_used", []):
            if src not in aggregate["sources_used"]:
                aggregate["sources_used"].append(src)
        if out.get("degraded"):
            aggregate["degraded"] = True
            aggregate["degraded_reason"] = (out.get("degraded_reason") or "")[:400]
    # dedupe across queries
    by_key: dict[str, dict] = {}
    for paper in aggregate["papers"]:
        by_key.setdefault(paper.get("paper_key") or paper.get("title", ""), paper)
    aggregate["papers"] = list(by_key.values())
    aggregate["citations"] = sorted(set(aggregate["citations"]))[:40]
    return aggregate


def run_metrics_pass(db: Session, commit: ProteinCommit, structure_pdb: str | None = None) -> dict:
    """Recompute physics/chemistry/developability metrics through the skills, with provenance."""
    sequence = seqlib.clean_sequence(commit.sequence)
    physics = skills.run(
        "skill.physics", {"sequence": sequence, "structure_pdb": structure_pdb}
    )
    chemistry = skills.run("skill.chemistry", {"sequence": sequence})
    drug = skills.run(
        "skill.drug_discovery",
        {
            "candidates": [
                {
                    "label": commit.label or commit.id[:8],
                    "sequence": sequence,
                    "mutations": commit.mutations or [],
                    "affinity_score": float((commit.scores or {}).get("binding_score", 0.0) or 0.0),
                    "affinity_sd": float(
                        (commit.uncertainty or {}).get("binding_score", 0.0) or 0.0
                    ),
                }
            ]
        },
    )
    metrics = {**physics["metrics"], **chemistry["metrics"], **drug["metrics"]}
    skills.require_skill_metrics(metrics)
    return {
        "metrics": metrics,
        "physics": physics,
        "chemistry": chemistry,
        "drug_discovery": drug,
        "skills_used": ["skill.physics", "skill.chemistry", "skill.drug_discovery"],
        "citations": sorted(
            set(physics.get("citations", []) + chemistry.get("citations", []) + drug.get("citations", []))
        ),
    }


def record_hypothesis(
    db: Session,
    rp: ResearchProject,
    commit: ProteinCommit,
    *,
    statement: str,
    metric: str,
    predicted_value: float | None,
    predicted_sd: float | None,
    rationale: str = "",
    citations: list[str] | None = None,
    event_id: str | None = None,
) -> Hypothesis:
    hypothesis = Hypothesis(
        research_project_id=rp.id,
        commit_id=commit.id,
        statement=statement[:2000],
        rationale=rationale[:2000],
        metric=metric,
        predicted_value=predicted_value,
        predicted_sd=predicted_sd,
        citations=citations or [],
        event_id=event_id,
    )
    db.add(hypothesis)
    db.flush()
    return hypothesis


def resolve_hypotheses(db: Session, rp: ResearchProject, commit_id: str, measured: dict) -> list[dict]:
    """Close out hypotheses whose metric was just measured."""
    resolved: list[dict] = []
    for h in hypotheses_for(db, rp):
        if h.commit_id != commit_id or h.status != "open" or h.metric not in measured:
            continue
        value = measured[h.metric]
        if h.predicted_value is None or not isinstance(value, int | float):
            continue
        tolerance = max(float(h.predicted_sd or 0.0), wm.noise_sd(h.metric, value)) * 2.0
        supported = abs(float(value) - float(h.predicted_value)) <= max(tolerance, 1e-9)
        h.status = "supported" if supported else "refuted"
        h.evidence = {"measured": value, "predicted": h.predicted_value, "tolerance": tolerance}
        h.resolved_at = utcnow()
        resolved.append({"statement": h.statement, "status": h.status, "metric": h.metric})
    db.flush()
    return resolved
