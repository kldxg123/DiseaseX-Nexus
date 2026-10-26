from __future__ import annotations

import json

from common.db import get_session, init_db
from common.models import SourceRegistry
from worker_agent.tasks import TaskRunner


FIXTURE = "tests/fixtures/source_discovery_norovirus_search_results.json"


def test_worker_discover_sources_fixture_task(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'worker-source-discovery.db'}"
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        result = TaskRunner(session).run_task(
            "discover_sources_fixture",
            country="United States",
            disease="norovirus",
            fixture=FIXTURE,
            output_dir=str(tmp_path / "reports"),
        )
        rows = session.query(SourceRegistry).all()

        assert result["status"] == "success"
        summary = result["job"]["summary"]
        assert summary["candidates_found"] == 8
        assert summary["candidates_inserted_or_updated"] == len(rows)
        assert summary["network_accessed"] is False
        assert all(row.status == "pending_review" for row in rows)
        rendered = json.dumps(summary, ensure_ascii=False).lower()
        assert "snippet" not in rendered
        assert "metadata_json" not in rendered
