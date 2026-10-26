from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict

from sqlalchemy.orm import Session

from common.config import get_settings
from common.db import get_session, init_db
from predictor_agent.backtest import (
    backtest_summary,
    fit_predictor_and_predict,
    group_feature_rows,
    run_backtest,
    rolling_origin_backtest,
)
from predictor_agent.baseline import MODEL_NAME
from predictor_agent.baselines import get_baseline_model
from predictor_agent.dataset import FeatureRow, load_feature_rows
from predictor_agent.datasets import FeatureRecord, load_feature_records, sort_records_temporally
from predictor_agent.experience_bank import ExperienceBankService
from predictor_agent.feedback import FeedbackInput, FeedbackService
from predictor_agent.prediction_store import PredictionStore, classify_risk_level


class PredictorAgent:
    def __init__(self, session: Session):
        self.session = session

    def run(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        admin1: str | None = None,
        horizons_days: tuple[int, ...] = (7, 28),
        split_type: str = "train",
        model_name: str = MODEL_NAME,
        min_train_size: int = 4,
        window_size: int = 4,
        seasonal_lag_weeks: int = 52,
        fallback_window_size: int = 4,
        replace_existing: bool = True,
    ) -> dict:
        rows = load_feature_rows(
            self.session,
            country=country,
            disease=disease,
            admin1=admin1,
            split_type=split_type,
            horizons_days=horizons_days,
        )
        backtest_records = rolling_origin_backtest(
            rows,
            model_name=model_name,
            min_train_size=min_train_size,
            window_size=window_size,
            seasonal_lag_weeks=seasonal_lag_weeks,
            fallback_window_size=fallback_window_size,
        )
        metrics = backtest_summary(backtest_records, model_name=model_name)
        prediction_rows = build_latest_prediction_rows(
            rows,
            metrics=metrics,
            model_name=model_name,
            min_train_size=min_train_size,
            window_size=window_size,
            seasonal_lag_weeks=seasonal_lag_weeks,
            fallback_window_size=fallback_window_size,
        )
        store_summary = PredictionStore(self.session).save_predictions(
            prediction_rows,
            replace_existing=replace_existing,
        )
        return {
            "features_seen": len(rows),
            "backtest_records": len(backtest_records),
            "prediction_rows": len(prediction_rows),
            "saved": store_summary["saved"],
            "updated": store_summary["updated"],
            "skipped": store_summary["skipped"],
            "metrics": metrics,
        }

    def backtest(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        admin1: str | None = None,
        horizon_days: int = 28,
        split_type: str = "train",
        model_name: str = "moving_average",
        min_train_size: int = 8,
        test_window: int = 1,
    ) -> dict:
        records = load_feature_records(
            self.session,
            country=country,
            disease=disease,
            admin1=admin1,
            horizon_days=horizon_days,
            split_type=split_type,
        )
        result = run_backtest(
            records,
            model_name=model_name,
            horizon_days=horizon_days,
            min_train_size=min_train_size,
            test_window=test_window,
        )
        result["features_seen"] = len(records)
        return result

    def predict_latest(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        admin1: str | None = None,
        horizon_days: int = 28,
        split_type: str = "train",
        model_name: str = "moving_average",
        min_train_size: int = 4,
        replace_existing: bool = True,
        write_predictions: bool = True,
    ) -> dict:
        records = load_feature_records(
            self.session,
            country=country,
            disease=disease,
            admin1=admin1,
            horizon_days=horizon_days,
            split_type=split_type,
        )
        predictions: list[dict] = []
        warnings: list[str] = []
        store_summary = {"rows_seen": 0, "saved": 0, "updated": 0, "skipped": 0}
        store = PredictionStore(self.session)
        for series in _group_feature_records(records).values():
            complete = [record for record in series if record.target_value is not None]
            if len(complete) <= min_train_size:
                warnings.append("insufficient records for latest prediction")
                continue
            train = complete[:-1]
            latest = complete[-1]
            model = get_baseline_model(model_name)
            model.fit(train)
            output = model.predict_one(latest)
            risk_level = classify_risk_level(output.predicted_outbreak_probability)
            version = _model_version(model_name, horizon_days)
            if write_predictions:
                summary = store.save_prediction(
                    feature_record=latest,
                    output=output,
                    model_version=version,
                    risk_level=risk_level,
                    replace_existing=replace_existing,
                )
                for key in store_summary:
                    store_summary[key] += summary[key]
            predictions.append(
                {
                    "feature_record_id": latest.id,
                    "country": latest.country,
                    "admin1": latest.admin1,
                    "disease": latest.disease,
                    "target_date": latest.target_date.isoformat(),
                    "iso_week": latest.iso_week,
                    "horizon_days": latest.horizon_days,
                    "model_name": str(output.explanation.get("model_name") or model_name),
                    "model_version": version,
                    "predicted_cases": output.predicted_cases,
                    "lower_bound": output.lower_bound,
                    "upper_bound": output.upper_bound,
                    "predicted_outbreak_probability": output.predicted_outbreak_probability,
                    "risk_level": risk_level,
                }
            )
        return {
            "model_name": _cli_model_name(model_name),
            "model_version": _model_version(model_name, horizon_days),
            "horizon_days": horizon_days,
            "features_seen": len(records),
            "prediction_rows": len(predictions),
            "predictions_written": store_summary["saved"] + store_summary["updated"],
            "saved": store_summary["saved"],
            "updated": store_summary["updated"],
            "skipped": store_summary["skipped"],
            "predictions": predictions,
            "warnings": warnings,
        }


