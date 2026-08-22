# skill.literature (v1.1.0)

Search OpenAlex / Semantic Scholar / PubMed for open metadata, de-duplicate, extract numeric assay
claims (Tm, KD, yield, ddG, solubility) and return citable records.

Module: `backend/app/skills/literature.py` - Tests: `backend/tests/test_skills.py`

## Input

| Field | Type | Notes |
| --- | --- | --- |
| `query` | string (required) | free text, e.g. `GB1 thermostability core packing mutations` |
| `limit` | int | max records returned after de-duplication |
| `year_from` | int / null | oldest publication year to accept |
| `sources` | string[] | subset of `openalex`, `semantic_scholar`, `pubmed` |
| `timeout_seconds` | number | per-source HTTP budget |
| `offline_corpus` | object[] | pre-fetched records; used by cache replays and tests |

## Output

| Field | Notes |
| --- | --- |
| `papers` | de-duplicated records: title, DOI, year, venue, authors, url, citation string, citation count, relevance, `extracted_metrics` |
| `sources_used` | which sources actually answered (`offline-cache` when replaying) |
| `degraded`, `degraded_reason`, `source_problems` | set when a source is unreachable or rate-limited |
| `metrics` | numeric claims extracted from abstracts, each stamped with the source paper |
| `citations` | citation strings for the sources themselves |

## Behaviour

- Only open metadata and abstracts. **No paywalled PDF scraping.**
- De-duplication is by DOI, then by normalised title.
- Every record is written to the immutable research cache (`ResearchPaper`), so a later pass replays
  from cache instead of re-querying, and the campaign keeps working with no network.
- Rate limiting or an outage never fails a research pass: the skill returns `degraded=true` with the
  reason, the daemon records that on the event, and the UI shows it.
- `app/skills/seed_corpus.py` is a small bundled corpus of open, widely-cited methods papers (the
  ones the physics/chemistry heuristics are derived from). `run_literature_pass` always appends it to
  the offline corpus, so a first pass on a clean database with `DAEMON_LITERATURE_NETWORK=false` still
  has citable evidence. Those records are tagged `source="bundled-corpus"` and carry one-line
  summaries written for this repository — no abstract is copied — and a real OpenAlex or Semantic
  Scholar record for the same work supersedes them on de-duplication.

## Citations

- Priem, Piwowar & Orr 2022, *OpenAlex: a fully-open index of scholarly works* (arXiv:2205.01833)
- Kinney et al. 2023, *The Semantic Scholar Open Data Platform* (arXiv:2301.10140)
- Sayers et al. 2022, *Database resources of the NCBI* (Nucleic Acids Res 50:D20)
