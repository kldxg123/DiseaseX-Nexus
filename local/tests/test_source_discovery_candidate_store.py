from __future__ import annotations

from common.db import get_session, init_db
from common.models import SourceRegistry
from source_discovery_agent import load_search_results_fixture
from source_discovery_agent.candidate_store import SourceCandidateStore
from source_discovery_agent.source_evaluator import evaluate_search_result, rank_source_candidates


FIXTURE = "tests/fixtures/source_discovery_norovirus_search_results.json"


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'source-discovery-store.db'}"


def _candidates():
    candidates = [
        evaluate_search_result(result, country="United States", disease="norovirus")
        for result in load_search_results_fixture(FIXTURE)
    ]
    return rank_source_candidates([candidate for candidate in candidates if candidate is not None])


def test_candidate_store_writes_pending_review_and_skips_low_score(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        summary = SourceCandidateStore(session).save_candidates(_candidates(), min_source_score=0.35)
        rows = session.query(SourceRegistry).all()

        assert summary["seen"] == 8
        assert summary["skipped_low_score"] >= 1
        assert summary["inserted_or_updated"] == len(rows)
        assert all(row.status == "pending_review" for row in rows)
        assert all(row.metadata_json["discovered_by"] == "source_discovery_agent" for row in rows)
        assert all(row.metadata_json["requires_human_review"] is True for row in rows)


def test_candidate_store_upsert_does_not_duplicate_base_url(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        store = SourceCandidateStore(session)
        first = store.save_candidates(_candidates(), min_source_score=0.35)
        second = store.save_candidates(_candidates(), min_source_score=0.35)

        assert second["inserted_or_updated"] == first["inserted_or_updated"]
        assert len(session.query(SourceRegistry).all()) == first["inserted_or_updated"]
