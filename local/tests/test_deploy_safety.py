from __future__ import annotations

import json

from scripts.deploy import (
    mask_deploy_value,
    run_command,
    sanitize_command_preview,
    validate_required_project_files,
)


def test_mask_deploy_value_redacts_present_values() -> None:
    assert mask_deploy_value("anything") == "***"
    assert mask_deploy_value(None) is None


def test_sanitize_command_preview_masks_host_user_and_paths() -> None:
    preview = sanitize_command_preview(
        [
            "ssh",
            "-i",
            "/tmp/fake_deploy_key",
            "fake-deploy-user@SERVER_HOST",
            "cd /opt/fake-epidemic-agent",
        ]
    )

    assert "SERVER_HOST" not in preview
    assert "fake-deploy-user" not in preview
    assert "/tmp/fake_deploy_key" not in preview
    assert "/opt/fake-epidemic-agent" not in preview
    assert "***@***" in preview


def test_run_command_dry_run_does_not_execute(monkeypatch) -> None:
    def fail_run(*args, **kwargs):
        raise AssertionError("dry-run must not execute subprocess")

    monkeypatch.setattr("subprocess.run", fail_run)

    result = run_command(["ssh", "fake-deploy-user@SERVER_HOST"], dry_run=True)

    assert result["status"] == "skipped"
    assert result["dry_run"] is True
    assert result["returncode"] is None


def test_missing_fields_output_only_names(tmp_path) -> None:
    from scripts.deploy import deploy

    path = tmp_path / "fake_server_info.txt"
    path.write_text(
        "\n".join(
            [
                "HOST=SERVER_HOST",
                "USER=fake-deploy-user",
            ]
        ),
        encoding="utf-8",
    )

    result = deploy(path)
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["status"] == "failed"
    assert "ssh_key_path" in result["missing_fields"]
    assert "deploy_path" in result["missing_fields"]
    assert "SERVER_HOST" not in rendered
    assert "fake-deploy-user" not in rendered


def test_validate_required_project_files_for_current_project() -> None:
    missing = validate_required_project_files(__import__("pathlib").Path("."))

    assert missing == []
