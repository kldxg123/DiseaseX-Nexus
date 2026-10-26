from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from scripts.docker_local_check import main, run_docker_local_check


@dataclass
class FakeCompletedProcess:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


class FakeRunner:
    def __init__(self, *, docker_available: bool = True, noisy_output: bool = False) -> None:
        self.docker_available = docker_available
        self.noisy_output = noisy_output
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> FakeCompletedProcess:
        self.calls.append((command, kwargs))
        if command in (["docker", "--version"], ["docker", "compose", "version"]):
            return FakeCompletedProcess(returncode=0 if self.docker_available else 1)
        stdout = "ok"
        stderr = ""
        if self.noisy_output:
            stdout = "normal line\nTOKEN=hidden-value"
            stderr = "PRIVATE KEY should not be shown"
        return FakeCompletedProcess(returncode=0, stdout=stdout, stderr=stderr)


def test_static_only_does_not_probe_or_start_docker() -> None:
    runner = FakeRunner()

    result = run_docker_local_check(static_only=True, runner=runner)

    assert result["status"] == "ok"
    assert result["docker_available"] is False
    assert result["operations"] == []
    assert runner.calls == []


def test_docker_commands_are_mockable_and_use_shell_false() -> None:
    runner = FakeRunner(noisy_output=True)

    result = run_docker_local_check(build=True, up=True, smoke=True, down=True, runner=runner)

    assert result["status"] == "ok"
    commands = [call[0] for call in runner.calls]
    assert commands == [
        ["docker", "--version"],
        ["docker", "compose", "version"],
        ["docker", "compose", "build"],
        ["docker", "compose", "up", "-d"],
        ["docker", "compose", "run", "--rm", "api", "python", "scripts/smoke_test.py", "--temp-db"],
        ["docker", "compose", "down"],
    ]
    assert all(call[1]["shell"] is False for call in runner.calls)
    dumped = json.dumps(result).lower()
    assert "hidden-value" not in dumped
    assert "should not be shown" not in dumped


def test_docker_unavailable_skips_container_operations_safely() -> None:
    runner = FakeRunner(docker_available=False)

    result = run_docker_local_check(build=True, up=True, smoke=True, down=True, runner=runner)

    assert result["status"] == "ok"
    assert result["docker_available"] is False
    assert len(result["operations"]) == 1
    assert result["operations"][0]["operation"] == "docker_available"


def test_cli_static_only_outputs_safe_json(capsys) -> None:
    exit_code = main(["--static-only"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["static"]["status"] == "ok"
