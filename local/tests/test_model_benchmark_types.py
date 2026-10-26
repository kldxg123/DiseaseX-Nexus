from model_benchmark_agent.types import (
    BenchmarkResult,
    BenchmarkRunConfig,
    BenchmarkTask,
    FeatureGroupSpec,
    ModelFeatureRanking,
    ModelSpec,
)


def test_model_benchmark_dataclasses_are_serializable():
    feature = FeatureGroupSpec("epidemiological_lag", "lags", ["lag_1w_cases"], "available")
    model = ModelSpec(
        model_name="moving_average",
        model_family="baseline",
        benchmark_strategy="moving_average",
        execution_status="executable",
        data_requirement="low",
        interpretability="high",
        compute_risk="low",
        leakage_risk="low",
        recommended_for_m2_m1=True,
    )
    task = BenchmarkTask(
        task_id="task",
        disease="influenza",
        country="United States",
        prediction_target="outbreak_or_case_trend",
        horizon_days=7,
        model_family="baseline",
        model_name="moving_average",
        feature_groups=["epidemiological_lag"],
        train_window_weeks=8,
        test_window_weeks=1,
        rolling_folds=4,
        metrics=["mae"],
        evidence_source="fixture",
    )
    result = BenchmarkResult(
        task_id="task",
        disease="influenza",
        country="United States",
        prediction_target="outbreak_or_case_trend",
        horizon_days=7,
        model_family="baseline",
        model_name="moving_average",
        benchmark_strategy="moving_average",
        feature_groups=["epidemiological_lag"],
        status="executed",
        metrics={"regression": {"mae": 1.0}},
        training_rows=8,
        test_rows=2,
        rolling_folds=2,
        warnings=[],
        evidence_source="fixture",
    )
    ranking = ModelFeatureRanking(
        rank=1,
        model_name="moving_average",
        model_family="baseline",
        feature_groups=["epidemiological_lag"],
        metric_score=0.9,
        evidence_score=0.8,
        data_fit_score=0.7,
        interpretability_score=1.0,
        implementation_feasibility_score=1.0,
        safety_score=1.0,
        total_score=0.9,
        recommendation_reason="safe baseline",
        limitations=[],
        status="executed",
    )
    config = BenchmarkRunConfig()
    assert feature.to_dict()["name"] == "epidemiological_lag"
    assert model.to_dict()["recommended_for_m2_m1"] is True
    assert task.to_dict()["horizon_days"] == 7
    assert result.to_dict()["status"] == "executed"
    assert ranking.to_dict()["rank"] == 1
    assert "influenza" in config.diseases
