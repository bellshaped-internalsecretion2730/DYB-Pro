"""The bundled corpus keeps an offline literature pass evidence-backed and honestly labelled."""

from __future__ import annotations

from app import skills
from app.skills import seed_corpus


def test_records_are_tagged_as_bundled_and_complete():
    records = seed_corpus.records()
    assert len(records) >= 8
    for record in records:
        assert record["source"] == seed_corpus.SOURCE
        assert record["title"] and record["abstract"] and record["year"]
        assert record["authors"]


def test_offline_literature_pass_returns_cited_papers_without_network():
    out = skills.run(
        "skill.literature",
        {
            "query": "protein thermal stability melting temperature core packing mutations",
            "limit": 6,
            "sources": [],  # no egress at all
            "offline_corpus": seed_corpus.records(),
        },
    )
    assert out["papers"], out
    assert out["degraded"] is False
    assert out["sources_used"] == ["offline-cache"]
    assert all(p["citation"] for p in out["papers"])
    # the summaries carry the numbers the physics/chemistry skills cite, so extraction is real
    assert any(p["extracted_metrics"] for p in out["papers"])
