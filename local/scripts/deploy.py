from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.read_server_info import ServerInfo, ServerInfoError, read_server_info

MASK = "***"

DEPLOY_PLAN_STEPS = [
    "validate_server_info",
    "run_local_tests",
    "check_local_required_files",
    "check_ssh_command",
    "check_remote_docker",
    "create_remote_deploy_path",
    "rsync_project_files",
    "upload_env_example_or_rendered_env",
    "remote_docker_compose_build",
    "remote_docker_compose_up",
    "remote_health_check",
    "remote_smoke_test",
]

RSYNC_EXCLUDES = [
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".env",
    ".env.*",
    "服务器信息.txt",
    "鏈嶅姟鍣ㄤ俊鎭?txt",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "mlruns",
    ".minio",
]

REQUIRED_PROJECT_FILES = [
    "docker-compose.yml",
    "Dockerfile",
    "pyproject.toml",
    "apps/api/main.py",
    "apps/dashboard/streamlit_app.py",
    "packages",
    "scripts/smoke_test.py",
]

SENSITIVE_MARKERS = [
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "private key",
    "ssh_key",
]


def mask_deploy_value(value: str | None) -> str | None:
    if value is None:
        return None
    return MASK


def sanitize_command_preview(command: list[str] | str) -> str:
    if isinstance(command, list):
        text = " ".join(str(part) for part in command)
    else:
        text = str(command)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "<host>", text)
    text = re.sub(r"[A-Za-z0-9._-]+@<host>", "***@***", text)
    text = re.sub(r"\b[A-Za-z0-9._-]+@[A-Za-z0-9._-]+\b", "***@***", text)
    text = re.sub(r"(?:(?<=\s)|^)/(?:[A-Za-z0-9._-]+/)*[A-Za-z0-9._-]+", "<path>", text)
    text = re.sub(r"[A-Za-z]:\\(?:[^\\\s]+\\)*[^\\\s]+", "<path>", text)
    replacements = [
        "--server-info",
        "ssh_key_path",
        "deploy_path",
    ]
    for marker in replacements:
        text = text.replace(marker, marker)
    for sensitive in SENSITIVE_MARKERS:
        text = _replace_case_insensitive(text, sensitive, "[redacted]")
    return _truncate(text)


def build_deploy_plan(server_info: ServerInfo, *, project_root: Path) -> list[dict]:
    remote = f"{MASK}@{MASK}"
    ssh_base = ["ssh", "-p", "<port>", "-i", MASK, remote]
    excludes = [f"--exclude={pattern}" for pattern in RSYNC_EXCLUDES]
    previews: dict[str, list[str] | str] = {
        "validate_server_info": "validate server info fields (all values redacted)",
        "run_local_tests": "python -m pytest tests/test_smoke_test.py tests/test_api_health.py",
        "check_local_required_files": "python scripts/check_compose.py",
        "check_ssh_command": [*ssh_base, "echo", "ok"],
        "check_remote_docker": [*ssh_base, "docker compose version"],
        "create_remote_deploy_path": [*ssh_base, "mkdir -p", "<deploy_path>"],
        "rsync_project_files": ["rsync", "-az", f"<safe_excludes:{len(excludes)}>", "./", f"{remote}:<deploy_path>"],
        "upload_env_example_or_rendered_env": ["scp", "-P", "<port>", "-i", MASK, ".env.example", f"{remote}:<deploy_path>/.env.example"],
        "remote_docker_compose_build": [*ssh_base, "cd <deploy_path> && docker compose build"],
        "remote_docker_compose_up": [*ssh_base, "cd <deploy_path> && docker compose up -d"],
        "remote_health_check": "curl --fail http://<host-or-domain>:8000/health",
        "remote_smoke_test": [*ssh_base, "cd <deploy_path> && python scripts/smoke_test.py"],
    }
    _unused = (server_info, project_root)
    return [
        {
            "name": name,
            "command_preview": sanitize_command_preview(previews[name]),
            "would_execute": True,
            "dry_run": True,
        }
        for name in DEPLOY_PLAN_STEPS
    ]


def validate_required_project_files(project_root: Path) -> list[str]:
    missing: list[str] = []
    for relative in REQUIRED_PROJECT_FILES:
        if not (project_root / relative).exists():
            missing.append(relative)
    return missing


