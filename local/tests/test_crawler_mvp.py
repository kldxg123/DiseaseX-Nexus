from __future__ import annotations

import httpx
import pytest

from common.db import get_session, init_db
from common.models import RawDocument, SourceRegistry
from crawler_agent.content_extraction import extract_content
from crawler_agent.crawler import CrawlerService
from crawler_agent.http_fetcher import HttpFetcher
from crawler_agent.robots import StaticRobotsPolicy
from crawler_agent.source_registry import SourceRegistryService


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'crawler-mvp.db'}"


def _source_payload(**overrides):
    payload = {
        "name": "Mock Source",
        "base_url": "https://example.invalid",
        "country_scope": ["global"],
        "disease_scope": ["all"],
        "source_type": "official_page",
        "authority_score": 0.8,
        "relevance_score": 0.8,
        "freshness_score": 0.8,
        "reliability_score": 0.8,
        "structured_data_score": 0.8,
        "historical_success_score": 0.8,
        "language_country_match": 0.8,
        "legal_or_robots_penalty": 0.0,
        "robots_allowed": True,
        "parser_type": "mock",
        "metadata_json": {"connector": "mock"},
        "status": "active",
    }
    payload.update(overrides)
    return payload


def _mock_fetcher(html: str) -> HttpFetcher:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text=html,
            request=request,
        )

    return HttpFetcher(transport=httpx.MockTransport(handler))


def test_extract_content_gets_title_and_body() -> None:
    extracted = extract_content(
        """
        <html>
          <head><title>Outbreak Update</title><script>ignore()</script></head>
          <body><h1>Headline</h1><p>Body text for analysts.</p></body>
        </html>
        """
    )

    assert extracted.title == "Outbreak Update"
    assert "Headline" in extracted.body_text
    assert "Body text for analysts." in extracted.body_text
    assert "ignore" not in extracted.body_text


def test_crawler_fetches_extracts_hashes_and_stores_raw_document(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    html = "<html><head><title>Mock Title</title></head><body><p>Mock body.</p></body></html>"

    with get_session(database_url) as session:
        source = SourceRegistryService(session).add_source(**_source_payload())
        service = CrawlerService(
            session,
            fetcher=_mock_fetcher(html),
            robots_policy=StaticRobotsPolicy(is_allowed=True),
        )

        result = service.crawl_url(source_id=source.id, url="/article?b=2&a=1")

        assert result.created is True
        assert result.content_hash
        assert result.title == "Mock Title"
        document = session.get(RawDocument, result.raw_document_id)
        assert document is not None
        assert document.source_registry_id == source.id
        assert document.normalized_url == "https://example.invalid/article?a=1&b=2"
        assert document.body_text == "Mock body."


def test_crawler_deduplicates_by_content_hash(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    html = "<html><head><title>Same</title></head><body><p>Same body.</p></body></html>"

    with get_session(database_url) as session:
        source = SourceRegistryService(session).add_source(**_source_payload())
        service = CrawlerService(
            session,
            fetcher=_mock_fetcher(html),
            robots_policy=StaticRobotsPolicy(is_allowed=True),
        )

        first = service.crawl_url(source_id=source.id, url="/first")
        second = service.crawl_url(source_id=source.id, url="/second")

        assert first.raw_document_id == second.raw_document_id
        assert second.created is False
        assert len(session.query(RawDocument).all()) == 1


def test_crawler_blocks_when_robots_policy_denies(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        source = SourceRegistryService(session).add_source(**_source_payload())
        service = CrawlerService(
            session,
            fetcher=_mock_fetcher("<html><body>blocked</body></html>"),
            robots_policy=StaticRobotsPolicy(is_allowed=False),
        )

        with pytest.raises(ValueError):
            service.crawl_url(source_id=source.id, url="/blocked")


def test_crawler_blocks_disabled_source(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        source = SourceRegistryService(session).add_source(**_source_payload(status="disabled"))
        service = CrawlerService(
            session,
            fetcher=_mock_fetcher("<html><body>disabled</body></html>"),
            robots_policy=StaticRobotsPolicy(is_allowed=True),
        )

        with pytest.raises(ValueError):
            service.crawl_url(source_id=source.id, url="/disabled")
