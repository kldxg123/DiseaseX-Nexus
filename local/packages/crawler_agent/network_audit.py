from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

SENSITIVE_MARKERS = (
    "secret",
    "token",
    "password",
    "private key",
    "ssh_key_path",
    "credential",
)


@dataclass(frozen=True)
class NetworkAuditRecord:
    source_key: str
    url: str | None
    allowed: bool
    reason: str
    request_count: int
    created_at: datetime
    warnings: list[str]


class InMemoryNetworkAuditLog:
    def __init__(self) -> None:
        self._records: list[NetworkAuditRecord] = []

    def record(self, record: NetworkAuditRecord) -> None:
        self._records.append(record)

    def list_records(self, limit: int = 100) -> list[NetworkAuditRecord]:
        if limit < 1:
            return []
        return list(reversed(self._records[-limit:]))

    def summary(self) -> dict:
        by_source = Counter(record.source_key for record in self._records)
        allowed = sum(1 for record in self._records if record.allowed)
        denied = len(self._records) - allowed
        return {
            "total": len(self._records),
            "allowed": allowed,
            "denied": denied,
            "by_source": dict(sorted(by_source.items())),
        }


def safe_audit_record(record: NetworkAuditRecord) -> dict:
    created_at = record.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return {
        "source": _sanitize_text(record.source_key),
        "url": _sanitize_url(record.url) if record.url else None,
        "allowed": record.allowed,
        "reason": _sanitize_text(record.reason),
        "request_count": record.request_count,
        "created_at": created_at.isoformat(),
        "warnings": [_sanitize_text(warning) for warning in record.warnings],
    }


def _sanitize_url(value: str) -> str:
    parsed = urlsplit(str(value))
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    # Drop userinfo, query, and fragment. Query parameters often carry credentials.
    return urlunsplit((parsed.scheme, host, parsed.path or "", "", ""))


def _sanitize_text(value: object) -> str:
    text = str(value)
    for marker in SENSITIVE_MARKERS:
        if marker.lower() in text.lower():
            return "[redacted]"
    return text
