from __future__ import annotations

import json
import subprocess
import sys

from common.db import get_session, init_db
from scripts.run_controlled_live_pilot import run_controlled_live_pilot_dry_run
from worker_agent.tasks import TaskRunner, list_task_names


def test_run_controlled_live_pilot_dry_run_succeeds(tmp_path) -> None:
    payload = run_controlled_live_pilot_dry_run(
        source_key="cdc_norostat",
        policy_path="configs/source_access_policy.example.json",
        output_dir=str(tmp_path),
    )

    assert payload["status"] == "ok"
    assert payload["dry_run"] is True
    assert payload["preflight_ok"] is True
    assert payload["plan_created"] is True
    assert payload["live_execution_enabled"] is False
    assert payload["network_accessed"] is False


def test_run_controlled_live_pilot_cli_requires_dry_run() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/run_controlled_live_pilot.py", "--source-key", "cdc_norostat"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode != 0
    payload = json.loads(completed.stdout)
    assert payload["live_execution_enabled"] is False
    assert "intentionally disabled" in payload["error"]


def test_run_controlled_live_pilot_cli_dry_run_outputs_safe_json(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_controlled_live_pilot.py",
            "--source-key",
            "cdc_norostat",
            "--dry-run",
            "--database-url",
            f"sqlite:///{tmp_path / 'pilot.db'}",
            "--output-dir",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    rendered = completed.stdout.lower()
    for snippet in ("服务器信息.txt", "ssh_key_path", "password", "token", "secret", "private key"):
        assert snippet not in rendered


def test_worker_prepare_live_data_pilot_task_is_manual_and_safe(tmp_path) -> None:
    assert "prepare_live_data_pilot" in list_task_names()
    database_url = f"sqlite:///{tmp_path / 'worker-pilot.db'}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        result = TaskRunner(session).run_task("prepare_live_data_pilot", output_dir=str(tmp_path))

    assert result["status"] == "success"
    summary = result["job"]["summary"]
    assert summary["preflight_ok"] is True
    assert summary["plan_created"] is True
    assert summary["live_execution_enabled"] is False
    assert summary["network_accessed"] is False