def build_latest_prediction_rows(
    rows: list[FeatureRow],
    *,
    metrics: dict,
    model_name: str = MODEL_NAME,
    min_train_size: int = 4,
    window_size: int = 4,
    seasonal_lag_weeks: int = 52,
    fallback_window_size: int = 4,
) -> list[dict]:
    prediction_rows: list[dict] = []
    for series in group_feature_rows(rows).values():
        if len(series) <= min_train_size:
            continue
        latest = series[-1]
        train = series[:-1]
        if len(train) < min_train_size:
            continue
        result = fit_predictor_and_predict(
            train,
            predict_row=latest,
            model_name=model_name,
            window_size=window_size,
            seasonal_lag_weeks=seasonal_lag_weeks,
            fallback_window_size=fallback_window_size,
        )
        prediction = result.prediction
        prediction_rows.append(
            {
                "country": latest.country,
                "admin1": latest.admin1,
                "disease": latest.disease,
                "target_date": latest.target_date,
                "iso_week": latest.iso_week,
                "horizon_days": latest.horizon_days,
                "model_name": result.model_name,
                "model_version": result.model_version,
                "predicted_value": prediction.predicted_value,
                "lower_bound": prediction.lower_bound,
                "upper_bound": prediction.upper_bound,
                "uncertainty_method": prediction.uncertainty_method,
                "prediction_interval_json": {
                    "level": 0.95,
                    "lower_bound": prediction.lower_bound,
                    "upper_bound": prediction.upper_bound,
                    "residual_std": prediction.residual_std,
                },
                "metrics_json": metrics,
                "provenance_json": {
                    "feature_row_id": latest.id,
                    "source": "model_features",
                    "model_name": result.model_name,
                    "model_version": result.model_version,
                    "predictor_family": result.predictor_family,
                    "training_count": prediction.training_count,
                    "min_train_size": min_train_size,
                    "window_size": window_size,
                    "seasonal_lag_weeks": seasonal_lag_weeks,
                    "fallback_window_size": fallback_window_size,
                    "human_review_required": True,
                },
                "review_status": "pending",
            }
        )
    return prediction_rows


def _parse_horizons(value: str) -> tuple[int, ...]:
    try:
        horizons = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("horizons-days must be comma-separated integers.") from exc
    if not horizons:
        raise argparse.ArgumentTypeError("horizons-days must include at least one value.")
    for horizon in horizons:
        if horizon <= 0 or horizon % 7 != 0:
            raise argparse.ArgumentTypeError("horizons-days values must be positive multiples of 7.")
    return horizons


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] in {
        "backtest",
        "predict",
        "submit-feedback",
        "build-experience",
        "summarize-feedback",
        "summarize-experiences",
    }:
        return _main_subcommand(args_list)
    return _main_legacy(args_list)


