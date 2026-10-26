from __future__ import annotations

import json
import subprocess
import sys

from scripts.run_controlled_live_pilot import run_controlled_live_pilot_live

LIVE_URL = "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html"


def test_controlled_live_pilot_rejects_when_not_execute_live() -> None:
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
    assert payload["network_accessed"] is False


def test_controlled_live_pilot_rejects_missing_allow_network() -> None:
    summary = run_controlled_live_pilot_live(
        source_key="cdc_norostat",
        live_url=LIVE_URL,
        allow_network=False,
        terms_reviewed=True,
        manual_reviewed=True,
        robots_allowed=True,
        max_requests=1,
    )

    assert summary["status"] == "failed"
    assert "--allow-network" in summary["warnings"][0]
    assert summary["network_accessed"] is False


def test_controlled_live_pilot_rejects_unapproved_url() -> None:
    summary = run_controlled_live_pilot_live(
        source_key="cdc_norostat",
        live_url="https://www.cdc.gov/other",
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        robots_allowed=True,
        max_requests=1,
    )

    assert summary["status"] == "failed"
    assert "single allowed" in summary["warnings"][0]
    assert summary["network_accessed"] is False


def test_controlled_live_pilot_rejects_more_than_one_request() -> None:
    summary = run_controlled_live_pilot_live(
        source_key="cdc_norostat",
        live_url=LIVE_URL,
        allow_network=True,
        terms_reviewed=True,
        manual_reviewed=True,
        robots_allowed=True,
        max_requests=2,
    )

    assert summary["status"] == "failed"
    assert "exactly 1" in summary["warnings"][0]
    assert summary["network_accessed"] is False


def test_controlled_live_pilot_rejects_missing_review_flags() -> None:
    for kwargs in (
        {"terms_reviewed": False, "manual_reviewed": True, "robots_allowed": True},
        {"terms_reviewed": True, "manual_reviewed": False, "robots_allowed": True},
        {"terms_reviewed": True, "manual_reviewed": True, "robots_allowed": False},
    ):
        summary = run_controlled_live_pilot_live(
            source_key="cdc_norostat",
            live_url=LIVE_URL,
            allow_network=True,
            max_requests=1,
            **kwargs,
        )
        assert summary["status"] == "failed"
        assert summary["network_accessed"] is False


def test_controlled_live_pilot_execute_cli_rejects_without_allow_network() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_controlled_live_pilot.py",
            "--source-key",
            "cdc_norostat",
            "--execute-live",
            "--live-url",
            LIVE_URL,
            "--terms-reviewed",
            "--manual-reviewed",
            "--robots-allowed",
            "--max-requests",
            "1",
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
    rendered = completed.stdout.lower()
    for snippet in ("服务器信息.txt", "ssh_key_path", "password", "token", "secret", "<html"):
        assert snippet not in rendered
