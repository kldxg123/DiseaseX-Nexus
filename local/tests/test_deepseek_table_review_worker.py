from __future__ import annotations

import json
from pathlib import Path

from common.db import get_session, init_db
from worker_agent import TaskRunner


def test_worker_runs_table_review_mock_validate_and_ingest(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'worker.sqlite').as_posix()}"
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        runner = TaskRunner(session)
        source_pack = runner.run_task(
            "deepseek_table_review_build_source_pack",
            disease="Ebola",
            country_or_region="African Region",
            date_start="2026-05-01",
            date_end="2026-07-01",
            output_dir=str(tmp_path / "worker-review"),
        )
        mock = runner.run_task(
            "deepseek_table_review_mock_extract",
            disease="Ebola",
            country_or_region="African Region",
            date_start="2026-05-01",
            date_end="2026-07-01",
            output_dir=str(tmp_path / "worker-review"),
        )
        review_path = Path(mock["job"]["summary"]["paths"]["review_json"])
        validate = runner.run_task("deepseek_table_review_clean_validate", review_table=str(review_path))
        blocked_ingest = runner.run_task(
            "deepseek_table_review_ingest_approved",
            review_table=str(review_path),
            database_url=database_url,
            allow_persistence=False,
        )

    assert source_pack["status"] == "success"
    assert source_pack["job"]["summary"]["network_accessed"] is False
    assert mock["status"] == "success"
    assert mock["job"]["summary"]["deepseek_called"] is False
    assert validate["status"] == "success"
    assert "visualization" in validate["job"]["summary"]
    assert blocked_ingest["status"] == "success"
    assert blocked_ingest["job"]["summary"]["status"] == "blocked"
    rendered = json.dumps(mock, ensure_ascii=False).lower()
    assert "prompt" not in rendered
    assert "completion" not in rendered
