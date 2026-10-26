from __future__ import annotations

import json

from scripts.smoke_test import main


SENSITIVE_MARKERS = [
    "服务器信息.txt",
    "secret",
    "token",
    "password",
    "private key",
]


def test_smoke_test_cli_outputs_safe_json(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{(tmp_path / 'smoke-cli.db').as_posix()}"

    result = main(["--database-url", database_url])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["status"] == "ok"
    assert payload["database"] == "sqlite"
    assert payload["database_scope"] == "custom"
    assert payload["api_smoke"] == {}
    assert payload["network_accessed"] is False
    assert payload["services_started"] is False
    assert payload["access_policy"]["guardrail_status"] == "ok"

    rendered = captured.out.lower()
    for marker in SENSITIVE_MARKERS:
        assert marker.lower() not in rendered


def test_smoke_test_cli_temp_db_include_api_outputs_safe_json(capsys) -> None:
    result = main(["--temp-db", "--include-api"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["status"] == "ok"
    assert payload["database"] == "sqlite"
    assert payload["database_scope"] == "temp"
    assert payload["api_smoke"]["checked_endpoints"] >= 11

    rendered = captured.out.lower()
    for marker in SENSITIVE_MARKERS:
        assert marker.lower() not in rendered


def test_smoke_test_cli_temp_db_include_worker_outputs_safe_json(capsys) -> None:
    result = main(["--temp-db", "--include-worker"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["status"] == "ok"
    assert payload["worker_smoke"]["ok"] is True
    assert payload["worker_daemon_started"] is False

    rendered = captured.out.lower()
    for marker in SENSITIVE_MARKERS:
        assert marker.lower() not in rendered
