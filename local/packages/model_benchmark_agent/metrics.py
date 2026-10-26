from __future__ import annotations

from typing import Any


def extract_metric(metrics: dict[str, Any], name: str) -> float | None:
    normalized = (name or "").strip().lower()
    regression = metrics.get("regression", {}) if isinstance(metrics, dict) else {}
    classification = metrics.get("classification", {}) if isinstance(metrics, dict) else {}
    aliases = {
        "mae": ("regression", "mae"),
        "rmse": ("regression", "rmse"),
        "smape": ("regression", "smape"),
        "mape": ("regression", "mape"),
        "interval_coverage": ("regression", "interval_coverage"),
        "f1": ("classification", "f1_score"),
        "f1_score": ("classification", "f1_score"),
        "auroc": ("classification", "auroc"),
        "pr_auc": ("classification", "pr_auc"),
        "brier": ("classification", "brier_score"),
        "brier_score": ("classification", "brier_score"),
    }
    section_name, metric_name = aliases.get(normalized, ("regression", normalized))
    section = regression if section_name == "regression" else classification
    value = section.get(metric_name)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def metric_score(metrics: dict[str, Any]) -> float:
    smape = extract_metric(metrics, "smape")
    mae = extract_metric(metrics, "mae")
    f1 = extract_metric(metrics, "f1")
    regression_score = 0.0 if smape is None else 1.0 / (1.0 + max(0.0, smape))
    mae_score = 0.0 if mae is None else 1.0 / (1.0 + max(0.0, mae) / 10.0)
    classification_score = max(0.0, min(1.0, f1)) if f1 is not None else 0.0
    return round((0.5 * regression_score) + (0.3 * mae_score) + (0.2 * classification_score), 6)


__all__ = ["extract_metric", "metric_score"]
