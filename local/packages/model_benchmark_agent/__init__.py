"""Milestone 2 M1 evidence-to-backtest benchmark harness."""

from model_benchmark_agent.backtest_runner import (
    generate_synthetic_feature_records,
    load_fixture_records,
    run_benchmark_matrix,
    run_benchmark_task,
)
from model_benchmark_agent.benchmark_matrix import build_benchmark_matrix, load_matrix
from model_benchmark_agent.feature_groups import (
    build_feature_availability_report,
    get_feature_group_registry,
    get_feature_group_sets,
)
from model_benchmark_agent.feature_ablation import evaluate_feature_subset, run_feature_ablation
from model_benchmark_agent.feature_selection import rank_feature_groups
from model_benchmark_agent.interpretable_models import (
    FeatureRuleBaseline,
    LagRollingLinearBaseline,
    PositivityAdjustedBaseline,
    WeightedSignalBaseline,
    get_interpretable_model,
)
from model_benchmark_agent.minimum_feature_recommender import recommend_minimum_features
from model_benchmark_agent.model_registry import (
    get_benchmark_model_specs,
    get_m2_m2_model_specs,
    get_model_registry,
    get_model_spec,
)
from model_benchmark_agent.ranking import rank_benchmark_results
from model_benchmark_agent.stability import analyze_stability
from model_benchmark_agent.types import (
    BenchmarkReport,
    BenchmarkResult,
    BenchmarkRunConfig,
    BenchmarkTask,
    FeatureGroupSpec,
    ModelFeatureRanking,
    ModelSpec,
)

__version__ = "0.1.0"

__all__ = [
    "BenchmarkReport",
    "BenchmarkResult",
    "BenchmarkRunConfig",
    "BenchmarkTask",
    "FeatureGroupSpec",
    "ModelFeatureRanking",
    "ModelSpec",
    "build_benchmark_matrix",
    "build_feature_availability_report",
    "evaluate_feature_subset",
    "FeatureRuleBaseline",
    "generate_synthetic_feature_records",
    "get_benchmark_model_specs",
    "get_feature_group_registry",
    "get_feature_group_sets",
    "get_interpretable_model",
    "get_m2_m2_model_specs",
    "get_model_registry",
    "get_model_spec",
    "LagRollingLinearBaseline",
    "load_fixture_records",
    "load_matrix",
    "PositivityAdjustedBaseline",
    "rank_feature_groups",
    "rank_benchmark_results",
    "recommend_minimum_features",
    "run_feature_ablation",
    "run_benchmark_matrix",
    "run_benchmark_task",
    "analyze_stability",
    "WeightedSignalBaseline",
]
