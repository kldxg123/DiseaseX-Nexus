from __future__ import annotations

from datetime import UTC, datetime

from crawler_optimizer_agent.parser_diagnostics import (
    detect_html_structure_issue,
    infer_parser_strategy_from_document,
    recommend_parser_switch,
)
from crawler_optimizer_agent.types import CrawlerFailureCase
from tests.crawler_optimizer_test_utils import add_document, add_source, init_test_db
from common.db import get_session


def test_infer_parser_strategy_from_content_type_and_metadata(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        source = add_source(session)
        csv_doc = add_document(session, source, content_type="text/csv", url_suffix="data.csv")
        json_doc = add_document(session, source, content_type="application/json", body='{"items": []}', url_suffix="data")
        table_doc = add_document(session, source, body="table row column cases", metadata={"table": True}, url_suffix="table")
        article_doc = add_document(session, source, title="Article", body="United States norovirus " * 20, url_suffix="article")

    assert infer_parser_strategy_from_document(csv_doc) == "csv_dataset"
    assert infer_parser_strategy_from_document(json_doc) == "json_api"
    assert infer_parser_strategy_from_document(table_doc) == "html_table"
    assert infer_parser_strategy_from_document(article_doc) == "article_page"


def test_parser_failed_recommends_parser_switch(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        source = add_source(session)
        document = add_document(session, source, body="United States norovirus " * 20)
        case = CrawlerFailureCase(
            source_id=str(source.id),
            raw_document_id=str(document.id),
            source_name=source.name,
            url=document.normalized_url,
            failure_type="parser_failed",
            severity="high",
            confidence=0.8,
            evidence={},
            created_at=datetime.now(UTC),
        )
        action = recommend_parser_switch(case, raw_document=document, source=source)

    assert action is not None
    assert action.action_type == "switch_parser"
    assert action.metadata["recommended_parser"] == "article_page"


def test_detect_html_structure_issue_from_metadata(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        source = add_source(session)
        document = add_document(session, source, body="short", metadata={"many_links": True})
        case = detect_html_structure_issue(document)

    assert case is not None
    assert case.failure_type == "html_structure_changed"
