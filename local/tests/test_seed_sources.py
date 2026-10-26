from __future__ import annotations

from common.db import get_session, init_db
from common.models import SourceRegistry
from crawler_agent.default_sources import get_default_seed_sources
from scripts import seed_sources


SENSITIVE_TERMS = ("token", "password", "secret", "api_key", "cookie")


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'seed-sources.db'}"


def test_default_seed_sources_have_required_metadata_without_sensitive_fields() -> None:
    seeds = get_default_seed_sources()

    assert len(seeds) >= 6
    names = {seed["name"] for seed in seeds}
    assert {
        "WHO Disease Outbreak News",
        "ECDC Surveillance Atlas",
        "ECDC ERVISS",
        "CDC NoroSTAT",
        "CDC NORS",
        "GDELT",
    }.issubset(names)
    for seed in seeds:
        assert {
            "name",
            "base_url",
            "country_scope",
            "disease_scope",
            "source_type",
            "authority_score",
            "relevance_score",
            "freshness_score",
            "reliability_score",
            "structured_data_score",
            "historical_success_score",
            "language_country_match",
            "legal_or_robots_penalty",
            "robots_allowed",
            "parser_type",
            "metadata_json",
            "status",
        }.issubset(seed)
        rendered = repr(seed).lower()
        assert not any(term in rendered for term in SENSITIVE_TERMS)


def test_dry_run_does_not_write_database(tmp_path, capsys) -> None:
    database_path = tmp_path / "dry-run.db"
    exit_code = seed_sources.main(
        ["--database-url", f"sqlite:///{database_path}", "--dry-run"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert not database_path.exists()
    assert "seed_source_count" in captured.out
    assert "WHO Disease Outbreak News" in captured.out


def test_seed_sources_writes_to_sqlite_temp_database(tmp_path) -> None:
    database_url = _database_url(tmp_path)

    summary = seed_sources.seed_sources(database_url)

    assert summary["seed_source_count"] >= 6
    assert summary["created"] >= 6
    with get_session(database_url) as session:
        records = session.query(SourceRegistry).all()

    assert len(records) == summary["seed_source_count"]


def test_seed_sources_is_idempotent(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    first = seed_sources.seed_sources(database_url)
    second = seed_sources.seed_sources(database_url)

    assert first["created"] >= 6
    assert second["created"] == 0
    assert second["updated"] == first["seed_source_count"]
