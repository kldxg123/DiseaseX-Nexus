from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class EvidenceStore:
    def __init__(self, output_dir: str | Path = "reports/milestone2_readiness"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_json(self, filename: str, payload: dict[str, Any]) -> str:
        path = self.output_dir / filename
        path.write_text(json.dumps(_safe_json(payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        return str(path)

    def write_markdown(self, filename: str, content: str) -> str:
        path = self.output_dir / filename
        path.write_text(_safe_text(content), encoding="utf-8")
        return str(path)


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if any(marker in lowered for marker in _BLOCKED):
                continue
            result[key_text] = _safe_json(item)
        return result
    if isinstance(value, list):
        return [_safe_json(item) for item in value[:300]]
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value[:300]]
    if isinstance(value, str):
        return _safe_text(value)
    return value


def _safe_text(value: str) -> str:
    text = str(value)
    lowered = text.lower()
    if any(marker in lowered for marker in ("password", "token", "secret", "private key", "ssh_key_path")):
        return "[redacted]"
    if "<html" in lowered or "</html" in lowered:
        return "[redacted-html]"
    return text


_BLOCKED = (
    "raw_text",
    "metadata_json",
    "feature_json",
    "provenance_json",
    "lesson_json",
    "html",
    "response_body",
    "password",
    "token",
    "secret",
    "private",
    "ssh",
)
