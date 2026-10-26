from __future__ import annotations

from model_benchmark_agent.feature_ablation import run_feature_ablation
from model_benchmark_agent.feature_selection import rank_feature_groups
from model_benchmark_agent.minimum_feature_recommender import recommend_minimum_features
from model_benchmark_agent.types import BenchmarkRunConfig


def test_recommend_minimum_features_contains_disease_specific_sets():
    ablation = run_feature_ablation(
        synthetic_smoke_if_needed=True,
        config=BenchmarkRunConfig(diseases=["influenza"], horizons_days=[7], train_window_weeks=4, rolling_folds=2),
    )
    ranking = rank_feature_groups(ablation)
    recommendation = recommend_minimum_features(ranking)
    diseases = {row["disease"] for row in recommendation["recommendations"]}
    assert {"influenza", "norovirus"} <= diseases
    for row in recommendation["recommendations"]:
        assert row["minimal_safe_feature_set"] == ["epidemiological_lag", "rolling_statistics"]
        assert row["requires_human_review"] is True
    assert recommendation["global_not_yet_ready_feature_groups"] == ["mobility", "weather"]
