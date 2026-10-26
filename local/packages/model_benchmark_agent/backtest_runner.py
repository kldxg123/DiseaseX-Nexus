from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from predictor_agent.backtest import run_backtest
from predictor_agent.datasets import FeatureRecord, sort_records_temporally

from model_benchmark_agent.benchmark_matrix import load_matrix
from model_benchmark_agent.feature_groups import filter_feature_json
from model_benchmark_agent.model_registry import get_model_spec
from model_benchmark_agent.types import BenchmarkResult, BenchmarkTask

FIXTURE_DIR = Path("tests/fixtures/model_benchmark")


def run_benchmark_matrix(
    matrix_path: str | Path,
    *,
    synthetic_smoke_if_needed: bool = False,
    max_tasks: int | None = None,
) -> dict[str, Any]:
    tasks = load_matrix(matrix_path)
    if max_tasks is not None:
        tasks = tasks[:max_tasks]
    results = [
        run_benchmark_task(task, synthetic_smoke_if_needed=synthetic_smoke_if_needed).to_dict()
        for task in tasks
    ]
    return {
        "schema_version": "m2_m1_backtest_results_v1",
        "source_matrix": str(matrix_path),
        "synthetic_smoke_if_needed": bool(synthetic_smoke_if_needed),
        "results": results,
        "limitations": [
            "Backtests use local fixtures or M1-compatible synthetic data only.",
            "Proxy models are explicitly marked and do not train optional external estimators.",
            "No production predictor is changed.",
        ],
    }


def run_benchmark_task(
    task: BenchmarkTask,
    *,
    synthetic_smoke_if_needed: bool = False,
    records: list[FeatureRecord] | None = None,
) -> BenchmarkResult:
    model_spec = get_model_spec(task.model_name)
    warnings = list(task.warnings) + list(model_spec.warnings)
    if model_spec.execution_status in {"skipped_optional_dependency", "deferred_high_risk"}:
        warnings.append(model_spec.skip_reason or "Model is skipped by registry policy.")
        return _result(task, model_spec.benchmark_strategy, "skipped", {}, 0, 0, warnings)

    source_records = records or load_fixture_records(task.disease)
    if not source_records and synthetic_smoke_if_needed:
        source_records = generate_synthetic_feature_records(
            disease=task.disease,
            country=task.country,
            horizons_days=[task.horizon_days],
            weeks=max(task.train_window_weeks + task.rolling_folds + 2, 18),
        )
        warnings.append("synthetic_smoke_only")

    masked = [
        mask_record_features(record, task.feature_groups)
        for record in source_records
        if record.country == task.country
        and record.disease == task.disease
        and record.horizon_days == task.horizon_days
    ]
    masked = sort_records_temporally(masked)
    min_required = task.train_window_weeks + task.test_window_weeks
    if len(masked) < min_required:
        if synthetic_smoke_if_needed:
            masked = generate_synthetic_feature_records(
                disease=task.disease,
                country=task.country,
                horizons_days=[task.horizon_days],
                weeks=max(min_required + task.rolling_folds + 2, 18),
                feature_groups=task.feature_groups,
            )
            warnings.append("synthetic_smoke_only")
        else:
            warnings.append("insufficient_data")
            return _result(task, model_spec.benchmark_strategy, "insufficient_data", {}, len(masked), 0, warnings)

    try:
        raw = run_backtest(
            masked,
            model_name=model_spec.benchmark_strategy,
            horizon_days=task.horizon_days,
            min_train_size=task.train_window_weeks,
            test_window=task.test_window_weeks,
        )
    except (ValueError, TypeError) as exc:
        warnings.append(f"backtest_failed: {type(exc).__name__}")
        return _result(task, model_spec.benchmark_strategy, "failed", {}, len(masked), 0, warnings)

    predictions = raw.get("predictions") or []
    if not predictions:
        warnings.extend(raw.get("warnings") or ["insufficient_data"])
        return _result(task, model_spec.benchmark_strategy, "insufficient_data", raw.get("metrics") or {}, len(masked), 0, warnings)
    warnings.extend(raw.get("warnings") or [])
    status = "executed" if model_spec.execution_status == "executable" else model_spec.execution_status
    return _result(
        task,
        model_spec.benchmark_strategy,
        status,
        raw.get("metrics") or {},
        training_rows=min(task.train_window_weeks, len(masked)),
        test_rows=len(predictions),
        warnings=warnings,
    )


def load_fixture_records(disease: str | None = None) -> list[FeatureRecord]:
    records: list[FeatureRecord] = []
    paths = sorted(FIXTURE_DIR.glob("m1_compatible_*_features.json"))
    for path in paths:
        if disease and disease.lower() not in path.name.lower():
            continue
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            continue
        rows = payload.get("records", payload if isinstance(payload, list) else [])
        records.extend(record_from_mapping(row) for row in rows)
    return sort_records_temporally(records)