def run_command(command: list[str], *, dry_run: bool = True, timeout_seconds: int = 60) -> dict:
    preview = sanitize_command_preview(command)
    if dry_run:
        return {
            "status": "skipped",
            "dry_run": True,
            "would_execute": True,
            "command_preview": preview,
            "returncode": None,
        }
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        return {
            "status": "failed",
            "dry_run": False,
            "would_execute": True,
            "command_preview": preview,
            "returncode": None,
            "error": sanitize_text(f"{type(exc).__name__}"),
        }
    return {
        "status": "success" if completed.returncode == 0 else "failed",
        "dry_run": False,
        "would_execute": True,
        "command_preview": preview,
        "returncode": completed.returncode,
        "stdout_summary": sanitize_text(completed.stdout),
        "stderr_summary": sanitize_text(completed.stderr),
    }


def deploy(server_info_path: str | Path, *, execute: bool = False, project_root: str | Path | None = None) -> dict:
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    dry_run = not execute
    warnings: list[str] = []
    try:
        server_info = read_server_info(server_info_path)
        missing_fields: list[str] = []
    except ServerInfoError as exc:
        message = str(exc)
        return {
            "status": "failed",
            "dry_run": dry_run,
            "execute_requested": execute,
            "missing_fields": _missing_fields_from_error(message),
            "error": sanitize_text(message),
        }
    missing_files = validate_required_project_files(root)
    if missing_files:
        warnings.append("missing required project files")

    plan = build_deploy_plan(server_info, project_root=root)
    command_results: list[dict] = []
    if execute:
        commands = _commands_for_execute_plan()
        command_results = [run_command(command, dry_run=False) for command in commands]

    status = "ok"
    if missing_files:
        status = "failed"
    if any(result.get("status") == "failed" for result in command_results):
        status = "failed"

    return sanitize_payload(
        {
            "status": status,
            "dry_run": dry_run,
            "execute_requested": execute,
            "server_info_summary": {
                "required_fields_present": True,
                "port": server_info.port,
                "domain_present": bool(server_info.domain),
                "values_redacted": True,
            },
            "missing_fields": missing_fields,
            "missing_required_files": missing_files,
            "plan": plan,
            "command_results": command_results,
            "warnings": warnings,
            "ssh_executed": bool(execute and command_results),
            "rsync_executed": bool(execute and command_results),
            "docker_started": False,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe deployment dry-run planner.")
    parser.add_argument("--server-info", required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-execute", action="store_true")
    args = parser.parse_args(argv)

    if args.execute and not args.confirm_execute:
        print(
            json.dumps(
                {
                    "status": "refused",
                    "dry_run": False,
                    "execute_requested": True,
                    "error": "execute requires --confirm-execute",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    result = deploy(args.server_info, execute=bool(args.execute))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


def sanitize_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value)
    for marker in SENSITIVE_MARKERS:
        text = _replace_case_insensitive(text, marker, "[redacted]")
    return _truncate(text)


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                safe[key] = MASK if item is not None else None
            else:
                safe[key] = sanitize_payload(item)
        return safe
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return sanitize_text(str(value))


def _commands_for_execute_plan() -> list[list[str]]:
    return [
        ["python", "-m", "pytest", "tests/test_smoke_test.py", "tests/test_api_health.py"],
        ["python", "scripts/check_compose.py"],
        ["ssh", "-p", "<port>", "-i", MASK, f"{MASK}@{MASK}", "echo ok"],
        ["rsync", "-az", "<safe_excludes>", "./", f"{MASK}@{MASK}:<deploy_path>"],
    ]


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ("secret", "token", "password", "key"))


def _missing_fields_from_error(message: str) -> list[str]:
    prefix = "Missing required server info fields:"
    if prefix not in message:
        return []
    tail = message.split(prefix, 1)[1].strip().rstrip(".")
    return [item.strip() for item in tail.split(",") if item.strip()]


def _replace_case_insensitive(text: str, needle: str, replacement: str) -> str:
    lowered = text.lower()
    needle_lower = needle.lower()
    start = 0
    result = []
    while True:
        index = lowered.find(needle_lower, start)
        if index == -1:
            result.append(text[start:])
            break
        result.append(text[start:index])
        result.append(replacement)
        start = index + len(needle)
    return "".join(result)


def _truncate(value: str, max_chars: int = 240) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "...[truncated]"


if __name__ == "__main__":
    raise SystemExit(main())
