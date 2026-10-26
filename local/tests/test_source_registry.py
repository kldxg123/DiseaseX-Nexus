from __future__ import annotations

import pytest

from common.db import get_session, init_db
from common.models import SourceRegistry
from crawler_agent.source_registry import SourceRegistryService


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'source-registry.db'}"


def _source_payload(**overrides):
    payload = {
        "name": "Fixture Source",
        "base_url": "https://example.invalid/source",
        "country_scope": ["global"],
        "disease_scope": ["all"],
        "source_type": "official_page",
        "authority_score": 0.9,
        "relevance_score": 0.8,
        "freshness_score": 0.7,
        "reliability_score": 0.9,
        "structured_data_score": 0.6,
        "historical_success_score": 0.5,
        "language_country_match": 0.9,
        "legal_or_robots_penalty": 0.0,
        "robots_allowed": True,
        "parser_type": "fixture",
        "metadata_json": {"connector": "fixture"},
        "status": "active",
    }
    payload.update(overrides)
    return payload


def test_add_source_inserts_registry_record(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = SourceRegistryService(session)
        source = service.add_source(**_source_payload())

        assert source.id is not None
        assert source.status == "active"
        assert 0.0 <= source.metadata_json["source_score"] <= 1.0


def test_upsert_source_does_not_duplicate_base_url(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = SourceRegistryService(session)
        first = service.upsert_source(**_source_payload(name="First"))
        second = service.upsert_source(**_source_payload(name="Second"))

        assert first.id == second.id
        assert second.name == "Second"
        assert len(session.query(SourceRegistry).all()) == 1


def test_active_and_candidate_listing_filters_status(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = SourceRegistryService(session)
        service.add_source(**_source_payload(name="Active", base_url="https://active.invalid"))
        service.add_source(
            **_source_payload(
                name="Candidate",
                base_url="https://candidate.invalid",
                status="pending_review",
            )
        )

        assert [source.name for source in service.list_active_sources()] == ["Active"]
        assert [source.name for source in service.list_candidate_sources()] == ["Candidate"]


def test_mark_failure_transitions_to_degraded_and_disabled(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = SourceRegistryService(session)
        source = service.add_source(**_source_payload())
        for _ in range(5):
            service.mark_failure(source.id, reason="temporary fixture failure")
        assert source.status == "degraded"
        assert source.consecutive_failures == 5

        for _ in range(10):
            service.mark_failure(source.id)
        assert source.status == "disabled"
        assert source.consecutive_failures == 15


def test_mark_success_resets_failures_and_recovers_non_disabled(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = SourceRegistryService(session)
        source = service.add_source(**_source_payload(status="pending_review"))
        service.mark_failure(source.id)
        service.mark_success(source.id)

        assert source.status == "active"
        assert source.consecutive_failures == 0
        assert source.last_success_at is not None


def test_disabled_source_is_not_restored_by_single_success(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = SourceRegistryService(session)
        source = service.add_source(**_source_payload(status="disabled"))
        service.mark_success(source.id)

        assert source.status == "disabled"
        assert source.consecutive_failures == 0


def test_unsafe_robots_state_prevents_active_status(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = SourceRegistryService(session)
        source = service.add_source(
            **_source_payload(
                base_url="https://blocked.invalid",
                robots_allowed=False,
                status="active",
            )
        )

        assert source.status == "disabled"


def test_missing_source_id_raises_value_error(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        service = SourceRegistryService(session)
        with pytest.raises(ValueError):
            service.mark_failure("999")
