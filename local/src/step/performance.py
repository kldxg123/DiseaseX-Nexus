from __future__ import annotations

from src.eval.metrics import classification_metrics
from src.step.forgetting_guard import compute_cfi


def summarize_performance(y_true: list[str], y_pred: list[str], history_scores: dict | None = None) -> dict:
    metrics = classification_metrics(y_true, y_pred)
    metrics["cfi"] = compute_cfi(history_scores or {})
    return metrics
