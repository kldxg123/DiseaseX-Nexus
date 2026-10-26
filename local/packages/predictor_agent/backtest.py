from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from predictor_agent.baseline import MODEL_NAME, MODEL_VERSION, BaselinePrediction, MovingAverageBaseline
from predictor_agent.baselines import FeatureRulePredictor, SeasonalNaivePredictor, get_baseline_model, get_predictor_factory
from predictor_agent.dataset import FeatureRow
from predictor_agent.datasets import FeatureRecord, sort_records_temporally
from predictor_agent.metrics import classification_metrics, regression_metrics


@dataclass(frozen=True)
class BacktestRecord:
    country: str
    admin1: str | None
    disease: str
    iso_week: str
    horizon_days: int
    actual_value: float
    predicted_value: float
    lower_bound: float
    upper_bound: float
    training_count: int
    model_name: str = MODEL_NAME
    model_version: str = MODEL_VERSION


@dataclass(frozen=True)
class PredictorResult:
    prediction: BaselinePrediction
    model_name: str
    model_version: str
    predictor_family: str


def group_feature_rows(
    rows: list[FeatureRow],
) -> dict[tuple[str, str | None, str, int], list[FeatureRow]]:
    groups: dict[tuple[str, str | None, str, int], list[FeatureRow]] = defaultdict(list)
    for row in rows:
        groups[(row.country, row.admin1, row.disease, row.horizon_days)].append(row)
    for key in groups:
        groups[key].sort(key=lambda item: (item.target_date, item.iso_week))
    return dict(groups)


def rolling_origin_backtest(
    rows: list[FeatureRow],
    *,
    model_name: str = MODEL_NAME,
    min_train_size: int = 4,
    window_size: int = 4,
    seasonal_lag_weeks: int = 52,
    fallback_window_size: int = 4,
) -> list[BacktestRecord]:
    records: list[BacktestRecord] = []
    for series in group_feature_rows(rows).values():
        for index in range(min_train_size, len(series)):
            train = series[:index]
            test = series[index]
            result = fit_predictor_and_predict(
                train,
                predict_row=test,
                model_name=model_name,
                window_size=window_size,
                seasonal_lag_weeks=seasonal_lag_weeks,
                fallback_window_size=fallback_window_size,
            )
            prediction = result.prediction
            records.append(
                BacktestRecord(
                    country=test.country,
                    admin1=test.admin1,
                    disease=test.disease,
                    iso_week=test.iso_week,
                    horizon_days=test.horizon_days,
                    actual_value=test.target_value,
                    predicted_value=prediction.predicted_value,
                    lower_bound=prediction.lower_bound,
                    upper_bound=prediction.upper_bound,
                    training_count=prediction.training_count,
                    model_name=result.model_name,
                    model_version=result.model_version,
                )
            )
    return records


def backtest_summary(
    records: list[BacktestRecord],
    *,
    model_name: str = MODEL_NAME,
) -> dict:
    metrics_by_horizon: dict[str, dict] = {}
    grouped: dict[int, list[BacktestRecord]] = defaultdict(list)
    for record in records:
        grouped[record.horizon_days].append(record)
    for horizon, horizon_records in sorted(grouped.items()):
        metrics_by_horizon[str(horizon)] = regression_metrics(horizon_records)
    overall = regression_metrics(records)
    resolved_name, resolved_version, _family = resolve_predictor_identity(model_name)
    if records:
        resolved_name = records[0].model_name
        resolved_version = records[0].model_version
    return {
        "model_name": resolved_name,
        "model_version": resolved_version,
        "overall": overall,
        "by_horizon_days": metrics_by_horizon,
    }


def fit_predictor_and_predict(
    train_rows: list[FeatureRow],
    *,
    predict_row: FeatureRow | None,
    model_name: str = MODEL_NAME,
    window_size: int = 4,
    seasonal_lag_weeks: int = 52,
    fallback_window_size: int = 4,
) -> PredictorResult:
    factory = get_predictor_factory(model_name)
    if factory is FeatureRulePredictor:
        predictor = factory(window_size=window_size).fit(train_rows)
        prediction = predictor.predict(predict_row.feature_json if predict_row is not None else None)
    elif factory is SeasonalNaivePredictor:
        predictor = factory(
            seasonal_lag_weeks=seasonal_lag_weeks,
            fallback_window_size=fallback_window_size,
        ).fit([row.target_value for row in train_rows])
        prediction = predictor.predict()
    else:
        predictor = factory(window_size=window_size).fit([row.target_value for row in train_rows])
        prediction = predictor.predict()
    resolved_name, resolved_version, family = resolve_predictor_identity(model_name)
    return PredictorResult(
        prediction=prediction,
        model_name=resolved_name,
        model_version=resolved_version,
        predictor_family=family,
    )


