from __future__ import annotations

import json
import socket

from scripts.live_source_discovery_preflight import run_preflight
from scripts.run_controlled_live_source_discovery import run_controlled_live_source_discovery


def test_live_source_discovery_readiness_outputs_are_safe_and_local(tmp_path, monkeypatch) -> None:
    def fail_network(*args, **kwargs):
        raise AssertionError("network must not be used")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    preflight = run_preflight(
        provider="gdelt",
        country="United States",
        disease="influenza",
        policy_path="configs/live_source_discovery_policy.example.json",
    )
    dry_run = run_controlled_live_source_discovery(
        provider="gdelt",
        country="United States",
        disease="influenza",
        dry_run=True,
        policy_path="configs/live_source_discovery_policy.example.json",
        output_dir=str(tmp_path / "readiness"),
    )
    rendered = json.dumps({"preflight": preflight, "dry_run": dry_run}, ensure_ascii=False).lower()

    assert preflight["network_accessed"] is False
    assert dry_run["network_accessed"] is False
    for forbidden in (
        "服务器信息.txt",
        "password",
        "token",
        "secret",
        "ssh",
        "raw_text",
        "metadata_json",
        "provenance_json",
        "feature_json",
        "lesson_json",
        "<html",
    ):
        assert forbidden not in rendered
