from __future__ import annotations

from source_discovery_agent import build_discovery_queries


def test_build_discovery_queries_for_us_norovirus() -> None:
    queries = build_discovery_queries("United States", "norovirus")
    query_texts = [query.query_text for query in queries]

    assert "United States norovirus outbreak data" in query_texts
    assert "United States norovirus surveillance data" in query_texts
    assert "CDC norovirus outbreak data" in query_texts
    assert "norovirus NoroSTAT data" in query_texts
    assert "norovirus NORS data" in query_texts
    assert all(query.metadata["network_executed"] is False for query in queries)


def test_build_discovery_queries_unknown_terms_fallback() -> None:
    queries = build_discovery_queries("Atlantis", "fixturepox", max_queries=3)

    assert len(queries) == 3
    assert queries[0].country == "Atlantis"
    assert "fixturepox" in queries[0].query_text
