from __future__ import annotations

from collections import Counter
from math import isnan
from typing import Any


LABEL_UP = "\u4e0a\u5347"
LABEL_DOWN = "\u4e0b\u964d"
LABEL_STABLE = "\u7a33\u5b9a"
LABEL_ORDER = [LABEL_UP, LABEL_DOWN, LABEL_STABLE]


def confusion_matrix(y_true: list[str], y_pred: list[str], labels: list[str] | None = None) -> dict[str, dict[str, int]]:
    ordered = labels or LABEL_ORDER
    matrix = {truth: {pred: 0 for pred in ordered} for truth in ordered}
    for truth, pred in zip(y_true, y_pred):
        if truth in matrix and pred in matrix[truth]:
            matrix[truth][pred] += 1
    return matrix


def classification_metrics(y_true: list[str], y_pred: list[str], labels: list[str] | None = None) -> dict[str, Any]:
    ordered = labels or LABEL_ORDER
    total = len(y_true)
    correct = sum(1 for truth, pred in zip(y_true, y_pred) if truth == pred)
    per_class: dict[str, dict[str, Any]] = {}
    macro_f1_values: list[float] = []
    recall_values: list[float] = []
    warnings: list[str] = []
    support = Counter(y_true)
    predicted = Counter(y_pred)
    for label in ordered:
        tp = sum(1 for truth, pred in zip(y_true, y_pred) if truth == label and pred == label)
        fp = sum(1 for truth, pred in zip(y_true, y_pred) if truth != label and pred == label)
        fn = sum(1 for truth, pred in zip(y_true, y_pred) if truth == label and pred != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        if support.get(label, 0) == 0:
            warnings.append(f"missing_true_class:{label}")
        if predicted.get(label, 0) == 0:
            warnings.append(f"missing_predicted_class:{label}")
        per_class[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support.get(label, 0),
            "predicted": predicted.get(label, 0),
        }
        macro_f1_values.append(f1)
        recall_values.append(recall)
    return {
        "accuracy": correct / total if total else 0.0,
        "macro_f1": sum(macro_f1_values) / len(ordered) if ordered else 0.0,
        "balanced_accuracy": sum(recall_values) / len(ordered) if ordered else 0.0,
        "per_class": per_class,
        "confusion_matrix": confusion_matrix(y_true, y_pred, ordered),
        "support": {label: support.get(label, 0) for label in ordered},
        "warnings": warnings,
    }


def brier_score(
    y_true: list[str],
    probabilities: list[dict[str, float]] | None,
    labels: list[str] | None = None,
) -> float | dict[str, str]:
    ordered = labels or LABEL_ORDER
    if not y_true:
        return {"status": "not_available", "reason": "empty_y_true"}
    if probabilities is None or len(probabilities) != len(y_true):
        return {"status": "not_available", "reason": "probability_length_mismatch"}
    total = 0.0
    for truth, probs in zip(y_true, probabilities):
        if not all(label in probs for label in ordered):
            return {"status": "not_available", "reason": "incomplete_probability_classes"}
        for label in ordered:
            value = float(probs.get(label, 0.0))
            if isnan(value):
                return {"status": "not_available", "reason": "nan_probability"}
            target = 1.0 if truth == label else 0.0
            total += (value - target) ** 2
    return total / len(y_true)


def expected_calibration_error(
    y_true: list[str],
    probabilities: list[dict[str, float]] | None,
    labels: list[str] | None = None,
    bins: int = 10,
) -> float | dict[str, str]:
    ordered = labels or LABEL_ORDER
    if not y_true:
        return {"status": "not_available", "reason": "empty_y_true"}
    if probabilities is None or len(probabilities) != len(y_true):
        return {"status": "not_available", "reason": "probability_length_mismatch"}
    buckets: list[list[tuple[bool, float]]] = [[] for _ in range(bins)]
    for truth, probs in zip(y_true, probabilities):
        if not all(label in probs for label in ordered):
            return {"status": "not_available", "reason": "incomplete_probability_classes"}
        pred, conf = max(((label, float(probs[label])) for label in ordered), key=lambda item: item[1])
        if isnan(conf):
            return {"status": "not_available", "reason": "nan_probability"}
        idx = min(bins - 1, max(0, int(conf * bins)))
        buckets[idx].append((truth == pred, conf))
    error = 0.0
    total = len(y_true)
    for bucket in buckets:
        if not bucket:
            continue
        accuracy = sum(1 for ok, _ in bucket if ok) / len(bucket)
        confidence = sum(conf for _, conf in bucket) / len(bucket)
        error += (len(bucket) / total) * abs(accuracy - confidence)
    return error
