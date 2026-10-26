from __future__ import annotations

import json

from common.db import get_session
from crawler_optimizer_agent import CrawlerOptimizerAgent
from tests.crawler_optimizer_test_utils import add_document, add_source, init_test_db


FORBIDDEN = [
    "SSH_KEY_PATH",
    "password",
    "token",
    "secret",
    "private key",
    "<html>",
    "full raw body",
]


def test_optimizer_report_omits_sensitive_and_full_content(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        source = add_source(
            session,
            name="Safety Source",
            metadata={"token": "hidden", "last_failure_reason": "timeout"},
            failures=6,
        )
        add_document(
            session,
            source,
            title=None,
            body="<html>full raw body token hidden secret password</html>",
            extraction_status="failed",
            metadata={"html": "<html>full raw body</html>", "token": "hidden"},
        )
        payload = CrawlerOptimizerAgent(session).analyze(disease="norovirus").to_dict()

    dumped = json.dumps(payload, ensure_ascii=False)
    for marker in FORBIDDEN:
        assert marker.lower() not in dumped.lower()


def test_high_risk_recommendations_require_human_review(tmp_path) -> None:
    database_url = init_test_db(tmp_path)
    with get_session(database_url) as session:
        add_source(session, name="Denied", robots_allowed=False)
        payload = CrawlerOptimizerAgent(session).analyze().to_dict()

    high_risk = [
        action
        for action in payload["recommended_actions"]
        if action.get("risk_level") == "high"
    ]
    assert high_risk
    assert all(action["requires_human_review"] is True for action in high_risk)
