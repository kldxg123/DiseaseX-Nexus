from __future__ import annotations

from common.db import get_session
from crawler_optimizer_agent.source_recommender import (
    rank_candidate_sources,
    recommend_better_sources,
    recommend_source_status_actions,
)
from tests.crawler_optimizer_test_utils import add_source, init_test_db


def test_rank_candidate_sources_filters_low_scores(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        low = add_source(session, name="Low", status="pending_review", score=0.2)
        high = add_source(session, name="High", status="pending_review", score=0.8)
        active = add_source(session, name="Active", status="active", score=0.9)
        ranked = rank_candidate_sources([low, high, active], min_score=0.35)

    assert [source.name for source in ranked] == ["High"]


def test_source_status_actions_are_safe_and_reviewed(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        denied = add_source(session, name="Denied", robots_allowed=False)
        degraded = add_source(session, name="Degraded", failures=5)
        actions = recommend_source_status_actions([denied, degraded])

    by_type = {action.action_type: action for action in actions}
    assert by_type["disable_source"].requires_human_review is True
    assert by_type["mark_degraded"].risk_level == "medium"


def test_recommend_better_sources_keeps_candidate_pending(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        degraded = add_source(session, name="Degraded", status="degraded", failures=8, score=0.3)
        candidate = add_source(session, name="Candidate", status="pending_review", score=0.85)
        actions = recommend_better_sources(
            existing_sources=[degraded],
            candidate_sources=[candidate],
            country="United States",
            disease="norovirus",
        )

    assert any(action.action_type == "add_candidate_source" for action in actions)
    assert all(action.requires_human_review for action in actions)
    assert candidate.status == "pending_review"
