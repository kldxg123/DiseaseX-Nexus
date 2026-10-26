from __future__ import annotations

import json
import statistics
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any

from predictor_agent.datasets import FeatureRecord, sort_records_temporally
from predictor_agent.metrics import summarize_classification_metrics, summarize_regression_metrics

from model_benchmark_agent.backtest_runner import (
    FIXTURE_DIR,
    generate_synthetic_feature_records,
    load_fixture_records,
    record_from_mapping,
)
from model_benchmark_agent.feature_groups import detect_feature_keys, feature_keys_for_groups, filter_feature_json
from model_benchmark_agent.interpretable_models import get_interpretable_model
from model_benchmark_agent.model_registry import get_m2_m2_model_specs
from model_benchmark_agent.types import BenchmarkRunConfig


M2_M2_FEATURE_SETS: dict[str, list[str]] = {
    "lag_only": ["epidemiological_lag"],
    "lag_rolling": ["epidemiological_lag", "rolling_statistics"],
    "lag_rolling_seasonality": ["epidemiological_lag", "rolling_statistics", "seasonality"],
    "lag_rolling_testing": ["epidemiological_lag", "rolling_statistics", "testing"],
    "lag_rolling_positivity": ["epidemiological_lag", "rolling_statistics", "positivity"],
    "lag_rolling_testing_positivity": [
        "epidemiological_lag",
        "rolling_statistics",
        "testing",
        "positivity",
    ],
    "lag_rolling_source_quality": [
        "epidemiological_lag",
        "rolling_statistics",
        "source_signal",
        "data_quality",
    ],
    "all_m1_available": [
        "epidemiological_lag",
        "rolling_statistics",
        "seasonality",
        "testing",
        "positivity",
        "source_signal",
        "data_quality",
    ],
}


def run_feature_ablation(
    *,
    m2_m1_dir: str | Path = "reports/milestone2_m1",
    output_dir: str | Path | None = None,
    synthetic_smoke_if_needed: bool = False,
    config: BenchmarkRunConfig | None = None,
) -> dict[str, Any]:
    run_config = config or BenchmarkRunConfig()
    model_specs = get_m2_m2_model_specs()
    results: list[dict[str, Any]] = []
    for disease in run_config.diseases:
        for horizon_days in run_config.horizons_days:
            records = load_feature_ablation_records(disease=disease, horizon_days=horizon_days)
            if not records and synthetic_smoke_if_needed:
                records = generate_synthetic_feature_records(
                    disease=disease,
                    country=run_config.country,
                    horizons_days=[horizon_days],
                    weeks=max(24, run_config.train_window_weeks + run_config.rolling_folds + 4),
                )
            for feature_set_name, feature_groups in M2_M2_FEATURE_SETS.items():
                for model_spec in model_specs:
                    results.append(
                        evaluate_feature_subset(
                            records,
                            disease=disease,
                            country=run_config.country,
                            horizon_days=horizon_days,
                            model_name=model_spec.model_name,
                            feature_set_name=feature_set_name,
                            feature_groups=feature_groups,
                            min_train_size=run_config.train_window_weeks,
                            rolling_folds=run_config.rolling_folds,
                        )
                    )
    payload = {
        "schema_version": "m2_m2_feature_ablation_v1",
        "source_m2_m1_dir": str(m2_m1_dir),
        "synthetic_smoke_if_needed": bool(synthetic_smoke_if_needed),
        "ablation_results": results,
        "interpretable_baseline_results": summarize_interpretable_baselines(results),
        "limitations": [
            "Offline local fixture/synthetic analysis only.",
            "No model is promoted and no production predictor is changed.",
            "Scores are decision-support signals and require human review.",
        ],
        "requires_human_review": True,
    }
    if output_dir is not None:
        from model_benchmark_agent.m2_m2_report import write_m2_m2_ablation_reports

        write_m2_m2_ablation_reports(payload, output_dir)
    return payload


