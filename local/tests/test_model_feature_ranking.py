from model_benchmark_agent.ranking import rank_benchmark_results, score_result
from model_benchmark_agent.types import result_from_dict


def test_ranking_does_not_put_skipped_model_first():
    payload = {
        "results": [
            {
                "task_id": "skip",
                "disease": "influenza",
                "country": "United States",
                "prediction_target": "outbreak_or_case_trend",
                "horizon_days": 7,
                "model_family": "statistical_time_series",
                "model_name": "arima_or_sarima",
                "benchmark_strategy": "skip",
                "feature_groups": ["epidemiological_lag"],
                "status": "skipped",
                "metrics": {},
                "training_rows": 0,
                "test_rows": 0,
                "rolling_folds": 0,
                "warnings": ["skipped_optional_dependency"],
                "evidence_source": "fixture",
                "requires_human_review": True,
            },
            {
                "task_id": "ok",
                "disease": "influenza",
                "country": "United States",
                "prediction_target": "outbreak_or_case_trend",
                "horizon_days": 7,
                "model_family": "baseline",
                "model_name": "moving_average",
                "benchmark_strategy": "moving_average",
                "feature_groups": ["epidemiological_lag"],
                "status": "executed",
                "metrics": {"regression": {"mae": 1, "rmse": 1, "smape": 0.1}, "classification": {"f1_score": 0.8}},
                "training_rows": 8,
                "test_rows": 3,
                "rolling_folds": 3,
                "warnings": [],
                "evidence_source": "fixture",
                "requires_human_review": True,
            },
        ]
    }
    ranking = rank_benchmark_results(payload)
    assert ranking["top_5"][0]["model_name"] == "moving_average"
    assert ranking["top_5"][0]["total_score"] > 0


def test_score_result_zero_for_insufficient_data():
    result = result_from_dict(
        {
            "task_id": "small",
            "disease": "influenza",
            "country": "United States",
            "prediction_target": "outbreak_or_case_trend",
            "horizon_days": 7,
            "model_family": "baseline",
            "model_name": "moving_average",
            "benchmark_strategy": "moving_average",
            "feature_groups": ["epidemiological_lag"],
            "status": "insufficient_data",
            "metrics": {},
            "training_rows": 1,
            "test_rows": 0,
            "rolling_folds": 0,
            "warnings": [],
            "evidence_source": "fixture",
        }
    )
    assert score_result(result)["total_score"] == 0.0