def record_from_mapping(row: dict[str, Any]) -> FeatureRecord:
    return FeatureRecord(
        id=str(row.get("id", "")),
        country=str(row.get("country", "United States")),
        admin1=row.get("admin1"),
        disease=str(row.get("disease", "")),
        target_date=date.fromisoformat(str(row.get("target_date"))),
        iso_week=str(row.get("iso_week")),
        horizon_days=int(row.get("horizon_days", 7)),
        feature_json=dict(row.get("feature_json") or {}),
        target_value=float(row.get("target_value", 0.0)),
        target_outbreak_label=bool(row.get("target_outbreak_label", False)),
        split_type=str(row.get("split_type", "synthetic")),
    )


def mask_record_features(record: FeatureRecord, feature_groups: list[str]) -> FeatureRecord:
    return replace(record, feature_json=filter_feature_json(record.feature_json, feature_groups))


def generate_synthetic_feature_records(
    *,
    disease: str,
    country: str,
    horizons_days: list[int],
    weeks: int = 24,
    feature_groups: list[str] | None = None,
) -> list[FeatureRecord]:
    start = date(2025, 1, 6)
    base = 18.0 if disease == "influenza" else 7.0
    records: list[FeatureRecord] = []
    history: list[float] = []
    for horizon in horizons_days:
        history.clear()
        for index in range(weeks):
            target_date = start + timedelta(days=7 * index)
            seasonal = 4.0 * math.sin((index / 52.0) * math.tau)
            trend = index * (0.25 if disease == "influenza" else 0.08)
            cases = max(0.0, base + seasonal + trend + (index % 3))
            lag_1 = history[-1] if len(history) >= 1 else cases
            lag_2 = history[-2] if len(history) >= 2 else lag_1
            lag_4 = history[-4] if len(history) >= 4 else lag_2
            rolling = sum((history + [cases])[-4:]) / min(4, len(history) + 1)
            positivity = min(0.45, 0.05 + cases / 250.0)
            feature_json = {
                "current_week_cases": cases,
                "lag_1w_cases": lag_1,
                "lag_2w_cases": lag_2,
                "lag_4w_cases": lag_4,
                "rolling_4w_mean_cases": rolling,
                "rolling_4w_sum_cases": rolling * 4,
                "rolling_8w_mean_cases": rolling,
                "iso_week_number": int(target_date.strftime("%V")),
                "month": target_date.month,
                "season_sin": math.sin((index / 52.0) * math.tau),
                "season_cos": math.cos((index / 52.0) * math.tau),
                "tests_performed": 100 + int(cases * 4),
                "testing_volume": 100 + int(cases * 4),
                "rolling_4w_tests": 100 + int(rolling * 4),
                "positivity_rate": positivity,
                "rolling_4w_positivity": positivity,
                "source_count": 2 + (index % 4),
                "official_source_count": 1,
                "news_source_count": index % 3,
                "quality_score": 0.85,
                "source_reliability_score": 0.9,
                "missing_value_rate": 0.0,
            }
            if feature_groups:
                feature_json = filter_feature_json(feature_json, feature_groups)
            records.append(
                FeatureRecord(
                    id=f"synthetic-{disease}-{horizon}-{index}",
                    country=country,
                    admin1=None,
                    disease=disease,
                    target_date=target_date,
                    iso_week=f"{target_date.isocalendar().year}-W{target_date.isocalendar().week:02d}",
                    horizon_days=horizon,
                    feature_json=feature_json,
                    target_value=round(cases, 3),
                    target_outbreak_label=cases >= (14 if disease == "influenza" else 8),
                    split_type="synthetic_smoke",
                )
            )
            history.append(cases)
    return sort_records_temporally(records)


def _result(
    task: BenchmarkTask,
    strategy: str,
    status: str,
    metrics: dict[str, Any],
    training_rows: int,
    test_rows: int,
    warnings: list[str],
) -> BenchmarkResult:
    return BenchmarkResult(
        task_id=task.task_id,
        disease=task.disease,
        country=task.country,
        prediction_target=task.prediction_target,
        horizon_days=task.horizon_days,
        model_family=task.model_family,
        model_name=task.model_name,
        benchmark_strategy=strategy,
        feature_groups=list(task.feature_groups),
        status=status,
        metrics=metrics,
        training_rows=int(training_rows),
        test_rows=int(test_rows),
        rolling_folds=int(test_rows),
        warnings=sorted(set(warnings)),
        evidence_source=task.evidence_source,
        requires_human_review=task.requires_human_review,
    )


__all__ = [
    "generate_synthetic_feature_records",
    "load_fixture_records",
    "mask_record_features",
    "record_from_mapping",
    "run_benchmark_matrix",
    "run_benchmark_task",
]
