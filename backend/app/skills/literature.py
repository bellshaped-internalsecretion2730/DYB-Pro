"""skill.literature — open bibliographic search with metric extraction.

Sources are open metadata APIs only: OpenAlex, Semantic Scholar Graph, and optionally NCBI
E-utilities. Paywalled PDFs are never fetched or scraped; the skill works from titles, abstracts
and open metadata, which is what the licences allow.

When the network is unavailable or an API rate-limits us the skill returns ``degraded=True`` with
the reason and whatever it did retrieve. It never invents a paper: a degraded literature pass is
recorded as degraded so the daemon can retry instead of pretending the evidence exists.
"""

from __future__ import annotations

import hashlib
import re

import httpx
from pydantic import BaseModel, Field

from app.skills.base import Metric, SkillSpec, collect, register

VERSION = "1.1.0"
OPENALEX = "https://api.openalex.org/works"
SEMANTIC_SCHOLAR = "https://api.semanticscholar.org/graph/v1/paper/search"
PUBMED_SEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_SUMMARY = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"

CITATIONS = [
    "Priem, Piwowar & Orr 2022, OpenAlex: a fully-open index of scholarly works (arXiv:2205.01833)",
    "Kinney et al. 2023, The Semantic Scholar Open Data Platform (arXiv:2301.10140)",
    "Sayers et al. 2022, Database resources of the NCBI (Nucleic Acids Res 50:D20)",
]

# Numeric claims worth extracting from an abstract. Each pattern captures the value.
METRIC_PATTERNS: list[tuple[str, str, str]] = [
    (
        "melting_temperature",
        r"\b(?:Tm|melting temperature)\D{0,20}?(\d{1,3}(?:\.\d+)?)\s*(?:°\s*C|degrees?\s*C|C\b)",
        "C",
    ),
    ("delta_tm", r"\b(?:ΔTm|delta[- ]?Tm)\D{0,12}?(-?\d{1,2}(?:\.\d+)?)\s*(?:°\s*C|K\b)", "C"),
    ("kd_nm", r"\bK[dD]\D{0,20}?(\d+(?:\.\d+)?)\s*nM", "nM"),
    ("kd_um", r"\bK[dD]\D{0,20}?(\d+(?:\.\d+)?)\s*(?:µ|u|μ)M", "uM"),
    ("kd_pm", r"\bK[dD]\D{0,20}?(\d+(?:\.\d+)?)\s*pM", "pM"),
    ("yield_mg_per_l", r"(\d+(?:\.\d+)?)\s*mg\s*(?:/|per\s*)\s*(?:L|liter|litre)", "mg/L"),
    ("ddg_kcal", r"(-?\d+(?:\.\d+)?)\s*kcal\s*(?:/|per\s*)\s*mol", "kcal/mol"),
    ("soluble_fraction_pct", r"(\d{1,3}(?:\.\d+)?)\s*%\s*(?:of\s*)?(?:soluble|solubility|expression)", "%"),
]


class LiteratureInput(BaseModel):
    query: str = Field(description="free-text search, e.g. 'GB1 thermostability core packing mutations'")
    limit: int = Field(default=8, ge=1, le=50)
    year_from: int | None = Field(default=None, description="oldest publication year to accept")
    sources: list[str] = Field(default_factory=lambda: ["openalex", "semanticscholar"])
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=120.0)
    offline_corpus: list[dict] = Field(
        default_factory=list,
        description="pre-fetched records; used by tests and by cache replays so the skill is offline-safe",
    )


class ExtractedMetric(BaseModel):
    name: str
    value: float
    unit: str
    context: str


class Paper(BaseModel):
    id: str
    title: str
    doi: str | None = None
    year: int | None = None
    venue: str = ""
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    url: str | None = None
    source: str = "offline"
    citation_count: int = 0
    relevance: float = 0.0
    extracted_metrics: list[ExtractedMetric] = Field(default_factory=list)
    citation: str = Field(default="", description="human-readable citation, filled in by the skill")

    @property
    def paper_key(self) -> str:
        return self.id

    def citation_text(self) -> str:
        first = self.authors[0].split()[-1] if self.authors else "Anon"
        year = self.year or "n.d."
        return f"{first} et al. {year}, {self.title[:110]}" + (f" (doi:{self.doi})" if self.doi else "")


class LiteratureOutput(BaseModel):
    query: str
    papers: list[Paper] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)
    degraded: bool = False
    degraded_reason: str = ""
    source_problems: list[str] = Field(default_factory=list)
    metrics: dict[str, dict] = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)


