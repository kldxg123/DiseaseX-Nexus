from __future__ import annotations

import json
from typing import Any


TREND_VALUES = {"上升", "下降", "稳定"}


def extract_first_json_object(text: str) -> str | None:
    start = None
    depth = 0
    in_string = False
    escaped = False
    for idx, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            if depth == 0:
                start = idx
            depth += 1
        elif char == "}":
            if depth:
                depth -= 1
                if depth == 0 and start is not None:
                    return text[start : idx + 1]
    return None


def normalize_prediction(payload: dict[str, Any], fallback_error: str | None = None) -> dict[str, Any]:
    trend = payload.get("trend")
    if trend not in TREND_VALUES:
        trend = "稳定"
        fallback_error = fallback_error or "invalid_or_missing_trend"
    def clamp_float(key: str, default: float) -> float:
        try:
            value = float(payload.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(0.0, min(1.0, value))

    evidence = payload.get("evidence")
    if not isinstance(evidence, list):
        evidence = []
    evidence = [str(item) for item in evidence if str(item).strip()]
    result = {
        "trend": trend,
        "confidence": clamp_float("confidence", 0.0 if fallback_error else 0.5),
        "risk_score": clamp_float("risk_score", 0.0),
        "evidence": evidence,
        "model_version": str(payload.get("model_version", "unknown")),
        "adapter_version": str(payload.get("adapter_version", "none")),
        "data_provenance": payload.get("data_provenance", {}) if isinstance(payload.get("data_provenance", {}), dict) else {},
    }
    if fallback_error:
        result["error"] = fallback_error
    return result


def repair_json_prediction(text: str, fallback_error: str = "model_output_not_parseable") -> dict[str, Any]:
    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            return normalize_prediction(loaded)
    except Exception:
        pass
    candidate = extract_first_json_object(text)
    if candidate:
        try:
            loaded = json.loads(candidate)
            if isinstance(loaded, dict):
                return normalize_prediction(loaded)
        except Exception:
            pass
    return normalize_prediction(
        {
            "trend": "稳定",
            "confidence": 0.0,
            "risk_score": 0.0,
            "evidence": [],
            "model_version": "fallback",
            "adapter_version": "none",
            "data_provenance": {},
        },
        fallback_error=fallback_error,
    )
