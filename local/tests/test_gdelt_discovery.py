from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from common.db import get_session, init_db
from common.models import SourceRegistry
from crawler_agent.gdelt_discovery import (
    GdeltDiscoveryService,
    build_gdelt_query,
    build_gdelt_url,
    expand_disease_aliases,
    normalize_country_code,
    parse_gdelt_articles,
)


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'gdelt-discovery.db'}"


def _mock_payload() -> dict:
    return {
        "articles": [
            {
                "url": "https://news-a.invalid/report?id=2",
                "title": "Ebola update one",
                "sourceCommonName": "News A",
                "language": "English",
                "sourceCountry": "US",
            },
            {
                "url": "https://www.news-a.invalid/second",
                "title": "Ebola update two",
                "sourceCommonName": "News A",
                "language": "English",
                "sourceCountry": "US",
            },
            {
                "url": "https://news-b.invalid/story",
                "title": "Regional alert",
                "sourceCommonName": "News B",
                "language": "French",
                "sourceCountry": "FR",
            },
            {
                "url": "file:///tmp/not-allowed",
                "title": "Invalid URL skipped",
            },
        ]
    }


def _mock_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.gdeltproject.org"
        return httpx.Response(
            200,
            json=_mock_payload(),
            request=request,
        )

    return httpx.MockTransport(handler)


def test_expand_disease_aliases_includes_known_aliases() -> None:
    aliases = expand_disease_aliases("ebola")

    assert aliases == ["ebola", "ebola virus disease", "EVD"]


def test_country_normalization_and_query_construction() -> None:
    assert normalize_country_code("United States") == "US"
    query = build_gdelt_query(country="United States", disease="ebola")

    assert '"ebola"' in query
    assert '"ebola virus disease"' in query
    assert "sourcecountry:US" in query


def test_build_gdelt_url_clamps_and_encodes_parameters() -> None:
    url = build_gdelt_url(country="US", disease="ebola", lookback_days=500, max_records=999)
    parsed = urlsplit(url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "api.gdeltproject.org"
    assert params["mode"] == ["ArtList"]
    assert params["format"] == ["json"]
    assert params["timespan"] == ["365d"]
    assert params["maxrecords"] == ["250"]
    assert "sourcecountry:US" in params["query"][0]


def test_parse_gdelt_articles_extracts_domains_and_skips_invalid_urls() -> None:
    articles = parse_gdelt_articles(_mock_payload())

    assert len(articles) == 3
    assert [article.domain for article in articles] == [
        "news-a.invalid",
        "news-a.invalid",
        "news-b.invalid",
    ]
    assert articles[0].url == "https://news-a.invalid/report?id=2"


def test_fetch_requires_mock_transport_or_explicit_network(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = GdeltDiscoveryService(session)
        with pytest.raises(RuntimeError):
            service.fetch_mockable_articles(
                country="US",
                disease="ebola",
                lookback_days=7,
            )


def test_discover_candidates_from_mock_transport_groups_and_scores(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = GdeltDiscoveryService(session, transport=_mock_transport())
        candidates = service.discover_candidates(
            country="United States",
            disease="ebola",
            lookback_days=7,
        )

    assert [candidate.base_url for candidate in candidates] == [
        "https://news-a.invalid/",
        "https://news-b.invalid/",
    ]
    assert candidates[0].article_count == 2
    assert candidates[0].source_score > candidates[1].source_score
    assert candidates[0].source_payload["status"] == "pending_review"


def test_discover_and_upsert_candidates_writes_pending_review_sources(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = GdeltDiscoveryService(session, transport=_mock_transport())
        candidates = service.discover_and_upsert_candidates(
            country="United States",
            disease="ebola",
            lookback_days=7,
        )
        records = session.query(SourceRegistry).order_by(SourceRegistry.base_url).all()

    assert len(candidates) == 2
    assert [record.base_url for record in records] == [
        "https://news-a.invalid/",
        "https://news-b.invalid/",
    ]
    assert {record.status for record in records} == {"pending_review"}
    assert all(record.metadata_json["connector"] == "gdelt_candidate_discovery" for record in records)
    assert all(record.metadata_json["source_score"] > 0 for record in records)


def test_mock_payload_does_not_contain_sensitive_fields() -> None:
    rendered = json.dumps(_mock_payload()).lower()

    assert "token" not in rendered
    assert "password" not in rendered
    assert "secret" not in rendered