def _norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (title or "").lower()).strip()


def paper_id(doi: str | None, title: str) -> str:
    seed = (doi or "").lower().strip() or _norm_title(title)
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


def extract_metrics(text: str) -> list[ExtractedMetric]:
    out: list[ExtractedMetric] = []
    for name, pattern, unit in METRIC_PATTERNS:
        for match in re.finditer(pattern, text or "", flags=re.IGNORECASE):
            try:
                value = float(match.group(1))
            except (TypeError, ValueError):
                continue
            start = max(match.start() - 60, 0)
            out.append(
                ExtractedMetric(
                    name=name, value=value, unit=unit, context=text[start : match.end() + 40].strip()
                )
            )
            if len(out) >= 24:
                return out
    return out


def _relevance(query: str, paper: Paper) -> float:
    terms = {t for t in re.split(r"\W+", query.lower()) if len(t) > 3}
    if not terms:
        return 0.0
    haystack = f"{paper.title} {paper.abstract}".lower()
    hits = sum(1 for t in terms if t in haystack)
    boost = 0.15 if paper.extracted_metrics else 0.0
    return round(min(hits / len(terms) + boost, 1.0), 3)


def _from_openalex(item: dict) -> Paper:
    abstract = item.get("abstract") or _invert_abstract(item.get("abstract_inverted_index") or {})
    authors = [
        (a.get("author") or {}).get("display_name", "")
        for a in (item.get("authorships") or [])
        if (a.get("author") or {}).get("display_name")
    ]
    doi = (item.get("doi") or "").replace("https://doi.org/", "") or None
    title = item.get("display_name") or item.get("title") or "untitled"
    venue = ((item.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
    return Paper(
        id=paper_id(doi, title),
        title=title,
        doi=doi,
        year=item.get("publication_year"),
        venue=venue,
        authors=authors[:12],
        abstract=abstract or "",
        url=item.get("id"),
        source="openalex",
        citation_count=int(item.get("cited_by_count") or 0),
        extracted_metrics=extract_metrics(f"{title}. {abstract or ''}"),
    )


def _invert_abstract(index: dict) -> str:
    if not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, spots in index.items():
        positions.extend((int(p), word) for p in spots or [])
    return " ".join(word for _, word in sorted(positions))[:4000]


def _from_s2(item: dict) -> Paper:
    ids = item.get("externalIds") or {}
    title = item.get("title") or "untitled"
    doi = ids.get("DOI")
    abstract = item.get("abstract") or ""
    authors = [a.get("name", "") for a in (item.get("authors") or []) if a.get("name")]
    return Paper(
        id=paper_id(doi, title),
        title=title,
        doi=doi,
        year=item.get("year"),
        venue=item.get("venue") or "",
        authors=authors[:12],
        abstract=abstract,
        url=item.get("url"),
        source="semanticscholar",
        citation_count=int(item.get("citationCount") or 0),
        extracted_metrics=extract_metrics(f"{title}. {abstract}"),
    )


def _from_offline(item: dict) -> Paper:
    title = item.get("title") or "untitled"
    abstract = item.get("abstract") or ""
    return Paper(
        id=item.get("id") or paper_id(item.get("doi"), title),
        title=title,
        doi=item.get("doi"),
        year=item.get("year"),
        venue=item.get("venue") or "",
        authors=list(item.get("authors") or []),
        abstract=abstract,
        url=item.get("url"),
        source=item.get("source") or "offline-cache",
        citation_count=int(item.get("citation_count") or 0),
        extracted_metrics=extract_metrics(f"{title}. {abstract}"),
    )


def _search_openalex(client: httpx.Client, payload: LiteratureInput) -> list[Paper]:
    params: dict[str, str | int] = {"search": payload.query, "per-page": min(payload.limit, 25)}
    if payload.year_from:
        params["filter"] = f"from_publication_date:{payload.year_from}-01-01"
    response = client.get(OPENALEX, params=params)
    response.raise_for_status()
    return [_from_openalex(item) for item in (response.json().get("results") or [])]


def _search_s2(client: httpx.Client, payload: LiteratureInput) -> list[Paper]:
    params: dict[str, str | int] = {
        "query": payload.query,
        "limit": min(payload.limit, 25),
        "fields": "title,abstract,year,venue,authors,externalIds,citationCount,url",
    }
    if payload.year_from:
        params["year"] = f"{payload.year_from}-"
    response = client.get(SEMANTIC_SCHOLAR, params=params)
    response.raise_for_status()
    return [_from_s2(item) for item in (response.json().get("data") or [])]


def _search_pubmed(client: httpx.Client, payload: LiteratureInput) -> list[Paper]:
    ids = client.get(
        PUBMED_SEARCH,
        params={"db": "pubmed", "term": payload.query, "retmode": "json", "retmax": min(payload.limit, 20)},
    )
    ids.raise_for_status()
    pmids = ((ids.json().get("esearchresult") or {}).get("idlist")) or []
    if not pmids:
        return []
    summary = client.get(
        PUBMED_SUMMARY, params={"db": "pubmed", "id": ",".join(pmids), "retmode": "json"}
    )
    summary.raise_for_status()
    result = summary.json().get("result") or {}
    papers: list[Paper] = []
    for pmid in pmids:
        item = result.get(pmid) or {}
        title = item.get("title") or "untitled"
        doi = next(
            (i.get("value") for i in item.get("articleids") or [] if i.get("idtype") == "doi"), None
        )
        papers.append(
            Paper(
                id=paper_id(doi, title),
                title=title,
                doi=doi,
                year=int((item.get("pubdate") or "0")[:4] or 0) or None,
                venue=item.get("source") or "",
                authors=[a.get("name", "") for a in item.get("authors") or []][:12],
                abstract="",
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                source="pubmed",
                extracted_metrics=extract_metrics(title),
            )
        )
    return papers


SEARCHERS = {
    "openalex": _search_openalex,
    "semanticscholar": _search_s2,
    "pubmed": _search_pubmed,
}


def run(payload: LiteratureInput) -> LiteratureOutput:
    papers: list[Paper] = [_from_offline(item) for item in payload.offline_corpus]
    used = ["offline-cache"] if papers else []
    problems: list[str] = []

    live_sources = [s for s in payload.sources if s in SEARCHERS]
    if live_sources:
        headers = {"User-Agent": "Foldsmith/1.0 (research daemon; open metadata only)"}
        try:
            with httpx.Client(timeout=payload.timeout_seconds, headers=headers) as client:
                for source in live_sources:
                    try:
                        found = SEARCHERS[source](client, payload)
                    except (httpx.HTTPError, ValueError, KeyError) as exc:
                        problems.append(f"{source}: {type(exc).__name__}: {exc}"[:200])
                        continue
                    if found:
                        used.append(source)
                    papers.extend(found)
        except (httpx.HTTPError, OSError) as exc:  # no egress at all
            problems.append(f"network unavailable: {type(exc).__name__}: {exc}"[:200])

    # de-duplicate on DOI, else on normalised title; keep the richest record.
    merged: dict[str, Paper] = {}
    for paper in papers:
        key = (paper.doi or "").lower() or _norm_title(paper.title)
        current = merged.get(key)
        if current is None or len(paper.abstract) > len(current.abstract):
            merged[key] = paper
    for paper in merged.values():
        paper.relevance = _relevance(payload.query, paper)
        paper.citation = paper.citation_text()

    ordered = sorted(
        merged.values(), key=lambda p: (p.relevance, p.citation_count, p.year or 0), reverse=True
    )[: payload.limit]
    if payload.year_from:
        ordered = [p for p in ordered if (p.year or payload.year_from) >= payload.year_from]

    numeric = [m for p in ordered for m in p.extracted_metrics]
    metrics = collect(
        [
            Metric(
                name="papers_retrieved",
                value=float(len(ordered)),
                unit="count",
                method="OpenAlex/Semantic Scholar metadata search, DOI+title de-duplication",
                skill="skill.literature",
                citations=tuple(CITATIONS[:2]),
            ),
            Metric(
                name="numeric_claims_extracted",
                value=float(len(numeric)),
                unit="count",
                method="regex extraction of Tm/KD/yield/ddG/solubility values from titles and abstracts",
                skill="skill.literature",
            ),
        ]
    )
    return LiteratureOutput(
        query=payload.query,
        papers=ordered,
        sources_used=sorted(set(used)),
        degraded=bool(problems) and not ordered,
        degraded_reason="; ".join(problems)[:500],
        source_problems=problems,
        metrics=metrics,
        citations=[p.citation for p in ordered],
    )


SKILL = register(
    SkillSpec(
        name="skill.literature",
        version=VERSION,
        summary=(
            "Search OpenAlex / Semantic Scholar / PubMed for open metadata, de-duplicate, extract "
            "numeric assay claims (Tm, KD, yield, ddG, solubility) and return citable records."
        ),
        input_model=LiteratureInput,
        output_model=LiteratureOutput,
        runner=run,
        citations=CITATIONS,
    )
)
