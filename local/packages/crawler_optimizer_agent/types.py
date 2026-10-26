from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

FAILURE_TYPES = {
    "fetch_timeout",
    "http_error",
    "robots_denied",
    "rate_limited",
    "unsupported_content_type",
    "empty_content",
    "html_structure_changed",
    "parser_failed",
    "duplicate_content",
    "low_relevance",
    "stale_source",
    "broken_link",
    "possible_better_source_found",
    "unknown",
}

ACTION_TYPES = {
    "noop",
    "mark_degraded",
    "disable_source",
    "lower_source_score",
    "switch_parser",
    "retry_with_different_parser",
    "reduce_crawl_frequency",
    "create_connector_spec",
    "add_candidate_source",
    "manual_review",
}

SEVERITIES = {"low", "medium", "high", "critical"}
RISK_LEVELS = {"low", "medium", "high"}
SENSITIVE_MARKERS = {
    "secret",
    "token",
    "password",
    "private key",
    "credential",
    "ssh_key_path",
}
LONG_TEXT_KEYS = {
    "raw_text",
    "body_text",
    "html",
    "feature_json",
    "lesson_json",
    "provenance_json",
    "metadata_json",
    "payload",
}


@dataclass(frozen=True)
class CrawlerFailureCase:
    source_id: str | None
    raw_document_id: str | None
    source_name: str | None
    url: str | None
    failure_type: str
    severity: str
    confidence: float
    evidence: dict[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "failure_type", _enum_or_default(self.failure_type, FAILURE_TYPES, "unknown"))
        object.__setattr__(self, "severity", _enum_or_default(self.severity, SEVERITIES, "medium"))
        object.__setattr__(self, "confidence", clamp_confidence(self.confidence))

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "raw_document_id": self.raw_document_id,
            "source_name": safe_text(self.source_name),
            "url": safe_url(self.url),
            "failure_type": self.failure_type,
            "severity": self.severity,
            "confidence": self.confidence,
            "evidence": safe_mapping(self.evidence),
            "created_at": _aware_datetime(self.created_at).isoformat(),
        }


@dataclass(frozen=True)
class OptimizationAction:
    action_type: str
    target_type: str
    target_id: str | None
    reason: str
    confidence: float
    risk_level: str
    requires_human_review: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        action_type = _enum_or_default(self.action_type, ACTION_TYPES, "manual_review")
        risk_level = _enum_or_default(self.risk_level, RISK_LEVELS, "medium")
        requires_review = bool(self.requires_human_review or risk_level == "high")
        object.__setattr__(self, "action_type", action_type)
        object.__setattr__(self, "risk_level", risk_level)
        object.__setattr__(self, "confidence", clamp_confidence(self.confidence))
        object.__setattr__(self, "requires_human_review", requires_review)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "target_type": safe_text(self.target_type),
            "target_id": self.target_id,
            "reason": safe_text(self.reason),
            "confidence": self.confidence,
            "risk_level": self.risk_level,
            "requires_human_review": self.requires_human_review,
            "metadata": safe_mapping(self.metadata),
        }


@dataclass(frozen=True)
class ConnectorSpec:
    connector_name: str
    source_name: str
    base_url: str
    source_type: str
    parser_strategy: str
    expected_fields: list[str]
    sample_urls: list[str]
    robots_required: bool
    rate_limit_seconds: float
    requires_human_review: bool
    test_fixture_required: bool
    live_fetch_supported: bool = False
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_name": safe_text(self.connector_name),
            "source_name": safe_text(self.source_name),
            "base_url": safe_url(self.base_url),
            "source_type": safe_text(self.source_type),
            "parser_strategy": safe_text(self.parser_strategy),
            "expected_fields": [safe_text(field) for field in self.expected_fields],
            "sample_urls": [safe_url(url) for url in self.sample_urls[:3]],
            "robots_required": self.robots_required,
            "rate_limit_seconds": float(self.rate_limit_seconds),
            "requires_human_review": self.requires_human_review,
            "test_fixture_required": self.test_fixture_required,
            "live_fetch_supported": self.live_fetch_supported,
            "notes": safe_text(self.notes) if self.notes else None,
        }


@dataclass(frozen=True)
class OptimizationReport:
    status: str
    sources_analyzed: int
    documents_analyzed: int
    failure_cases: list[dict]
    recommended_actions: list[dict]
    connector_specs: list[dict]
    summary: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": safe_text(self.status),
            "sources_analyzed": int(self.sources_analyzed),
            "documents_analyzed": int(self.documents_analyzed),
            "failure_cases": [safe_mapping(case) for case in self.failure_cases],
            "recommended_actions": [safe_mapping(action) for action in self.recommended_actions],
            "connector_specs": [safe_mapping(spec) for spec in self.connector_specs],
            "summary": safe_mapping(self.summary),
            "warnings": [safe_text(warning) for warning in self.warnings],
        }


def clamp_confidence(value: float | int | None, default: float = 0.0) -> float:
    if value is None:
        return float(default)
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(0.0, min(1.0, parsed))


def safe_mapping(payload: Any) -> Any:
    if isinstance(payload, dict):
        result: dict[str, Any] = {}
        for key, value in payload.items():
            key_text = str(key)
            if _looks_sensitive(key_text) or key_text in LONG_TEXT_KEYS:
                continue
            result[safe_text(key_text)] = safe_mapping(value)
        return result
    if isinstance(payload, list):
        return [safe_mapping(item) for item in payload[:50]]
    if isinstance(payload, tuple):
        return [safe_mapping(item) for item in payload[:50]]
    if isinstance(payload, datetime):
        return _aware_datetime(payload).isoformat()
    if isinstance(payload, (str, int, float, bool)) or payload is None:
        return safe_text(payload) if isinstance(payload, str) else payload
    return safe_text(str(payload))


def safe_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    if _looks_sensitive(text):
        return "[redacted]"
    if len(text) > 240:
        return text[:240] + "...[truncated]"
    return text


def safe_url(value: str | None) -> str | None:
    if not value:
        return None
    parsed = urlsplit(str(value))
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path or "", "", ""))


def _looks_sensitive(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in SENSITIVE_MARKERS)


def _enum_or_default(value: str, allowed: set[str], default: str) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in allowed else default


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