def resolve_predictor_identity(model_name: str) -> tuple[str, str, str]:
    factory = get_predictor_factory(model_name)
    if factory is MovingAverageBaseline:
        return MODEL_NAME, MODEL_VERSION, "moving_average"
    predictor = factory()
    resolved_name = str(getattr(predictor, "model_name", model_name))
    resolved_version = str(getattr(predictor, "model_version", MODEL_VERSION))
    return resolved_name, resolved_version, resolved_name


def make_rolling_splits(
    records: list[FeatureRecord],
    *,
    min_train_size: int = 8,
    test_window: int = 1,
) -> list[tuple[list[FeatureRecord], list[FeatureRecord]]]:
    usable = [record for record in sort_records_temporally(records) if record.target_value is not None]
    splits: list[tuple[list[FeatureRecord], list[FeatureRecord]]] = []
    if min_train_size <= 0 or test_window <= 0:
        raise ValueError("min_train_size and test_window must be positive.")
    for start in range(min_train_size, len(usable), test_window):
        train = usable[:start]
        test = usable[start : start + test_window]
        if not test:
            continue
        if train[-1].target_date >= test[0].target_date:
            continue
        splits.append((train, test))
    return splits


def run_backtest(
    records: list[FeatureRecord],
    *,
    model_name: str = "moving_average",
    horizon_days: int | None = None,
    min_train_size: int = 8,
    test_window: int = 1,
    outbreak_probability_threshold: float = 0.5,
) -> dict:
    filtered = [
        record
        for record in records
        if record.target_value is not None and (horizon_days is None or record.horizon_days == horizon_days)
    ]
    filtered = sort_records_temporally(filtered)
    warnings: list[str] = []
    grouped = _group_feature_records(filtered)
    split_sets = [
        split
        for series in grouped.values()
        for split in make_rolling_splits(series, min_train_size=min_train_size, test_window=test_window)
    ]
    if not split_sets:
        warnings.append("insufficient records for rolling backtest")
    predictions: list[dict] = []
    actual_cases: list[float] = []
    predicted_cases: list[float] = []
    labels: list[bool] = []
    probabilities: list[float] = []
    for train, test_rows in split_sets:
        model = get_baseline_model(model_name)
        model.fit(train)
        for record in test_rows:
            output = model.predict_one(record)
            actual = float(record.target_value or 0.0)
            label = bool(record.target_outbreak_label)
            actual_cases.append(actual)
            predicted_cases.append(output.predicted_cases)
            labels.append(label)
            probabilities.append(output.predicted_outbreak_probability)
            predictions.append(
                {
                    "feature_record_id": record.id,
                    "target_date": record.target_date.isoformat(),
                    "actual_cases": actual,
                    "predicted_cases": output.predicted_cases,
                    "actual_outbreak": label,
                    "predicted_outbreak_probability": output.predicted_outbreak_probability,
                    "lower_bound": output.lower_bound,
                    "upper_bound": output.upper_bound,
                    "explanation": _safe_output_explanation(output.explanation),
                }
            )
    return {
        "model_name": _normalized_model_name(model_name),
        "horizon_days": horizon_days,
        "splits": len(split_sets),
        "predictions": predictions,
        "metrics": {
            "regression": regression_metrics(actual_cases, predicted_cases),
            "classification": classification_metrics(labels, probabilities, threshold=outbreak_probability_threshold),
        },
        "warnings": warnings,
    }


def _normalized_model_name(model_name: str) -> str:
    normalized = (model_name or "").strip().lower()
    if normalized in {"moving_average", "moving_average_baseline"}:
        return "moving_average"
    return normalized


def _safe_output_explanation(explanation: dict) -> dict:
    return {
        "model_name": explanation.get("model_name"),
        "fallback_used": bool(explanation.get("fallback_used", False)),
        "training_rows": int(explanation.get("training_rows", 0) or 0),
        "uncertainty_method": explanation.get("uncertainty_method"),
        "residual_std": float(explanation.get("residual_std", 0.0) or 0.0),
    }


def _group_feature_records(records: list[FeatureRecord]) -> dict[tuple[str, str | None, str, int], list[FeatureRecord]]:
    groups: dict[tuple[str, str | None, str, int], list[FeatureRecord]] = defaultdict(list)
    for record in records:
        groups[(record.country, record.admin1, record.disease, record.horizon_days)].append(record)
    return dict(groups)
