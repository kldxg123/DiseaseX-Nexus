from __future__ import annotations

from pathlib import Path

from model_benchmark_agent.feature_ablation import run_feature_ablation
from model_benchmark_agent.feature_selection import rank_feature_groups
from model_benchmark_agent.m2_m2_report import (
    write_m2_m2_ablation_reports,
    write_m2_m2_minimum_reports,
    write_m2_m2_selection_reports,
)
from model_benchmark_agent.minimum_feature_recommender import recommend_minimum_features
from model_benchmark_agent.types import BenchmarkRunConfig


def test_m2_m2_report_files_are_created(tmp_path):
    ablation = run_feature_ablation(
        synthetic_smoke_if_needed=True,
        config=BenchmarkRunConfig(diseases=["influenza"], horizons_days=[7], train_window_weeks=4, rolling_folds=2),
    )
    ablation_paths = write_m2_m2_ablation_reports(ablation, tmp_path)
    ranking = rank_feature_groups(ablation)
    ranking_paths = write_m2_m2_selection_reports(ranking, tmp_path)
    recommendation = recommend_minimum_features(ranking)
    recommendation_paths = write_m2_m2_minimum_reports(recommendation, tmp_path)
    for path in [*ablation_paths.values(), *ranking_paths.values(), *recommendation_paths.values()]:
        assert Path(path).exists()
    assert "feature_json" not in Path(ablation_paths["ablation_json"]).read_text(encoding="utf-8")
