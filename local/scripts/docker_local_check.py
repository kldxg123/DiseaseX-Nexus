from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_compose import check_compose, sanitize_payload

SENSITIVE_MARKERS = (
    "secret",
    "token",
    "password",
    "private key",
    "ssh_key_path",
    "服务器信息.txt",
)

REQUIRED_DOCKERIGNORE_PATTERNS = (
    "服务器信息.txt",
    ".env",
    ".env.*",
    "!.env.example",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "*.token",
    "*.secret",
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "mlruns",
    ".minio",
)


def run_docker_local_check(
    *,
    static_only: bool = False,
    build: bool = False,
    up: bool = False,
    smoke: bool = False,
    down: bool = False,
    runner: Callable[..., Any] = subprocess.run,
) -> dict:
    result: dict[str, Any] = {
        "status": "ok",
        "static": _static_checks(),
        "docker_available": False,
        "operations": [],
        "warnings": [],
    }
    if result["static"]["status"] != "ok":
        result["status"] = "failed"
    requested_docker = build or up or smoke or down
    if static_only or not requested_docker:
        return _safe_result(result)

    availability = _docker_available(runner)
    result["docker_available"] = availability["available"]
    if not availability["available"]:
        result["warnings"].append("Docker is not available; skipped container operations.")
        result["operations"].append(availability)
        return _safe_result(result)

    if build:
        result["operations"].append(_run_docker_command(["docker", "compose", "build"], runner))
    if up:
        result["operations"].append(_run_docker_command(["docker", "compose", "up", "-d"], runner))
    if smoke:
        result["operations"].append(
            _run_docker_command(
                ["docker", "compose", "run", "--rm", "api", "python", "scripts/smoke_test.py", "--temp-db"],
                runner,
            )
        )
    if down:
        result["operations"].append(_run_docker_command(["docker", "compose", "down"], runner))

    if any(operation.get("status") == "failed" for operation in result["operations"]):
        result["status"] = "failed"
    return _safe_result(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run safe local Docker Compose checks.")
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--up", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--down", action="store_true")
    args = parser.parse_args(argv)
    result = run_docker_local_check(
        static_only=args.static_only,
        build=args.build,
        up=args.up,
        smoke=args.smoke,
        down=args.down,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


def _static_checks() -> dict:
    dockerfile = _check_dockerfile(PROJECT_ROOT / "Dockerfile")
    dockerignore = _check_dockerignore(PROJECT_ROOT / ".dockerignore")
    compose = check_compose(PROJECT_ROOT / "docker-compose.yml")
    status = "ok" if dockerfile["ok"] and dockerignore["ok"] and compose.get("status") == "ok" else "failed"
    return {
        "status": status,
        "dockerfile": dockerfile,
        "dockerignore": dockerignore,
        "compose": compose,
    }


def _check_dockerfile(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"ok": False, "warnings": ["Dockerfile missing"]}
    checks = {
        "uses_python_slim": "FROM python:3.11-slim" in text or "FROM python:3.12-slim" in text,
        "bytecode_disabled": "PYTHONDONTWRITEBYTECODE=1" in text,
        "unbuffered": "PYTHONUNBUFFERED=1" in text,
        "workdir_set": "WORKDIR" in text,
        "installs_project": "pip install --no-cache-dir ." in text,
        "default_api_command": "uvicorn" in text and "apps.api.main:app" in text,
    }
    return {"ok": all(checks.values()), "checks": checks}


def _check_dockerignore(path: Path) -> dict:
    try:
        lines = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    except FileNotFoundError:
        return {"ok": False, "missing_patterns": list(REQUIRED_DOCKERIGNORE_PATTERNS)}
    missing = [pattern for pattern in REQUIRED_DOCKERIGNORE_PATTERNS if pattern not in lines]
    return {"ok": not missing, "missing_patterns": missing}


def _docker_available(runner: Callable[..., Any]) -> dict:
    version = _run_docker_command(["docker", "--version"], runner)
    compose = _run_docker_command(["docker", "compose", "version"], runner)
    return {
        "operation": "docker_available",
        "available": version["status"] == "ok" and compose["status"] == "ok",
        "version_status": version["status"],
        "compose_status": compose["status"],
    }


def _run_docker_command(command: list[str], runner: Callable[..., Any]) -> dict:
    try:
        completed = runner(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=600,
            shell=False,
        )
    except FileNotFoundError:
        return {"operation": _operation_name(command), "status": "failed", "error": "docker command not found"}
    except subprocess.TimeoutExpired:
        return {"operation": _operation_name(command), "status": "failed", "error": "docker command timed out"}
    except Exception as exc:
        return {"operation": _operation_name(command), "status": "failed", "error": type(exc).__name__}

    return {
        "operation": _operation_name(command),
        "status": "ok" if int(getattr(completed, "returncode", 1)) == 0 else "failed",
        "returncode": int(getattr(completed, "returncode", 1)),
        "stdout_preview": _safe_text(getattr(completed, "stdout", "")),
        "stderr_preview": _safe_text(getattr(completed, "stderr", "")),
        "command": _safe_command(command),
    }


def _operation_name(command: list[str]) -> str:
    if command[:2] == ["docker", "--version"]:
        return "docker_version"
    if command[:3] == ["docker", "compose", "version"]:
        return "docker_compose_version"
    if command[:3] == ["docker", "compose", "build"]:
        return "compose_build"
    if command[:4] == ["docker", "compose", "up", "-d"]:
        return "compose_up"
    if command[:4] == ["docker", "compose", "run", "--rm"]:
        return "compose_smoke"
    if command[:3] == ["docker", "compose", "down"]:
        return "compose_down"
    return "docker_command"


def _safe_command(command: list[str]) -> list[str]:
    return [part for part in command if not _looks_sensitive(part)]


def _safe_text(value: str, max_chars: int = 500) -> str:
    text = value or ""
    safe_lines: list[str] = []
    for line in text.splitlines():
        if _looks_sensitive(line):
            safe_lines.append("[redacted-line]")
            continue
        safe_line = line
        for marker in SENSITIVE_MARKERS:
            safe_line = _replace_case_insensitive(safe_line, marker, "[redacted]")
        safe_lines.append(safe_line)
    text = "\n".join(safe_lines)
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text


def _looks_sensitive(value: str) -> bool:
    lowered = str(value).lower()
    return any(marker in lowered for marker in SENSITIVE_MARKERS)


def _safe_result(result: dict) -> dict:
    return sanitize_payload(result)


def _replace_case_insensitive(text: str, needle: str, replacement: str) -> str:
    lowered = text.lower()
    needle_lower = needle.lower()
    start = 0
    result: list[str] = []
    while True:
        index = lowered.find(needle_lower, start)
        if index == -1:
            result.append(text[start:])
            break
        result.append(text[start:index])
        result.append(replacement)
        start = index + len(needle)
    return "".join(result)


if __name__ == "__main__":
    raise SystemExit(main())
