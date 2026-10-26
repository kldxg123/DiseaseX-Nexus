from __future__ import annotations

import json

from scripts.deploy import DEPLOY_PLAN_STEPS, RSYNC_EXCLUDES, build_deploy_plan, deploy
from scripts.read_server_info import read_server_info


FAKE_VALUES = [
    "SERVER_HOST",
    "fake-deploy-user",
    "/tmp/fake_deploy_key",
    "/opt/fake-epidemic-agent",
    "example.invalid",
]


def test_deploy_defaults_to_dry_run_without_subprocess(monkeypatch) -> None:
    def fail_run(*args, **kwargs):
        raise AssertionError("dry-run must not call subprocess")

    monkeypatch.setattr("subprocess.run", fail_run)

    result = deploy("tests/fixtures/fake_server_info.txt")
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["status"] == "ok"
    assert result["dry_run"] is True
    assert result["command_results"] == []
    assert result["ssh_executed"] is False
    assert result["rsync_executed"] is False
    for value in FAKE_VALUES:
        assert value not in rendered


def test_build_deploy_plan_contains_expected_steps_and_safe_previews() -> None:
    server_info = read_server_info("tests/fixtures/fake_server_info.txt")

    plan = build_deploy_plan(server_info, project_root=__import__("pathlib").Path("."))

    assert [step["name"] for step in plan] == DEPLOY_PLAN_STEPS
    assert all(step["dry_run"] is True for step in plan)
    rendered = json.dumps(plan, ensure_ascii=False)
    for value in FAKE_VALUES:
        assert value not in rendered


def test_rsync_excludes_cover_sensitive_local_files() -> None:
    expected = {
        ".git",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".env",
        ".env.*",
        "服务器信息.txt",
        "*.pem",
        "*.key",
        "id_rsa",
        "id_ed25519",
        "mlruns",
        ".minio",
    }

    assert expected.issubset(set(RSYNC_EXCLUDES))
