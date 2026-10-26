from __future__ import annotations

import json
import socket
import subprocess
import sys

from scripts.live_data_preflight import run_preflight


def test_live_data_preflight_cdc_norostat_ok_and_guardrail_decisions(monkeypatch) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network must not be used")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    payload = run_preflight(source_key="cdc_norostat", policy_path="configs/source_access_policy.example.json")

    assert payload["status"] == "ok"
    assert payload["checks"]["policy_exists"] is True
    assert payload["checks"]["default_live_disabled"] is True
    assert payload["checks"]["max_requests_lte_3"] is True
    assert payload["checks"]["cdc_domain_allowed"] is True
    assert payload["guardrail_denies_without_allow_network"] is True
    assert payload["guardrail_allows_when_all_conditions_met"] is True
    assert payload["network_accessed"] is False


def test_live_data_preflight_cli_outputs_safe_json() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/live_data_preflight.py",
            "--source-key",
            "cdc_norostat",
            "--policy",
            "configs/source_access_policy.example.json",
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
    for snippet in ("服务器信息.txt", "ssh_key_path", "password", "token", "secret", "private key"):
        assert snippet not in rendered
