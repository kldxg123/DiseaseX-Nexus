from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from common.db import get_session, init_db
from common.models import SourceRegistry
from crawler_agent.country_terms import normalize_country_code
from crawler_agent.disease_terms import expand_disease_aliases
from crawler_agent.gdelt import build_gdelt_query, build_gdelt_url, parse_gdelt_articles
from crawler_agent.gdelt_discovery import (
    GdeltDiscoveryService as LegacyGdeltDiscoveryService,
)
from crawler_agent.source_discovery import GdeltDiscoveryService
from scripts import discover_sources


FIXTURE_PATH = Path("tests/fixtures/gdelt_norovirus.json")


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'discover-sources.db'}"


def test_norovirus_aliases_are_complete_enough_for_query_terms() -> None:
    aliases = {alias.lower() for alias in expand_disease_aliases("norovirus")}

    assert {
        "norovirus",
        "norwalk virus",
        "norwalk-like virus",
        "acute gastroenteritis",
        "viral gastroenteritis",
        "stomach flu",
        "winter vomiting disease",
        "noro virus",
        "norovirus outbreak",
    }.issubset(aliases)


def test_country_terms_and_gdelt_url_are_stable() -> None:
    assert normalize_country_code("U.S.") == "US"
    query = build_gdelt_query(country="U.S.", disease="norovirus")
    url = build_gdelt_url(country="U.S.", disease="norovirus", lookback_days=14)
    params = parse_qs(urlsplit(url).query)

    assert "sourcecountry:US" in query
    assert '"acute gastroenteritis"' in query
    assert params["timespan"] == ["14d"]
    assert params["format"] == ["json"]


def test_fixture_parses_without_network() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    articles = parse_gdelt_articles(payload)

    assert len(articles) == 3
    assert [article.domain for article in articles] == [
        "local-news.invalid",
        "local-news.invalid",
        "public-radio.invalid",
    ]


def test_new_and_legacy_service_interfaces_match() -> None:
    assert LegacyGdeltDiscoveryService is GdeltDiscoveryService


def test_discover_sources_cli_dry_run_with_fixture_does_not_write_database(tmp_path, capsys) -> None:
    database_path = tmp_path / "dry-run.db"
    result = discover_sources.main(
        [
            "--database-url",
            f"sqlite:///{database_path}",
            "--country",
            "U.S.",
            "--disease",
            "norovirus",
            "--lookback-days",
            "14",
            "--fixture-json",
            str(FIXTURE_PATH),
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert not database_path.exists()
    assert "local-news.invalid" in captured.out


def test_discover_sources_cli_fixture_writes_pending_review_candidates(tmp_path) -> None:
    database_url = _database_url(tmp_path)

    result = discover_sources.main(
        [
            "--database-url",
            database_url,
            "--country",
            "U.S.",
            "--disease",
            "norovirus",
            "--lookback-days",
            "14",
            "--fixture-json",
            str(FIXTURE_PATH),
        ]
    )

    assert result == 0
    with get_session(database_url) as session:
        records = session.query(SourceRegistry).order_by(SourceRegistry.base_url).all()

    assert [record.base_url for record in records] == [
        "https://local-news.invalid/",
        "https://public-radio.invalid/",
    ]
    assert {record.status for record in records} == {"pending_review"}


def test_live_cli_without_fixture_is_refused() -> None:
    try:
        discover_sources.main(
            [
                "--country",
                "U.S.",
                "--disease",
                "norovirus",
            ]
        )
    except SystemExit as exc:
        assert str(exc) == "Refusing live GDELT discovery without a local fixture."
    else:
        raise AssertionError("Expected SystemExit")


def test_service_can_group_fixture_articles_without_network(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    articles = parse_gdelt_articles(FIXTURE_PATH.read_text(encoding="utf-8"))

    with get_session(database_url) as session:
        service = GdeltDiscoveryService(session)
        candidates = service.discover_candidates_from_articles(
            articles=articles,
            country="U.S.",
            disease="norovirus",
            query_url="fixture://gdelt_norovirus",
        )

    assert len(candidates) == 2
    assert candidates[0].article_count == 2
    assert candidates[0].source_payload["metadata_json"]["query_url"] == "fixture://gdelt_norovirus"
