from pathlib import Path

from model_benchmark_agent.backtest_runner import load_fixture_records
from model_benchmark_agent.benchmark_matrix import build_benchmark_matrix
from model_benchmark_agent.ranking import rank_benchmark_results
from model_benchmark_agent.report import (
    write_backtest_reports,
    write_benchmark_matrix_reports,
    write_feature_availability_report,
    write_gap_and_next_action_reports,
    write_ranking_reports,
)


def test_model_benchmark_reports_are_generated(tmp_path):
    matrix = build_benchmark_matrix()
    matrix_paths = write_benchmark_matrix_reports(matrix, tmp_path)
    feature_paths = write_feature_availability_report(load_fixture_records(), tmp_path)
    results = {
        "results": [
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
            }
        ]
    }
    backtest_paths = write_backtest_reports(results, tmp_path)
    ranking = rank_benchmark_results(results)
    ranking_paths = write_ranking_reports(ranking, tmp_path)
    extra = write_gap_and_next_action_reports(ranking=ranking, output_dir=tmp_path)
    for path in [
        matrix_paths["json"],
        matrix_paths["markdown"],
        feature_paths["markdown"],
        backtest_paths["json"],
        backtest_paths["markdown"],
        ranking_paths["json"],
        ranking_paths["markdown"],
        extra["gap"],
        extra["next"],
    ]:
        assert path
        assert Path(path).exists()
