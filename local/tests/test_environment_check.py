from __future__ import annotations

import json
import subprocess
import sys

from scripts.check_environment import build_environment_report


FORBIDDEN_SNIPPETS = (
    "服务器信息.txt",
    "SSH_KEY_PATH",
    "password",
    "token",
    "secret",
    "*.key",
)


def test_check_environment_function_returns_safe_status() -> None:
    payload = build_environment_report()

    assert payload["pyproject_ok"] is True
    assert payload["pytest_available"] is True
    assert payload["imports"]["data_quality_agent"] is True
    assert payload["imports"]["source_discovery_agent"] is True
    assert payload["source_discovery_agent_importable"] is True
    assert payload["package_config"]["missing_patterns"] == []
    rendered = json.dumps(payload, ensure_ascii=False)
    for snippet in FORBIDDEN_SNIPPETS:
        assert snippet.lower() not in rendered.lower()


def test_check_environment_script_outputs_safe_json_without_starting_services() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/check_environment.py"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode in {0, 1}
    payload = json.loads(completed.stdout)
    assert payload["pyproject_ok"] is True
    assert payload["source_discovery_agent_importable"] is True
    assert "imports" in payload
    rendered = completed.stdout + completed.stderr
    for snippet in FORBIDDEN_SNIPPETS:
        assert snippet.lower() not in rendered.lower()