def evaluate_feature_subset(
    records: list[FeatureRecord],
    *,
    disease: str,
    country: str,
    horizon_days: int,
    model_name: str,
    feature_set_name: str,
    feature_groups: list[str],
    min_train_size: int = 8,
    rolling_folds: int = 4,
) -> dict[str, Any]:
    warnings: list[str] = []
    selected = [
        record
        for record in sort_records_temporally(records)
        if record.country == country and record.disease == disease and record.horizon_days == horizon_days
    ]
    present_keys = detect_feature_keys(selected)
    missing_groups = [
        group
        for group in feature_groups
        if not any(key in present_keys for key in feature_keys_for_groups([group]))
    ]
    for group in missing_groups:
        warnings.append(f"missing_feature_group:{group}")

    if len(selected) < min_train_size + 1:
        warnings.append("insufficient_data")
        return _ablation_row(
            disease=disease,
            country=country,
            horizon_days=horizon_days,
            model_name=model_name,
            feature_set_name=feature_set_name,
            feature_groups=feature_groups,
            status="insufficient_data",
            warnings=warnings,
        )

    if len(missing_groups) == len(feature_groups):
        warnings.append("all_selected_feature_groups_missing")
        return _ablation_row(
            disease=disease,
            country=country,
            horizon_days=horizon_days,
            model_name=model_name,
            feature_set_name=feature_set_name,
            feature_groups=feature_groups,
            status="skipped_missing_features",
            warnings=warnings,
        )

    masked = [mask_record(record, feature_groups) for record in selected]
    start = max(min_train_size, len(masked) - rolling_folds)
    actual: list[float] = []
    predicted: list[float] = []
    lower: list[float] = []
    upper: list[float] = []
    labels: list[bool] = []
    probabilities: list[float] = []
    model_warnings: list[str] = []
    for index in range(start, len(masked)):
        train = masked[:index]
        test = masked[index]
        if test.target_value is None:
            continue
        try:
            model = get_interpretable_model(model_name, feature_groups=feature_groups).fit(train)
            prediction = model.predict_one(test)
        except (TypeError, ValueError, ZeroDivisionError) as exc:
            warnings.append(f"model_failed:{type(exc).__name__}")
            return _ablation_row(
                disease=disease,
                country=country,
                horizon_days=horizon_days,
                model_name=model_name,
                feature_set_name=feature_set_name,
                feature_groups=feature_groups,
                status="failed",
                warnings=warnings,
            )
        actual.append(float(test.target_value))
        predicted.append(float(prediction.prediction))
        lower.append(float(prediction.lower_bound))
        upper.append(float(prediction.upper_bound))
        labels.append(bool(test.target_outbreak_label))
        probabilities.append(float(prediction.probability_score))
        model_warnings.extend(prediction.warnings)

    if not actual:
        warnings.append("no_test_rows")
        return _ablation_row(
            disease=disease,
            country=country,
            horizon_days=horizon_days,
            model_name=model_name,
            feature_set_name=feature_set_name,
            feature_groups=feature_groups,
            status="insufficient_data",
            warnings=warnings,
        )

    regression = summarize_regression_metrics(actual, predicted, lower, upper)
    classification = summarize_classification_metrics(labels, probabilities)
    residuals = [a - p for a, p in zip(actual, predicted)]
    stability = _stability_score(residuals)
    warnings.extend(model_warnings)
    return _ablation_row(
        disease=disease,
        country=country,
        horizon_days=horizon_days,
        model_name=model_name,
        feature_set_name=feature_set_name,
        feature_groups=feature_groups,
        status="executed",
        metrics={
            "regression": _round_mapping(regression),
            "classification": _round_mapping(classification),
        },
        training_rows=max(0, start),
        test_rows=len(actual),
        rolling_folds=len(actual),
        stability_score=stability,
        missing_feature_penalty=round(min(0.6, len(missing_groups) * 0.12), 6),
        interpretability_score=interpretability_score(feature_groups),
        warnings=warnings,
    )


def load_feature_ablation_records(
    *,
    disease: str | None = None,
    horizon_days: int | None = None,
) -> list[FeatureRecord]:
    records: list[FeatureRecord] = []
    paths = sorted(FIXTURE_DIR.glob("feature_ablation_*_features.json"))
    if not paths:
        records = load_fixture_records(disease)
    for path in paths:
        if disease and disease.lower() not in path.name.lower():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = payload.get("records", payload if isinstance(payload, list) else [])
        records.extend(record_from_mapping(row) for row in rows)
    if disease:
        records = [record for record in records if record.disease == disease]
    if horizon_days is not None:
        records = [record for record in records if record.horizon_days == int(horizon_days)]
    return sort_records_temporally(records)


