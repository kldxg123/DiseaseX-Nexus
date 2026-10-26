from __future__ import annotations

from model_benchmark_agent.feature_ablation import run_feature_ablation
from model_benchmark_agent.stability import analyze_stability
from model_benchmark_agent.types import BenchmarkRunConfig


def test_stability_analysis_returns_safe_summary():
    ablation = run_feature_ablation(
        synthetic_smoke_if_needed=True,
        config=BenchmarkRunConfig(diseases=["influenza"], horizons_days=[7], train_window_weeks=4, rolling_folds=2),
    )
    summary = analyze_stability(ablation)
    assert summary["schema_version"] == "m2_m2_stability_analysis_v1"
    assert "stable_recommendations" in summary
    assert "rank_stability_across_horizons" in summary
    assert summary["requires_human_review"] is True
    assert "guarantee" not in str(summary).lower()
