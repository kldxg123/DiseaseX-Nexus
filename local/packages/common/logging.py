from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

MASK = "***"
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


def mask_sensitive(value: str | None) -> str | None:
    if value is None:
        return None
    return MASK


def _is_sensitive_field(name: str) -> bool:
    lowered = name.lower()
    return any(term in lowered for term in SENSITIVE_FIELD_TERMS)


def sanitize_log_record(data: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in data.items():
        if _is_sensitive_field(str(key)):
            sanitized[key] = mask_sensitive(str(value) if value is not None else None)
        elif isinstance(value, Mapping):
            sanitized[key] = sanitize_log_record(dict(value))
        elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            sanitized[key] = [
                sanitize_log_record(dict(item)) if isinstance(item, Mapping) else item
                for item in value
            ]
        else:
            sanitized[key] = value
    return sanitized


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key
            not in {
                "args",
                "asctime",
                "created",
                "exc_info",
                "exc_text",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "msg",
                "name",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "stack_info",
                "thread",
                "threadName",
            }
        }
        if extra:
            payload["extra"] = extra
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(sanitize_log_record(payload), ensure_ascii=False, default=str)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logger.propagate = False
    return logger
