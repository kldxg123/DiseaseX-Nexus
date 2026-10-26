from __future__ import annotations

from predictor_agent.model_governance import (
    ModelEvaluationSummary,
    build_model_version,
    compare_model_metrics,
    extract_metric,
)


def _summary(version: str, mae: float, *, warning: str | None = None, false_alarm: float = 0.1):
    return ModelEvaluationSummary(
        model_version=version,
        model_name="feature_rule",
        metrics={
            "regression": {"mae": mae},
            "classification": {"false_alarm_rate": false_alarm, "missed_outbreak_rate": 0.1},
        },
        warnings=[warning] if warning else [],
    )


def test_extract_metric_and_build_model_version() -> None:
    assert extract_metric({"regression": {"mae": 1.2}}, "regression.mae") == 1.2
    assert extract_metric({"regression": {}}, "regression.mae") is None
    assert build_model_version("feature rule", 28, "v2") == "baseline:feature_rule:h28:v2"


def test_compare_model_metrics_promotes_clear_improvement() -> None:
    decision = compare_model_metrics(
        _summary("champion-v1", 10.0),
        _summary("challenger-v2", 8.0),
        min_relative_improvement=0.05,
    )

    assert decision.promote is True
    assert decision.reason == "challenger_meets_offline_promotion_checks"
    assert decision.checks["relative_improvement"] == 0.2


def test_compare_model_metrics_blocks_missing_warning_insufficient_and_regression() -> None:
    champion = _summary("champion-v1", 10.0)

    assert compare_model_metrics(champion, _summary("challenger-v2", 9.9)).promote is False
    assert compare_model_metrics(champion, _summary("challenger-v2", 8.0, warning="data too sparse")).promote is False
    assert (
        compare_model_metrics(
            champion,
            ModelEvaluationSummary("challenger-v2", "feature_rule", {"regression": {}}, []),
        ).reason
        == "missing_primary_metric"
    )
    assert (
        compare_model_metrics(
            champion,
            _summary("challenger-v2", 7.0, false_alarm=0.4),
            max_false_alarm_regression=0.05,
        ).reason
        == "false_alarm_regression_exceeds_threshold"
    )