def mask_record(record: FeatureRecord, feature_groups: list[str]) -> FeatureRecord:
    return replace(record, feature_json=filter_feature_json(record.feature_json, feature_groups))


def summarize_interpretable_baselines(results: list[dict[str, Any]]) -> dict[str, Any]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        by_model[str(row.get("model_name"))].append(row)
    summaries: list[dict[str, Any]] = []
    for model_name, rows in sorted(by_model.items()):
        executed = [row for row in rows if row.get("status") == "executed"]
        scores = [_score_row(row) for row in executed]
        summaries.append(
            {
                "model_name": model_name,
                "executed_runs": len(executed),
                "total_runs": len(rows),
                "average_score": round(sum(scores) / len(scores), 6) if scores else 0.0,
                "average_stability_score": round(
                    sum(float(row.get("stability_score", 0.0)) for row in executed) / len(executed),
                    6,
                )
                if executed
                else 0.0,
                "requires_human_review": True,
            }
        )
    return {
        "models": sorted(summaries, key=lambda row: row["average_score"], reverse=True),
        "limitations": [
            "Interpretable baselines are low-dependency local benchmarks.",
            "They do not guarantee future accuracy.",
        ],
    }


def interpretability_score(feature_groups: list[str]) -> float:
    penalty = max(0, len(feature_groups) - 2) * 0.04
    return round(max(0.6, 0.95 - penalty), 6)


def _ablation_row(
    *,
    disease: str,
    country: str,
    horizon_days: int,
    model_name: str,
    feature_set_name: str,
    feature_groups: list[str],
    status: str,
    metrics: dict[str, Any] | None = None,
    training_rows: int = 0,
    test_rows: int = 0,
    rolling_folds: int = 0,
    stability_score: float = 0.0,
    missing_feature_penalty: float = 0.0,
    interpretability_score: float | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "disease": disease,
        "country": country,
        "horizon_days": int(horizon_days),
        "model_name": model_name,
        "feature_set_name": feature_set_name,
        "feature_groups": list(feature_groups),
        "status": status,
        "metrics": metrics or {},
        "training_rows": int(training_rows),
        "test_rows": int(test_rows),
        "rolling_folds": int(rolling_folds),
        "stability_score": round(float(stability_score), 6),
        "missing_feature_penalty": round(float(missing_feature_penalty), 6),
        "interpretability_score": (
            round(float(interpretability_score), 6)
            if interpretability_score is not None
            else globals()["interpretability_score"](feature_groups)
        ),
        "warnings": sorted(set(warnings or [])),
        "requires_human_review": True,
    }


def _score_row(row: dict[str, Any]) -> float:
    regression = row.get("metrics", {}).get("regression", {})
    classification = row.get("metrics", {}).get("classification", {})
    smape = float(regression.get("smape", 1.0))
    mae = float(regression.get("mae", 10.0))
    f1 = float(classification.get("f1_score", 0.0))
    base = (0.45 / (1.0 + max(0.0, smape))) + (0.35 / (1.0 + max(0.0, mae) / 10.0)) + (0.20 * f1)
    return max(0.0, min(1.0, base))


def _stability_score(residuals: list[float]) -> float:
    if not residuals:
        return 0.0
    if len(residuals) == 1:
        return 0.5
    std = statistics.stdev(residuals)
    mean_abs = statistics.mean(abs(value) for value in residuals)
    return round(1.0 / (1.0 + std / (1.0 + mean_abs)), 6)


def _round_mapping(mapping: dict[str, Any]) -> dict[str, float | int]:
    rounded: dict[str, float | int] = {}
    for key, value in mapping.items():
        if isinstance(value, bool):
            rounded[key] = int(value)
        elif isinstance(value, int):
            rounded[key] = value
        else:
            try:
                rounded[key] = round(float(value), 6)
            except (TypeError, ValueError):
                rounded[key] = 0.0
    return rounded


__all__ = [
    "M2_M2_FEATURE_SETS",
    "evaluate_feature_subset",
    "interpretability_score",
    "load_feature_ablation_records",
    "mask_record",
    "run_feature_ablation",
    "summarize_interpretable_baselines",
]
