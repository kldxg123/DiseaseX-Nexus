from __future__ import annotations

import json
import socket

from common.db import get_session, init_db
from source_discovery_agent import SourceDiscoveryAgent
from source_discovery_agent.report import discovery_report_to_dict, render_discovery_report_markdown


FIXTURE = "tests/fixtures/source_discovery_norovirus_search_results.json"


def test_source_discovery_does_not_use_network(monkeypatch, tmp_path) -> None:
    def fail_network(*_args, **_kwargs):
        raise AssertionError("network access is forbidden in source discovery tests")

    monkeypatch.setattr(socket, "create_connection", fail_network)
    database_url = f"sqlite:///{tmp_path / 'source-discovery-safety.db'}"
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        report = SourceDiscoveryAgent(session).discover_from_fixture(
            country="United States",
            disease="norovirus",
            fixture_path=FIXTURE,
        )

    assert report.status == "ok"


def test_source_discovery_outputs_do_not_expose_sensitive_or_long_payloads(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'source-discovery-safety-output.db'}"
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        report = SourceDiscoveryAgent(session).discover_from_fixture(
            country="United States",
            disease="norovirus",
            fixture_path=FIXTURE,
        )

    output = json.dumps(discovery_report_to_dict(report), ensure_ascii=False).lower()
    output += render_discovery_report_markdown(report).lower()

    forbidden = [
        "服务器信息.txt",
        "ssh_key_path",
        "password",
        "token",
        "secret",
        "private key",
        "raw_text",
        "metadata_json",
        "provenance_json",
        "feature_json",
        "lesson_json",
        "<html",
    ]
    for term in forbidden:
        assert term.lower() not in output