def _main_legacy(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run local predictor_agent baseline MVP.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--country", default=None)
    parser.add_argument("--disease", default=None)
    parser.add_argument("--admin1", default=None)
    parser.add_argument("--horizons-days", type=_parse_horizons, default=(7, 28))
    parser.add_argument("--split-type", default="train")
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--min-train-size", type=int, default=4)
    parser.add_argument("--window-size", type=int, default=4)
    parser.add_argument("--seasonal-lag-weeks", type=int, default=52)
    parser.add_argument("--fallback-window-size", type=int, default=4)
    parser.add_argument("--no-replace-existing", action="store_true")
    args = parser.parse_args(argv)

    database_url = args.database_url or get_settings().database_url
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        summary = PredictorAgent(session).run(
            country=args.country,
            disease=args.disease,
            admin1=args.admin1,
            horizons_days=args.horizons_days,
            split_type=args.split_type,
            model_name=args.model,
            min_train_size=args.min_train_size,
            window_size=args.window_size,
            seasonal_lag_weeks=args.seasonal_lag_weeks,
            fallback_window_size=args.fallback_window_size,
            replace_existing=not args.no_replace_existing,
        )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def _main_subcommand(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run stable predictor_agent baseline commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backtest_parser = subparsers.add_parser("backtest", help="Run a local rolling-origin backtest.")
    _add_common_stable_args(backtest_parser)
    backtest_parser.add_argument("--min-train-size", type=int, default=8)
    backtest_parser.add_argument("--test-window", type=int, default=1)

    predict_parser = subparsers.add_parser("predict", help="Write the latest local baseline prediction.")
    _add_common_stable_args(predict_parser)
    predict_parser.add_argument("--min-train-size", type=int, default=4)
    predict_parser.add_argument("--no-replace-existing", action="store_true")
    predict_parser.add_argument("--no-write", action="store_true")

    feedback_parser = subparsers.add_parser("submit-feedback", help="Submit local human feedback.")
    feedback_parser.add_argument("--database-url", default=None)
    feedback_parser.add_argument("--prediction-id", required=True)
    feedback_parser.add_argument("--reviewer", default=None)
    feedback_parser.add_argument("--verdict", required=True)
    feedback_parser.add_argument("--true-cases", type=float, default=None)
    feedback_parser.add_argument("--true-outbreak-label", type=_parse_bool, default=None)
    feedback_parser.add_argument("--reason", default=None)
    feedback_parser.add_argument("--notes", default=None)

    experience_parser = subparsers.add_parser("build-experience", help="Build one experience-bank record.")
    experience_parser.add_argument("--database-url", default=None)
    experience_parser.add_argument("--feedback-id", required=True)

    feedback_summary_parser = subparsers.add_parser("summarize-feedback", help="Summarize local feedback.")
    feedback_summary_parser.add_argument("--database-url", default=None)
    feedback_summary_parser.add_argument("--country", default=None)
    feedback_summary_parser.add_argument("--disease", default=None)

    experience_summary_parser = subparsers.add_parser("summarize-experiences", help="Summarize experience bank.")
    experience_summary_parser.add_argument("--database-url", default=None)
    experience_summary_parser.add_argument("--country", default=None)
    experience_summary_parser.add_argument("--disease", default=None)

    args = parser.parse_args(argv)
    database_url = args.database_url or get_settings().database_url
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        agent = PredictorAgent(session)
        if args.command == "backtest":
            summary = agent.backtest(
                country=args.country,
                disease=args.disease,
                admin1=args.admin1,
                horizon_days=args.horizon_days,
                split_type=args.split_type,
                model_name=args.model,
                min_train_size=args.min_train_size,
                test_window=args.test_window,
            )
        elif args.command == "predict":
            summary = agent.predict_latest(
                country=args.country,
                disease=args.disease,
                admin1=args.admin1,
                horizon_days=args.horizon_days,
                split_type=args.split_type,
                model_name=args.model,
                min_train_size=args.min_train_size,
                replace_existing=not args.no_replace_existing,
                write_predictions=not args.no_write,
            )
        elif args.command == "submit-feedback":
            result = FeedbackService(session).submit_feedback(
                FeedbackInput(
                    prediction_id=args.prediction_id,
                    reviewer=args.reviewer,
                    verdict=args.verdict,
                    true_cases=args.true_cases,
                    true_outbreak_label=args.true_outbreak_label,
                    reason=args.reason,
                    notes=args.notes,
                )
            )
            summary = {
                "prediction_id": result["prediction_id"],
                "feedback_id": result["feedback_id"],
                "experience_id": result["experience_id"],
                "verdict": result["verdict"],
                "feedback_created": result["feedback_created"],
                "experience_created": result["experience_created"],
            }
        elif args.command == "build-experience":
            experience = ExperienceBankService(session).create_experience_from_feedback(args.feedback_id)
            summary = _experience_cli_summary(experience)
        elif args.command == "summarize-feedback":
            summary = FeedbackService(session).summarize_feedback(country=args.country, disease=args.disease)
        elif args.command == "summarize-experiences":
            summary = ExperienceBankService(session).summarize_experiences(
                country=args.country,
                disease=args.disease,
            )
        else:
            raise ValueError(f"Unsupported predictor_agent command: {args.command}.")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def _add_common_stable_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--country", default=None)
    parser.add_argument("--disease", default=None)
    parser.add_argument("--admin1", default=None)
    parser.add_argument("--horizon-days", type=int, default=28)
    parser.add_argument("--split-type", default="train")
    parser.add_argument("--model", default="moving_average")


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise argparse.ArgumentTypeError("Boolean values must be true or false.")


def _experience_cli_summary(experience) -> dict:
    if experience is None:
        return {
            "experience_created": False,
            "experience_id": None,
            "failure_type": None,
            "weight_for_retraining": None,
        }
    return {
        "experience_created": True,
        "experience_id": experience.id,
        "prediction_id": experience.prediction_id,
        "feedback_id": experience.feedback_id,
        "failure_type": experience.failure_type,
        "weight_for_retraining": experience.weight_for_retraining,
        "recommended_action": experience.recommended_action,
    }


def _group_feature_records(records: list[FeatureRecord]) -> dict[tuple[str, str | None, str, int], list[FeatureRecord]]:
    groups: dict[tuple[str, str | None, str, int], list[FeatureRecord]] = defaultdict(list)
    for record in sort_records_temporally(records):
        groups[(record.country, record.admin1, record.disease, record.horizon_days)].append(record)
    return dict(groups)


def _cli_model_name(model_name: str) -> str:
    normalized = (model_name or "").strip().lower()
    if normalized in {"moving_average", "moving_average_baseline"}:
        return "moving_average"
    return normalized


def _model_version(model_name: str, horizon_days: int) -> str:
    return f"baseline:{_cli_model_name(model_name)}:h{int(horizon_days)}:v1"


if __name__ == "__main__":
    raise SystemExit(main())
