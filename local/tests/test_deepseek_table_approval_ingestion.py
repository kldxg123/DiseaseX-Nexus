from __future__ import annotations

from sqlalchemy import func, select

from common.db import get_session, init_db
from common.models import EpidemicEvent, RawDocument, SourceRegistry
from deepseek_web_search_agent.approval_ingestion import ingest_approved_table_rows
from tests.deepseek_table_review_fixture import approve_review_rows, create_mock_table_review


def test_approved_table_rows_require_explicit_persistence(tmp_path) -> None:
    _summary, review_path = create_mock_table_review(tmp_path)
    approve_review_rows(review_path)

    result = ingest_approved_table_rows(review_table_path=str(review_path), allow_persistence=False)

    assert result["status"] == "blocked"
    assert result["epidemic_events_created"] == 0


def test_approved_table_rows_create_raw_documents_and_events_idempotently(tmp_path) -> None:
    _summary, review_path = create_mock_table_review(tmp_path)
    approve_review_rows(review_path)
    database_url = f"sqlite:///{(tmp_path / 'approved.sqlite').as_posix()}"
    init_db(database_url).dispose()

    first = ingest_approved_table_rows(
        review_table_path=str(review_path),
        database_url=database_url,
        allow_persistence=True,
        output_dir=str(tmp_path / "ingestion"),
    )
    second = ingest_approved_table_rows(
        review_table_path=str(review_path),
        database_url=database_url,
        allow_persistence=True,
        output_dir=str(tmp_path / "ingestion"),
    )

    assert first["epidemic_events_created"] == 2
    assert second["epidemic_events_created"] == 0
    assert first["feature_generation_allowed"] is False
    with get_session(database_url) as session:
        assert session.scalar(select(func.count()).select_from(SourceRegistry)) == 2
        assert session.scalar(select(func.count()).select_from(RawDocument)) == 2
        assert session.scalar(select(func.count()).select_from(EpidemicEvent)) == 2
