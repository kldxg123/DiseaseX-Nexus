from __future__ import annotations

from typing import Any

import numpy as np


def rare_event_metrics(y_true: list[int], y_score: list[float], threshold: float = 0.5) -> dict[str, Any]:
    y = np.array([int(v) for v in y_true], dtype=int)
    scores = np.array([float(v) for v in y_score], dtype=float)
    pred = (scores >= threshold).astype(int)
    tp = int(((y == 1) & (pred == 1)).sum())
    fp = int(((y == 0) & (pred == 1)).sum())
    tn = int(((y == 0) & (pred == 0)).sum())
    fn = int(((y == 1) & (pred == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    positive_count = int((y == 1).sum())
    k = max(1, positive_count)
    order = np.argsort(-scores)
    top = order[: min(k, len(order))]
    recall_at_k = float((y[top] == 1).sum() / positive_count) if positive_count else 0.0
    precision_at_k = float((y[top] == 1).sum() / len(top)) if len(top) else 0.0
    return {
        "pr_auc": _average_precision(y, scores),
        "roc_auc": _roc_auc(y, scores),
        "macro_f1": f1 / 2.0 + (2 * specificity * (tn / (tn + fn) if tn + fn else 0.0) / (specificity + (tn / (tn + fn) if tn + fn else 0.0)) if specificity + (tn / (tn + fn) if tn + fn else 0.0) else 0.0) / 2.0,
        "balanced_accuracy": (recall + specificity) / 2.0,
        "precision": precision,
        "recall": recall,
        "recall_at_k": recall_at_k,
        "precision_at_k": precision_at_k,
        "false_alarms_per_100_region_weeks": fp / len(y) * 100.0 if len(y) else 0.0,
        "confusion_matrix": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "positive_count": positive_count,
        "low_n_warning": positive_count < 30,
    }


def event_recall_and_lead_time(rows: list[dict[str, Any]], scores: list[float], threshold: float = 0.5) -> dict[str, Any]:
    event_to_positive: dict[str, list[tuple[int, float]]] = {}
    for row, score in zip(rows, scores):
        if int(float(row.get("label", 0))) != 1:
            continue
        event_id = str(row.get("outbreak_id") or row.get("matched_positive_id") or row.get("sample_id"))
        try:
            days = int(float(row.get("days_to_event", 0)))
        except Exception:
            days = 0
        event_to_positive.setdefault(event_id, []).append((days, float(score)))
    if not event_to_positive:
        return {"event_recall": 0.0, "lead_time_weeks": {"status": "not_available", "reason": "no_positive_events"}}
    detected = []
    lead_weeks = []
    for samples in event_to_positive.values():
        hits = [(days, score) for days, score in samples if score >= threshold]
        detected.append(bool(hits))
        if hits:
            lead_weeks.append(max(days for days, _ in hits) / 7.0)
    return {
        "event_recall": sum(1 for item in detected if item) / len(detected),
        "lead_time_weeks": sum(lead_weeks) / len(lead_weeks) if lead_weeks else {"status": "not_available", "reason": "no_detected_events"},
        "event_count": len(event_to_positive),
    }


def _average_precision(y: np.ndarray, scores: np.ndarray) -> float | dict[str, str]:
    if len(set(y.tolist())) < 2:
        return {"status": "not_available", "reason": "single_class"}
    order = np.argsort(-scores)
    y_sorted = y[order]
    positives = int((y_sorted == 1).sum())
    if positives == 0:
        return {"status": "not_available", "reason": "no_positive"}
    tp = 0
    precisions = []
    for idx, truth in enumerate(y_sorted, start=1):
        if truth == 1:
            tp += 1
            precisions.append(tp / idx)
    return float(sum(precisions) / positives)


def _roc_auc(y: np.ndarray, scores: np.ndarray) -> float | dict[str, str]:
    positives = scores[y == 1]
    negatives = scores[y == 0]
    if len(positives) == 0 or len(negatives) == 0:
        return {"status": "not_available", "reason": "single_class"}
    wins = 0.0
    total = len(positives) * len(negatives)
    for pos in positives:
        wins += float((pos > negatives).sum()) + 0.5 * float((pos == negatives).sum())
    return float(wins / total)
