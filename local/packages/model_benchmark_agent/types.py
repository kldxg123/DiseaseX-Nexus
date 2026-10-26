from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class FeatureGroupSpec:
    name: str
    description: str
    feature_keys: list[str]
    m1_status: str
    evidence_source: str = "M2_M0"
    requires_human_review: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelSpec:
    model_name: str
    model_family: str
    benchmark_strategy: str
    execution_status: str
    data_requirement: str
    interpretability: str
    compute_risk: str
    leakage_risk: str
    recommended_for_m2_m1: bool
    recommended_for_m2_m2: bool = False
    optional_dependency_required: bool = False
    evidence_source: str = "M2_M0"
    requires_human_review: bool = True
    skip_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkTask:
    task_id: str
    disease: str
    country: str
    prediction_target: str
    horizon_days: int
    model_family: str
    model_name: str
    feature_groups: list[str]
    train_window_weeks: int
    test_window_weeks: int
    rolling_folds: int
    metrics: list[str]
    evidence_source: str
    requires_human_review: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkRunConfig:
    country: str = "United States"
    diseases: list[str] = field(default_factory=lambda: ["influenza", "norovirus"])
    prediction_target: str = "outbreak_or_case_trend"
    horizons_days: list[int] = field(default_factory=lambda: [7, 14, 28])
    train_window_weeks: int = 8
    test_window_weeks: int = 1
    rolling_folds: int = 4
    synthetic_smoke_if_needed: bool = False
    evidence_source: str = "M2_M0"
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkResult:
    task_id: str
    disease: str
    country: str
    prediction_target: str
    horizon_days: int
    model_family: str
    model_name: str
    benchmark_strategy: str
    feature_groups: list[str]
    status: str
    metrics: dict[str, Any]
    training_rows: int
    test_rows: int
    rolling_folds: int
    warnings: list[str]
    evidence_source: str
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ModelFeatureRanking:
    rank: int
    model_name: str
    model_family: str
    feature_groups: list[str]
    metric_score: float
    evidence_score: float
    data_fit_score: float
    interpretability_score: float
    implementation_feasibility_score: float
    safety_score: float
    total_score: float
    recommendation_reason: str
    limitations: list[str]
    status: str
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkReport:
    benchmark_matrix_path: str
    backtest_results_path: str
    ranking_report_path: str
    feature_availability_report_path: str
    gap_analysis_update_path: str
    next_action_plan_path: str
    executed_models: list[str]
    skipped_models: dict[str, str]
    ranking_top_5: list[dict[str, Any]]
    feature_groups_top_5: list[list[str]]
    warnings: list[str]
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def task_from_dict(data: dict[str, Any]) -> BenchmarkTask:
    return BenchmarkTask(
        task_id=str(data["task_id"]),
        disease=str(data["disease"]),
        country=str(data["country"]),
        prediction_target=str(data.get("prediction_target", "outbreak_or_case_trend")),
        horizon_days=int(data["horizon_days"]),
        model_family=str(data.get("model_family", "")),
        model_name=str(data["model_name"]),
        feature_groups=list(data.get("feature_groups") or []),
        train_window_weeks=int(data.get("train_window_weeks", 8)),
        test_window_weeks=int(data.get("test_window_weeks", 1)),
        rolling_folds=int(data.get("rolling_folds", 4)),
        metrics=list(data.get("metrics") or []),
        evidence_source=str(data.get("evidence_source", "M2_M0")),
        requires_human_review=bool(data.get("requires_human_review", True)),
        warnings=list(data.get("warnings") or []),
    )


def result_from_dict(data: dict[str, Any]) -> BenchmarkResult:
    return BenchmarkResult(
        task_id=str(data["task_id"]),
        disease=str(data["disease"]),
        country=str(data["country"]),
        prediction_target=str(data.get("prediction_target", "outbreak_or_case_trend")),
        horizon_days=int(data["horizon_days"]),
        model_family=str(data.get("model_family", "")),
        model_name=str(data["model_name"]),
        benchmark_strategy=str(data.get("benchmark_strategy", data["model_name"])),
        feature_groups=list(data.get("feature_groups") or []),
        status=str(data.get("status", "unknown")),
        metrics=dict(data.get("metrics") or {}),
        training_rows=int(data.get("training_rows", 0)),
        test_rows=int(data.get("test_rows", 0)),
        rolling_folds=int(data.get("rolling_folds", 0)),
        warnings=list(data.get("warnings") or []),
        evidence_source=str(data.get("evidence_source", "M2_M0")),
        requires_human_review=bool(data.get("requires_human_review", True)),
    )
