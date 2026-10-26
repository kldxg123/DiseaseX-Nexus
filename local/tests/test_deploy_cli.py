from __future__ import annotations

import json

from scripts import deploy as deploy_module


FAKE_VALUES = [
    "SERVER_HOST",
    "fake-deploy-user",
    "/tmp/fake_deploy_key",
    "/opt/fake-epidemic-agent",
    "example.invalid",
]


def test_deploy_cli_dry_run_outputs_safe_json(capsys, monkeypatch) -> None:
    def fail_run(*args, **kwargs):
        raise AssertionError("dry-run must not call subprocess")

    monkeypatch.setattr("subprocess.run", fail_run)

    result = deploy_module.main(["--server-info", "tests/fixtures/fake_server_info.txt"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["status"] == "ok"
    assert payload["dry_run"] is True
    for value in FAKE_VALUES:
        assert value not in captured.out


def test_deploy_cli_execute_without_confirm_is_refused(capsys) -> None:
    result = deploy_module.main(["--server-info", "tests/fixtures/fake_server_info.txt", "--execute"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result != 0
    assert payload["status"] == "refused"
    assert "confirm" in payload["error"]


def test_deploy_execute_branch_uses_mocked_run_command(monkeypatch) -> None:
    calls = []

    def fake_run_command(command, *, dry_run=True, timeout_seconds=60):
        calls.append((command, dry_run, timeout_seconds))
        return {
            "status": "success",
            "dry_run": dry_run,
            "would_execute": True,
            "command_preview": "mocked",
            "returncode": 0,
        }

    monkeypatch.setattr(deploy_module, "run_command", fake_run_command)

    result = deploy_module.deploy("tests/fixtures/fake_server_info.txt", execute=True)

    assert result["status"] == "ok"
    assert result["dry_run"] is False
    assert calls
    assert all(call[1] is False for call in calls)
