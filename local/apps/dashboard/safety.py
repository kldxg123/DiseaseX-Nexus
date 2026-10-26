from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

SENSITIVE_KEYS = (
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "private_key",
    "secret_key",
    "api_key",
    "access_key",
    "ssh_key",
    "authorization",
    "auth_header",
)

SENSITIVE_VALUE_MARKERS = (
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "api_key",
    "private key",
    "ssh_key",
)

OMITTED_KEY_MARKERS = (
    "html",
    "full_html",
    "raw_text",
    "body_text",
    "response_body",
    "fixture_content",
    "metadata_json",
    "snippet",
    "feature_json",
    "lesson_json",
    "provenance_json",
    "raw_html",
    "raw_response",
    "prompt",
    "completion",
    "full_abstract",
    "raw_xml",
)


def sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return sanitize_dict(value)
    if isinstance(value, list):
        return [sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        if _looks_sensitive_text(value):
            return "***"
        return truncate_text(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return truncate_text(str(value))


def sanitize_dict(data: dict) -> dict:
    safe: dict[str, Any] = {}
    for key, value in (data or {}).items():
        key_text = str(key)
        if _should_omit_key(key_text):
            continue
        if _is_sensitive_key(key_text):
            safe[key_text] = "***"
        else:
            safe[key_text] = sanitize_value(value)
    return safe


def truncate_text(value: str | None, max_chars: int = 120) -> str | None:
    if value is None:
        return None
    text = str(value)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "...[truncated]"


def safe_json_preview(data, max_chars: int = 500) -> str:
    try:
        rendered = json.dumps(sanitize_value(data), ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        rendered = json.dumps({"preview": "unavailable"}, sort_keys=True)
    return truncate_text(rendered, max_chars) or ""


def _looks_sensitive_text(value: str) -> bool:
    text = value.lower()
    return any(marker in text for marker in SENSITIVE_VALUE_MARKERS)


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    if any(part in normalized for part in SENSITIVE_KEYS):
        return True
    if normalized == "key":
        return True
    return normalized.endswith("_key") and not normalized.endswith("_keys")


def _should_omit_key(key: str) -> bool:
    normalized = key.lower()
    return any(marker in normalized for marker in OMITTED_KEY_MARKERS)
