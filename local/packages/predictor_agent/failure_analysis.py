from __future__ import annotations

from typing import Any


FAILURE_TYPES = {
    "late_signal",
    "false_alarm",
    "missing_source",
    "extraction_error",
    "model_error",
    "label_delay",
    "unknown",
}

LESSON_VERSION = "feedback_experience_v1"


def classify_failure_type(
    *,
    feedback: Any,
    prediction: Any,
    true_cases: float | None = None,
    true_outbreak_label: bool | None = None,
) -> str:
    verdict = _feedback_verdict(feedback)
    if verdict != "fail":
        return "unknown"

    reason_text = " ".join(
        value
        for value in (
            _feedback_value(feedback, "reason", "failure_reason"),
            _feedback_value(feedback, "notes", "notes"),
        )
        if value
    ).lower()
    if any(token in reason_text for token in ("source", "missing data", "data source")):
        return "missing_source"
    if any(token in reason_text for token in ("extract", "wrong number", "parser")):
        return "extraction_error"
    if any(token in reason_text for token in ("delay", "reported late")):
        return "label_delay"

    actual_label = _coalesce_bool(true_outbreak_label, _feedback_value(feedback, "true_outbreak_label", "actual_outbreak_label"))
    probability = _prediction_probability(prediction)
    if probability is not None and actual_label is not None:
        if probability >= 0.5 and actual_label is False:
            return "false_alarm"
        if probability < 0.5 and actual_label is True:
            return "late_signal"

    predicted_cases = _optional_float(getattr(prediction, "predicted_value", None))
    actual_cases = _optional_float(true_cases)
    if actual_cases is None:
        actual_cases = _optional_float(_feedback_value(feedback, "true_cases", "actual_value"))
    if predicted_cases is not None and actual_cases is not None and abs(actual_cases - predicted_cases) > 0:
        return "model_error"
    return "unknown"


def build_failure_lesson(
    *,
    feedback: Any,
    prediction: Any,
    failure_type: str,
) -> dict:
    actual_cases = _optional_float(_feedback_value(feedback, "true_cases", "actual_value"))
    predicted_cases = _optional_float(getattr(prediction, "predicted_value", None))
    absolute_error = None
    if actual_cases is not None and predicted_cases is not None:
        absolute_error = abs(actual_cases - predicted_cases)
    predicted_probability = _prediction_probability(prediction)
    true_label = _coalesce_bool(
        _feedback_value(feedback, "true_outbreak_label", "actual_outbreak_label"),
        None,
    )
    verdict = _feedback_verdict(feedback)
    reason = _compact_reason(_feedback_value(feedback, "reason", "failure_reason"))
    feature_snapshot_id = _feature_snapshot_id(prediction)
    weight = assign_retraining_weight(
        failure_type=failure_type,
        verdict=verdict,
        absolute_error=absolute_error,
    )
    return {
        "lesson_version": LESSON_VERSION,
        "verdict": verdict,
        "reason": reason,
        "failure_type": failure_type or "unknown",
        "absolute_error": absolute_error,
        "predicted_cases": predicted_cases,
        "true_cases": actual_cases,
        "predicted_outbreak_probability": predicted_probability,
        "true_outbreak_label": true_label,
        "model_name": getattr(prediction, "model_name", None),
        "model_version": getattr(prediction, "model_version", None),
        "horizon_days": getattr(prediction, "horizon_days", None),
        "target_date": _iso_date(getattr(prediction, "target_date", None)),
        "feature_snapshot_id": feature_snapshot_id,
        "weight_for_retraining": weight,
        "retraining_weight": weight,
        "review_required": True,
        "auto_retrain_triggered": False,
        "auto_model_replacement_triggered": False,
    }


def assign_retraining_weight(
    *,
    failure_type: str,
    verdict: str,
    absolute_error: float | None = None,
) -> float:
    normalized_verdict = _normalize_verdict(verdict)
    weight = {
        "success": 0.1,
        "partial": 1.5,
        "fail": 3.0,
        "unknown": 0.25,
    }[normalized_verdict]
    if failure_type in {"late_signal", "false_alarm"}:
        weight += 0.75
    elif failure_type in {"missing_source", "extraction_error", "label_delay"}:
        weight += 0.5
    elif failure_type == "model_error":
        weight += 0.25
    if absolute_error is not None:
        weight += min(1.0, max(0.0, float(absolute_error)) / 20.0)
    return round(max(0.0, min(5.0, weight)), 4)


def _feedback_verdict(feedback: Any) -> str:
    value = _feedback_value(feedback, "verdict", "feedback_status")
    return _normalize_verdict(value)


def _normalize_verdict(value: Any) -> str:
    normalized = str(value or "unknown").strip().lower()
    aliases = {
        "partial_success": "partial",
        "failure": "fail",
    }
    normalized = aliases.get(normalized, normalized)
    return normalized if normalized in {"success", "partial", "fail", "unknown"} else "unknown"


def _feedback_value(feedback: Any, *names: str) -> Any:
    if isinstance(feedback, dict):
        for name in names:
            if name in feedback:
                return feedback[name]
        return None
    for name in names:
        if hasattr(feedback, name):
            return getattr(feedback, name)
    return None


def _prediction_probability(prediction: Any) -> float | None:
    for payload_name in ("metrics_json", "provenance_json"):
        payload = getattr(prediction, payload_name, None)
        if isinstance(payload, dict):
            for key in ("predicted_outbreak_probability", "outbreak_probability"):
                value = _optional_float(payload.get(key))
                if value is not None:
                    return max(0.0, min(1.0, value))
            label = payload.get("predicted_outbreak_label")
            if label is not None:
                return 1.0 if bool(label) else 0.0
    predicted = _optional_float(getattr(prediction, "predicted_value", None))
    if predicted is None:
        return None
    return max(0.0, min(1.0, predicted / 20.0))


def _feature_snapshot_id(prediction: Any) -> str | None:
    payload = getattr(prediction, "provenance_json", None)
    if not isinstance(payload, dict):
        return None
    for key in ("feature_snapshot_id", "feature_record_id", "feature_row_id"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coalesce_bool(*values: Any) -> bool | None:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y"}:
                return True
            if normalized in {"false", "0", "no", "n"}:
                return False
        return bool(value)
    return None


def _compact_reason(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:80]


def _iso_date(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
