from __future__ import annotations

from source_discovery_agent import load_search_results_fixture, parse_search_results_fixture


FIXTURE = "tests/fixtures/source_discovery_norovirus_search_results.json"


def test_parse_search_results_fixture_skips_invalid_and_dedupes() -> None:
    results = load_search_results_fixture(FIXTURE)
    urls = [result.url for result in results]

    assert len(results) == 8
    assert "not-a-url" not in urls
    assert len(urls) == len(set(urls))
    assert "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html" in urls


def test_parse_search_results_fixture_accepts_list_and_missing_text() -> None:
    results = parse_search_results_fixture(
        [
            {"url": "https://example.invalid/a", "rank": "1"},
            {"url": "ftp://example.invalid/skip"},
        ]
    )

    assert len(results) == 1
    assert results[0].title is None
    assert results[0].snippet is None
    assert results[0].rank == 1
