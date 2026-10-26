from __future__ import annotations

import re
from typing import Iterable


PLACEHOLDER_HOST = "<remote-host>"
PLACEHOLDER_USER = "<remote-user>"
PLACEHOLDER_SERVER = "<remote-server>"
PLACEHOLDER_SECRET = 'REDACTED'


def sanitize_text(text: str, secrets: Iterable[str] | None = None) -> str:
    sanitized = str(text)
    for secret in secrets or []:
        if secret:
            sanitized = sanitized.replace(str(secret), PLACEHOLDER_SECRET)
    sanitized = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", PLACEHOLDER_HOST, sanitized)
    sanitized = re.sub(
        r"(?i)(password|passwd|pwd|token|hf_token|api_key|private_key)\s*[:=]\s*\S+",
        lambda match: f"{match.group(1)}={PLACEHOLDER_SECRET}",
        sanitized,
    )
    sanitized = re.sub(r"(?i)(user|username)\s*[:=]\s*\S+", r"\1=<remote-user>", sanitized)
    sanitized = re.sub(r"(?i)(host|hostname|ip)\s*[:=]\s*\S+", r"\1=<remote-host>", sanitized)
    sanitized = re.sub(r"\b\S+@<remote-host>\b", PLACEHOLDER_SERVER, sanitized)
    sanitized = re.sub(r"(probe:hostname\r?\n)([^\r\n]+)", r"\1<remote-host>", sanitized)
    sanitized = re.sub(r"(probe:uname\r?\nLinux\s+)\S+", r"\1<remote-host>", sanitized)
    return sanitized


def sanitized_dict(payload: dict, secret_keys: set[str] | None = None) -> dict:
    secret_keys = secret_keys or {"host", "hostname", "ip", "user", "username", "password", "key_file", "identity_file"}
    result = {}
    for key, value in payload.items():
        if key.lower() in secret_keys and value:
            if key.lower() in {"user", "username"}:
                result[key] = PLACEHOLDER_USER
            elif key.lower() in {"host", "hostname", "ip"}:
                result[key] = PLACEHOLDER_HOST
            else:
                result[key] = PLACEHOLDER_SECRET
        else:
            result[key] = value
    return result
