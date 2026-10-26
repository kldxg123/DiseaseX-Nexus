from __future__ import annotations

import json

from common.db import get_session
from crawler_optimizer_agent.connector_spec import (
    connector_spec_to_markdown,
    infer_connector_spec_from_source,
    slugify_connector_name,
)
from tests.crawler_optimizer_test_utils import add_source, init_test_db


def test_slugify_connector_name_is_snake_case() -> None:
    assert slugify_connector_name("CDC NoroSTAT Feed!") == "cdc_norostat_feed"


def test_connector_spec_from_source_is_review_required(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        source = add_source(session, name="Candidate Feed", status="pending_review", metadata={"table": True})
        spec = infer_connector_spec_from_source(source, sample_urls=["https://fixture.invalid/a?token=hidden"])

    summary = spec.to_dict()
    assert summary["connector_name"] == "candidate_feed"
    assert summary["requires_human_review"] is True
    assert summary["test_fixture_required"] is True
    assert "title" in summary["expected_fields"]
    assert summary["sample_urls"] == ["https://fixture.invalid/a"]


def test_connector_spec_markdown_is_safe(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        source = add_source(session, name="Secret Token Source", status="pending_review")
        spec = infer_connector_spec_from_source(source)
        markdown = connector_spec_to_markdown(spec)

    dumped = json.dumps({"markdown": markdown}).lower()
    assert "password" not in dumped
    assert "hidden" not in dumped
