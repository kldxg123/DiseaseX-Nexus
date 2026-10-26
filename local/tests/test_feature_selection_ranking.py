from __future__ import annotations

from model_benchmark_agent.feature_ablation import run_feature_ablation
from model_benchmark_agent.feature_selection import compute_feature_group_cost, rank_feature_groups
from model_benchmark_agent.types import BenchmarkRunConfig


def test_rank_feature_groups_keeps_weather_mobility_not_ready():
    payload = run_feature_ablation(
        synthetic_smoke_if_needed=True,
        config=BenchmarkRunConfig(diseases=["influenza"], horizons_days=[7], train_window_weeks=4, rolling_folds=2),
    )
    ranking = rank_feature_groups(payload)
    assert ranking["schema_version"] == "m2_m2_feature_selection_ranking_v1"
    assert "epidemiological_lag" in ranking["top_recommended_feature_groups"]
    assert set(ranking["not_yet_ready_feature_groups"]) == {"mobility", "weather"}
    assert "weather" not in ranking["top_recommended_feature_groups"]
    assert compute_feature_group_cost("weather") > compute_feature_group_cost("epidemiological_lag")
