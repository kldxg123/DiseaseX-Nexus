from __future__ import annotations

from datetime import date, datetime
from typing import Any

SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "passwd",
    "credential",
    "private",
    "ssh",
    "server",
    "key",
)


def sanitize_mapping(value: Any, *, max_string: int = 240, max_items: int = 40) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                safe["truncated"] = True
                break
            key_text = str(key)
            if _is_sensitive_key(key_text):
                safe[key_text] = "***"
            else:
                safe[key_text] = sanitize_mapping(item, max_string=max_string, max_items=max_items)
        return safe
    if isinstance(value, list):
        return [sanitize_mapping(item, max_string=max_string, max_items=max_items) for item in value[:max_items]]
    if isinstance(value, tuple):
        return [sanitize_mapping(item, max_string=max_string, max_items=max_items) for item in value[:max_items]]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return _truncate(value, max_string)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate(str(value), max_string)


def notes_summary(notes: str | None) -> dict[str, Any]:
    return {
        "notes_present": bool(notes),
    }


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "...[truncated]"
