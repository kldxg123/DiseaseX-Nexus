from __future__ import annotations

import json

from model_benchmark_agent.feature_ablation import run_feature_ablation
from model_benchmark_agent.feature_selection import rank_feature_groups
from model_benchmark_agent.minimum_feature_recommender import recommend_minimum_features
from model_benchmark_agent.types import BenchmarkRunConfig


FORBIDDEN_TERMS = [
    "password",
    "token",
    "secret",
    "ssh_key_path",
    "raw_text",
    "html",
    "metadata_json",
    "feature_json",
    "provenance_json",
    "lesson_json",
]


def test_m2_m2_outputs_do_not_expose_sensitive_or_raw_payloads():
    ablation = run_feature_ablation(
        synthetic_smoke_if_needed=True,
        config=BenchmarkRunConfig(diseases=["influenza"], horizons_days=[7], train_window_weeks=4, rolling_folds=2),
    )
    ranking = rank_feature_groups(ablation)
    recommendation = recommend_minimum_features(ranking)
    text = json.dumps(
        {"ablation": ablation, "ranking": ranking, "recommendation": recommendation},
        ensure_ascii=False,
    ).lower()
    for term in FORBIDDEN_TERMS:
        assert term not in text
    assert "guaranteed accuracy" not in text
