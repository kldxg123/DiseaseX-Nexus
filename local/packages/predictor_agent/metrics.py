from __future__ import annotations

import math
from collections.abc import Iterable


def _float_list(values: Iterable[float]) -> list[float]:
    return [float(value) for value in values]


def _safe_probability(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def mean_absolute_error(actual: list[float], predicted: list[float]) -> float:
    if not actual:
        return 0.0
    return sum(abs(a - p) for a, p in zip(actual, predicted)) / len(actual)


def root_mean_squared_error(actual: list[float], predicted: list[float]) -> float:
    if not actual:
        return 0.0
    return math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, predicted)) / len(actual))


def mean_absolute_percentage_error(actual: list[float], predicted: list[float]) -> float:
    pairs = [(a, p) for a, p in zip(actual, predicted) if a != 0]
    if not pairs:
        return 0.0
    return sum(abs((a - p) / a) for a, p in pairs) / len(pairs)


def interval_coverage(actual: list[float], lower: list[float], upper: list[float]) -> float:
    if not actual:
        return 0.0
    covered = sum(1 for value, lo, hi in zip(actual, lower, upper) if lo <= value <= hi)
    return covered / len(actual)


def smape(actual: list[float], predicted: list[float]) -> float:
    if not actual:
        return 0.0
    total = 0.0
    for actual_value, predicted_value in zip(actual, predicted):
        denominator = abs(actual_value) + abs(predicted_value)
        if denominator == 0:
            continue
        total += 2 * abs(actual_value - predicted_value) / denominator
    return total / len(actual)


def brier_score(labels: list[bool | int | float], probabilities: list[float]) -> float:
    if not labels:
        return 0.0
    return sum((float(bool(label)) - _safe_probability(probability)) ** 2 for label, probability in zip(labels, probabilities)) / len(labels)


def f1_score(
    labels: list[bool | int | float],
    probabilities: list[float],
    *,
    threshold: float = 0.5,
) -> float:
    if not labels:
        return 0.0
    true_positive = false_positive = false_negative = 0
    for label, probability in zip(labels, probabilities):
        actual = bool(label)
        predicted = _safe_probability(probability) >= threshold
        if predicted and actual:
            true_positive += 1
        elif predicted and not actual:
            false_positive += 1
        elif not predicted and actual:
            false_negative += 1
    denominator = 2 * true_positive + false_positive + false_negative
    return 0.0 if denominator == 0 else (2 * true_positive) / denominator


def auroc(labels: list[bool | int | float], probabilities: list[float]) -> float:
    positives = [_safe_probability(score) for label, score in zip(labels, probabilities) if bool(label)]
    negatives = [_safe_probability(score) for label, score in zip(labels, probabilities) if not bool(label)]
    if not positives or not negatives:
        return 0.0
    wins = 0.0
    for positive_score in positives:
        for negative_score in negatives:
            if positive_score > negative_score:
                wins += 1.0
            elif positive_score == negative_score:
                wins += 0.5
    return wins / (len(positives) * len(negatives))


def pr_auc(labels: list[bool | int | float], probabilities: list[float]) -> float:
    pairs = sorted(
        [(bool(label), _safe_probability(probability)) for label, probability in zip(labels, probabilities)],
        key=lambda item: item[1],
        reverse=True,
    )
    positive_count = sum(1 for label, _probability in pairs if label)
    if positive_count == 0 or positive_count == len(pairs):
        return 0.0
    true_positive = 0
    false_positive = 0
    previous_recall = 0.0
    area = 0.0
    for label, _probability in pairs:
        if label:
            true_positive += 1
        else:
            false_positive += 1
        recall = true_positive / positive_count
        precision = true_positive / (true_positive + false_positive)
        area += precision * (recall - previous_recall)
        previous_recall = recall
    return area


def summarize_regression_metrics(
    actual: list[float],
    predicted: list[float],
    lower: list[float] | None = None,
    upper: list[float] | None = None,
) -> dict:
    actual_values = _float_list(actual)
    predicted_values = _float_list(predicted)
    lower_values = _float_list(lower) if lower is not None else []
    upper_values = _float_list(upper) if upper is not None else []
    coverage = (
        interval_coverage(actual_values, lower_values, upper_values)
        if lower is not None and upper is not None
        else 0.0
    )
    return {
        "n": len(actual_values),
        "mae": mean_absolute_error(actual_values, predicted_values),
        "rmse": root_mean_squared_error(actual_values, predicted_values),
        "mape": mean_absolute_percentage_error(actual_values, predicted_values),
        "smape": smape(actual_values, predicted_values),
        "interval_coverage": coverage,
    }


def summarize_classification_metrics(
    labels: list[bool | int | float],
    probabilities: list[float],
    *,
    threshold: float = 0.5,
) -> dict:
    return {
        "n": len(labels),
        "brier_score": brier_score(labels, probabilities),
        "f1_score": f1_score(labels, probabilities, threshold=threshold),
        "auroc": auroc(labels, probabilities),
        "pr_auc": pr_auc(labels, probabilities),
        "threshold": float(threshold),
    }


def f1_score_binary(y_true: list[bool], y_pred: list[bool]) -> float:
    if not y_true:
        return 0.0
    probabilities = [1.0 if value else 0.0 for value in y_pred]
    return f1_score(y_true, probabilities, threshold=0.5)


def auroc_score(y_true: list[bool], y_prob: list[float]) -> float | None:
    if not y_true or len({bool(value) for value in y_true}) < 2:
        return None
    return auroc(y_true, y_prob)


def average_precision_score(y_true: list[bool], y_prob: list[float]) -> float | None:
    if not y_true or len({bool(value) for value in y_true}) < 2:
        return None
    return pr_auc(y_true, y_prob)


def classification_metrics(
    y_true: list[bool],
    y_prob: list[float],
    threshold: float = 0.5,
) -> dict:
    probabilities = [_safe_probability(value) for value in y_prob]
    predicted = [probability >= threshold for probability in probabilities]
    return {
        "n": len(y_true),
        "brier_score": brier_score(y_true, probabilities),
        "f1_score": f1_score_binary(y_true, predicted),
        "auroc": auroc_score(y_true, probabilities),
        "average_precision": average_precision_score(y_true, probabilities),
        "pr_auc": average_precision_score(y_true, probabilities),
        "threshold": float(threshold),
    }


def regression_metrics(actual_or_records: list, predicted: list[float] | None = None) -> dict:
    if predicted is None:
        records = actual_or_records
        actual = [float(record.actual_value) for record in records]
        predicted_values = [float(record.predicted_value) for record in records]
        lower = [float(record.lower_bound) for record in records]
        upper = [float(record.upper_bound) for record in records]
    else:
        actual = [float(value) for value in actual_or_records]
        predicted_values = [float(value) for value in predicted]
        lower = None
        upper = None
    summary = summarize_regression_metrics(actual, predicted_values, lower, upper)
    return {
        "mae": summary["mae"],
        "rmse": summary["rmse"],
        "mape": summary["mape"],
        "smape": summary["smape"],
        "interval_coverage": summary["interval_coverage"],
        "n": summary["n"],
    }
