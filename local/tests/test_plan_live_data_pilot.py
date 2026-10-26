from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.plan_live_data_pilot import build_live_pilot_plan, write_live_pilot_plan


def test_plan_live_data_pilot_builds_non_executing_plan() -> None:
    plan = build_live_pilot_plan(
        source_key="cdc_norostat",
        country="United States",
        disease="norovirus",
        policy_path="configs/source_access_policy.example.json",
    )

    assert plan["source_key"] == "cdc_norostat"
    assert plan["current_step_executes_live_fetch"] is False
    assert plan["network_accessed"] is False
    assert "--allow-network" in plan["future_live_command_template"]
    assert "<CDC_NOROSTAT_URL_REVIEWED_BY_HUMAN>" in plan["future_live_command_template"]
    assert any("does not execute live fetch" in warning for warning in plan["template_warnings"])


def test_plan_live_data_pilot_writes_json_and_markdown(tmp_path) -> None:
    result = write_live_pilot_plan(
        source_key="cdc_norostat",
        country="United States",
        disease="norovirus",
        policy_path="configs/source_access_policy.example.json",
        output_dir=str(tmp_path),
    )

    assert result["status"] == "ok"
    json_path = Path(result["plan_json"])
    md_path = Path(result["plan_markdown"])
    assert json_path.exists()
    assert md_path.exists()
    assert "not executed" in md_path.read_text(encoding="utf-8").lower()
    rendered = json.dumps(json.loads(json_path.read_text(encoding="utf-8"))).lower()
    assert "password" not in rendered
    assert "token" not in rendered
    assert "secret" not in rendered


def test_plan_live_data_pilot_cli_creates_plan(tmp_path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/plan_live_data_pilot.py",
            "--source-key",
            "cdc_norostat",
            "--country",
            "United States",
            "--disease",
            "norovirus",
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
    assert payload["live_execution_enabled"] is False
