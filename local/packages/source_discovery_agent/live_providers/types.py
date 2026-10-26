from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LiveDiscoveryRequest:
    country: str
    disease: str
    provider: str
    query_text: str | None = None
    max_requests: int = 1
    allow_network: bool = False
    terms_reviewed: bool = False
    manual_reviewed: bool = False
    robots_allowed: bool | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LiveDiscoveryProviderResult:
    provider: str
    request_count: int
    search_results: list[dict]
    warnings: list[str]
    network_accessed: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    failure_type: str | None = None
    retry_allowed: bool = False
    cooldown_recommended: bool = False
    retry_after_seconds: int | None = None


class LiveDiscoveryProvider(Protocol):
    provider_name: str

    def discover(self, request: LiveDiscoveryRequest) -> LiveDiscoveryProviderResult:
        ...


def sanitize_search_result(result: dict[str, Any]) -> dict[str, Any]:
    allowed = {"title", "url", "snippet", "source_name", "rank", "published_at"}
    sanitized: dict[str, Any] = {}
    for key, value in result.items():
        if key not in allowed:
            continue
        if isinstance(value, (int, float, bool)) or value is None:
            sanitized[key] = value
            continue
        text = str(value)
        lowered = text.lower()
        if any(part in lowered for part in ("password", "token", "secret", "credential", "private", "ssh")):
            sanitized[key] = "[redacted]"
        else:
            sanitized[key] = text[:260]
    return sanitized
