from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MASK = "***"

SUPPORTED_KEYS = {
    "host": "host",
    "hostname": "host",
    "server_host": "host",
    "port": "port",
    "ssh_port": "port",
    "user": "user",
    "username": 'REDACTED',
    "ssh_user": 'REDACTED',
    "ssh_key_path": "ssh_key_path",
    "key_path": "ssh_key_path",
    "private_key_path": 'REDACTED',
    "identity_file": "ssh_key_path",
    "deploy_path": "deploy_path",
    "remote_path": "deploy_path",
    "domain": "domain",
}

SENSITIVE_FIELD_TERMS = (
    "host",
    "server",
    "user",
    "domain",
    "path",
    "secret",
    "token",
    "password",
    "passwd",
    "key",
    "credential",
)


class ServerInfoError(ValueError):
    """Raised when server info cannot be parsed or validated.

    Error messages must never include raw field values or file contents.
    """


@dataclass(frozen=True, repr=False)
class ServerInfo:
    host: str
    port: int
    user: str
    ssh_key_path: str
    deploy_path: str
    domain: str | None = None

    def redacted_dict(self) -> dict[str, Any]:
        return {
            "host": MASK,
            "port": self.port,
            "user": MASK,
            "ssh_key_path": MASK,
            "deploy_path": MASK,
            "domain": MASK if self.domain else None,
        }

    def __repr__(self) -> str:
        return f"ServerInfo({self.redacted_dict()!r})"

    __str__ = __repr__


def _is_sensitive_field(name: str) -> bool:
    lowered = name.lower()
    return any(term in lowered for term in SENSITIVE_FIELD_TERMS)


def sanitize_server_info_record(data: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if _is_sensitive_field(str(key)):
            sanitized[key] = MASK if value is not None else None
        elif isinstance(value, dict):
            sanitized[key] = sanitize_server_info_record(value)
        else:
            sanitized[key] = value
    return sanitized


def _strip_value(value: Any) -> str:
    if not isinstance(value, str):
        return str(value)
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1].strip()
    return stripped


def _normalize_key(key: str) -> str:
    normalized = key.strip().lower().replace("-", "_")
    if normalized not in SUPPORTED_KEYS:
        if _is_sensitive_field(normalized):
            raise ServerInfoError(f"Unsupported sensitive field at key {key.strip()!r}.")
        raise ServerInfoError(f"Unsupported server info field at key {key.strip()!r}.")
    return SUPPORTED_KEYS[normalized]


def _parse_json(text: str) -> dict[str, str]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ServerInfoError(f"Invalid JSON server info at line {exc.lineno}.") from exc
    if not isinstance(payload, dict):
        raise ServerInfoError("JSON server info must be an object.")
    return {_normalize_key(str(key)): _strip_value(value) for key, value in payload.items()}


def _parse_line_format(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            raise ServerInfoError(f"Invalid server info line at {line_number}.")
        normalized_key = _normalize_key(key)
        result[normalized_key] = _strip_value(value)
    return result


def parse_server_info_text(text: str) -> ServerInfo:
    stripped = text.strip()
    if not stripped:
        raise ServerInfoError("Server info file is empty.")

    parsed = _parse_json(stripped) if stripped.startswith("{") else _parse_line_format(stripped)
    return _build_server_info(parsed)


def _build_server_info(parsed: dict[str, str]) -> ServerInfo:
    required = ("host", "user", "ssh_key_path", "deploy_path")
    missing = [key for key in required if not parsed.get(key)]
    if missing:
        raise ServerInfoError(f"Missing required server info fields: {', '.join(missing)}.")

    raw_port = parsed.get("port", "22")
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ServerInfoError("Invalid server info port.") from exc
    if port < 1 or port > 65535:
        raise ServerInfoError("Invalid server info port.")

    return ServerInfo(
        host=parsed["host"],
        port=port,
        user=parsed["user"],
        ssh_key_path=parsed["ssh_key_path"],
        deploy_path=parsed["deploy_path"],
        domain=parsed.get("domain") or None,
    )


def read_server_info(path: str | Path) -> ServerInfo:
    server_info_path = Path(path)
    try:
        text = server_info_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise ServerInfoError("Server info file was not found.") from exc
    except OSError as exc:
        raise ServerInfoError("Server info file could not be read.") from exc
    return parse_server_info_text(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safely validate a server info file.")
    parser.add_argument("path", help="Path to the server info file to validate.")
    args = parser.parse_args(argv)

    server_info = read_server_info(args.path)
    print(json.dumps(server_info.redacted_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
