from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

JOB_STATUS_SUCCESS = "success"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_SKIPPED = "skipped"
VALID_JOB_STATUSES = {JOB_STATUS_SUCCESS, JOB_STATUS_FAILED, JOB_STATUS_SKIPPED}

SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "passwd",
    "key",
    "credential",
    "private",
    "ssh",
    "server",
    "host",
    "username",
    "database_url",
)

SENSITIVE_VALUE_MARKERS = (
    "secret",
    "token",
    "password",
    "private key",
    "credential",
    "ssh_key",
)


@dataclass(frozen=True)
class JobDefinition:
    name: str
    description: str
    default_params: dict[str, Any] = field(default_factory=dict)
    safe_for_scheduler: bool = True
    allows_network: bool = False


@dataclass
class JobRunResult:
    job_name: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_seconds: float | None
    summary: dict[str, Any]
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return sanitize_for_json(payload)


def sanitize_for_json(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        redacted_count = 0
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_text(key_text):
                redacted_count += 1
                safe[f"redacted_field_{redacted_count}"] = "***"
            else:
                safe[key_text] = sanitize_for_json(item)
        return safe
    if isinstance(value, list):
        return [sanitize_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_for_json(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        if _is_sensitive_text(value):
            return "[redacted]"
        return _truncate(value)
    return _truncate(str(value))


def sanitize_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    sanitized = sanitize_for_json(text)
    return sanitized if isinstance(sanitized, str) else "error"


def _is_sensitive_text(value: str) -> bool:
    lowered = value.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS) or any(
        marker in lowered for marker in SENSITIVE_VALUE_MARKERS
    )


def _truncate(value: str, max_chars: int = 240) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "...[truncated]"
