from __future__ import annotations

import json
from pathlib import Path

from common.db import get_session, init_db
from scripts.run_who_ebola_data_acquisition_test import main as who_cli_main
from worker_agent.tasks import TaskRunner, list_task_names


def test_who_data_acquisition_api_dry_run_and_blocked_live(api_client, tmp_path) -> None:
    payload = {
        "disease": "Ebola",
        "country_or_region": "Democratic Republic of the Congo",
        "date_start": "2025-01-01",
        "date_end": "2025-12-31",
        "output_dir": str(tmp_path / "api-who"),
    }
    dry = api_client.post("/who-data-acquisition/dry-run", json=payload)
    blocked = api_client.post("/who-data-acquisition/run", json=payload)

    assert dry.status_code == 200
    summary = dry.json()["summary"]
    assert summary["network_accessed"] is False
    assert summary["pending_review_rows"] >= 1
    assert Path(summary["review_json"]).exists()
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "blocked"
    rendered = json.dumps(dry.json(), ensure_ascii=False).lower()
    assert "raw_text" not in rendered
    assert "response_body" not in rendered


def test_worker_who_data_acquisition_task_runs_dry_run(tmp_path) -> None:
    assert "who_data_acquisition_dry_run" in list_task_names()
    database_url = f"sqlite:///{(tmp_path / 'worker-who.sqlite').as_posix()}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        result = TaskRunner(session).run_task(
            "who_data_acquisition_dry_run",
            output_dir=str(tmp_path / "worker-who"),
        )

    assert result["status"] == "success"
    assert result["job"]["summary"]["pending_review_rows"] >= 1
    assert result["job"]["summary"]["network_accessed"] is False


def test_who_cli_dry_run_outputs_safe_summary(tmp_path, capsys) -> None:
    status = who_cli_main(["--dry-run", "--output-dir", str(tmp_path / "cli-who")])
    captured = capsys.readouterr().out
    payload = json.loads(captured)

    assert status == 0
    assert payload["network_accessed"] is False
    assert payload["pending_review_rows"] >= 1
    assert "raw_text" not in captured.lower()
    assert "response_body" not in captured.lower()


def test_dashboard_who_page_imports() -> None:
    from apps.dashboard.pages import render_who_data_acquisition_page

    assert callable(render_who_data_acquisition_page)
