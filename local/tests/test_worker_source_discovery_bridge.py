from __future__ import annotations

import json

from common.db import get_session, init_db
from common.models import SourceRegistry
from worker_agent.tasks import TaskRunner


def test_worker_source_discovery_bridge_fixture_task(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'worker-bridge.db'}"
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        result = TaskRunner(session).run_task(
            "source_discovery_bridge_fixture",
            country="United States",
            disease="influenza",
            parser_strategy="json_api",
            output_dir=str(tmp_path / "reports"),
        )
        sources = session.query(SourceRegistry).all()

    assert result["status"] == "success"
    summary = result["job"]["summary"]
    assert summary["parser_strategy"] == "json_api"
    assert summary["raw_documents_saved"] == 12
    assert summary["source_discovery_candidates_found"] == 8
    assert summary["network_accessed"] is False
    assert all(source.status == "pending_review" for source in sources)
    rendered = json.dumps(summary, ensure_ascii=False).lower()
    assert "metadata_json" not in rendered
    assert "raw_text" not in rendered
