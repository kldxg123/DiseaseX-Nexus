from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_SERVICES = ["postgres", "redis", "minio", "api", "worker", "dashboard", "mlflow"]
REQUIRED_PORTS = {
    "api": ["8000"],
    "dashboard": ["8501"],
    "mlflow": ["5000"],
    "minio": ["9000", "9001"],
}
FORBIDDEN_MOUNT_MARKERS = [
    "服务器信息.txt",
    "鏈嶅姟鍣ㄤ俊鎭?txt",
    "private key",
    "id_rsa",
    "id_ed25519",
    ".env:",
    ".env:/",
    ".env\\",
]
SENSITIVE_MARKERS = ["secret", "token", "password", "private key", "ssh_key_path"]
STRICT_FORBIDDEN_MOUNT_MARKERS = [
    "服务器信息.txt",
    ".pem",
    ".key",
    ".token",
    ".secret",
]
FORBIDDEN_WORKER_COMMAND_MARKERS = ["ssh", "deploy", "rsync", "real_crawl", "live_crawl"]


def check_compose(path: str | Path | None = None) -> dict:
    compose_path = Path(path) if path is not None else PROJECT_ROOT / "docker-compose.yml"
    try:
        text = compose_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"status": "failed", "error": "docker-compose.yml not found"}
    except OSError:
        return {"status": "failed", "error": "docker-compose.yml could not be read"}

    services = _extract_services(text)
    service_checks = {name: name in services for name in REQUIRED_SERVICES}
    port_checks = {
        service: {port: _service_has_port(text, service, port) for port in ports}
        for service, ports in REQUIRED_PORTS.items()
    }
    forbidden_mounts = _find_forbidden_mounts(text)
    command_checks = _command_checks(text)
    ports_checked = _ports_ok(port_checks)
    warnings = _warnings_for_checks(service_checks, port_checks, command_checks, forbidden_mounts)
    status = (
        "ok"
        if all(service_checks.values())
        and ports_checked
        and all(command_checks.values())
        and not forbidden_mounts
        else "failed"
    )
    return sanitize_payload(
        {
            "status": status,
            "compose_file": "docker-compose.yml",
            "services": service_checks,
            "service_names": sorted(services),
            "ports": port_checks,
            "ports_checked": ports_checked,
            "commands": command_checks,
            "forbidden_mounts_found": forbidden_mounts,
            "warnings": warnings,
            "docker_started": False,
        }
    )


def main(argv: list[str] | None = None) -> int:
    _unused = argv
    result = check_compose()
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


def sanitize_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, str):
        text = value
        for marker in SENSITIVE_MARKERS:
            text = _replace_case_insensitive(text, marker, "[redacted]")
        return text[:240] + "...[truncated]" if len(text) > 240 else text
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(type(value).__name__)


def _extract_services(text: str) -> set[str]:
    services: set[str] = set()
    in_services = False
    for line in text.splitlines():
        if re.match(r"^services:\s*$", line):
            in_services = True
            continue
        if in_services:
            match = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
            if match:
                services.add(match.group(1))
            elif line and not line.startswith(" ") and not line.startswith("#"):
                break
    return services


def _service_block(text: str, service: str) -> str:
    pattern = re.compile(rf"^  {re.escape(service)}:\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    next_match = re.search(r"^  [A-Za-z0-9_-]+:\s*$|^[A-Za-z0-9_-]+:\s*$", text[start:], flags=re.MULTILINE)
    end = start + next_match.start() if next_match else len(text)
    return text[start:end]


def _service_has_port(text: str, service: str, port: str) -> bool:
    block = _service_block(text, service)
    return f'"{port}:{port}"' in block or f"'{port}:{port}'" in block or f"{port}:{port}" in block


def _find_forbidden_mounts(text: str) -> list[str]:
    found: list[str] = []
    lower = text.lower()
    for marker in [*FORBIDDEN_MOUNT_MARKERS, *STRICT_FORBIDDEN_MOUNT_MARKERS]:
        if marker.lower() in lower:
            found.append(_safe_marker(marker))
    return sorted(set(found))


def _safe_marker(marker: str) -> str:
    if "env" in marker.lower():
        return ".env mount"
    if (
        "key" in marker.lower()
        or "rsa" in marker.lower()
        or "ed25519" in marker.lower()
        or "pem" in marker.lower()
    ):
        return "key-file mount"
    if "token" in marker.lower() or "secret" in marker.lower():
        return "credential-file mount"
    return "server-info mount"


def _ports_ok(port_checks: dict[str, dict[str, bool]]) -> bool:
    return all(all(checks.values()) for checks in port_checks.values())


def _command_checks(text: str) -> dict[str, bool]:
    api_block = _service_block(text, "api")
    dashboard_block = _service_block(text, "dashboard")
    worker_block = _service_block(text, "worker")
    api_command = _command_text(api_block)
    dashboard_command = _command_text(dashboard_block)
    worker_command = _command_text(worker_block).lower()
    return {
        "api": "uvicorn apps.api.main:app" in api_command,
        "dashboard": "streamlit run apps/dashboard/streamlit_app.py" in dashboard_command,
        "worker": bool(worker_command)
        and not any(marker in worker_command for marker in FORBIDDEN_WORKER_COMMAND_MARKERS),
    }


def _command_text(block: str) -> str:
    lines: list[str] = []
    in_command = False
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("command:"):
            in_command = True
            lines.append(stripped[len("command:") :].strip())
            continue
        if in_command:
            if stripped.startswith("- ") or line.startswith("      "):
                lines.append(stripped)
                continue
            break
    return " ".join(lines)


def _warnings_for_checks(
    service_checks: dict[str, bool],
    port_checks: dict[str, dict[str, bool]],
    command_checks: dict[str, bool],
    forbidden_mounts: list[str],
) -> list[str]:
    warnings: list[str] = []
    warnings.extend(f"missing service: {name}" for name, ok in service_checks.items() if not ok)
    for service, checks in port_checks.items():
        warnings.extend(f"missing port mapping: {service}:{port}" for port, ok in checks.items() if not ok)
    warnings.extend(f"unsafe or missing command: {name}" for name, ok in command_checks.items() if not ok)
    if forbidden_mounts:
        warnings.append("forbidden mount detected")
    return sorted(set(warnings))


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
