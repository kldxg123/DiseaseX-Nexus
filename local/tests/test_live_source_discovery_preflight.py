from __future__ import annotations

import json
import socket
import subprocess
import sys

from scripts.live_source_discovery_preflight import run_preflight


def test_live_source_discovery_preflight_is_local_and_safe(monkeypatch) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network must not be used")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    payload = run_preflight(
        provider="gdelt",
        country="United States",
        disease="influenza",
        policy_path="configs/live_source_discovery_policy.example.json",
    )

    assert payload["status"] == "ok"
    assert payload["default_live_disabled"] is True
    assert payload["decision_without_allow_network"] is False
    assert payload["decision_with_all_conditions"] is True
    assert payload["network_accessed"] is False


def test_live_source_discovery_preflight_cli_outputs_safe_json() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/live_source_discovery_preflight.py",
            "--provider",
            "gdelt",
            "--country",
            "United States",
            "--disease",
            "influenza",
            "--policy",
            "configs/live_source_discovery_policy.example.json",
            "--json",
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
    for forbidden in ("服务器信息.txt", "password", "token", "secret", "ssh"):
        assert forbidden not in rendered
