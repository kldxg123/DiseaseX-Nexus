from __future__ import annotations

import json
import subprocess
import sys

from scripts.run_controlled_live_source_discovery import run_controlled_live_source_discovery


def test_run_controlled_live_source_discovery_dry_run_success(tmp_path) -> None:
    result = run_controlled_live_source_discovery(
        provider="gdelt",
        country="United States",
        disease="influenza",
        dry_run=True,
        policy_path="configs/live_source_discovery_policy.example.json",
        output_dir=str(tmp_path / "readiness"),
    )

    assert result["status"] == "ok"
    assert result["dry_run"] is True
    assert result["live_execution_enabled"] is False
    assert result["network_accessed"] is False
    assert result["provider_dry_run"]["network_accessed"] is False
    assert result["next_step_required"] == "human approval before live discovery"


def test_run_controlled_live_source_discovery_requires_dry_run() -> None:
    result = run_controlled_live_source_discovery(
        provider="gdelt",
        country="United States",
        disease="influenza",
        dry_run=False,
    )

    assert result["status"] == "failed"
    assert result["live_execution_enabled"] is False
    assert "disabled" in result["error"]


def test_run_controlled_live_source_discovery_cli_rejects_without_dry_run() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_controlled_live_source_discovery.py",
            "--provider",
            "gdelt",
            "--country",
            "United States",
            "--disease",
            "influenza",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode != 0
    payload = json.loads(completed.stdout)
    assert payload["status"] == "failed"
    assert payload["network_accessed"] is False
