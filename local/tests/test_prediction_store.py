from __future__ import annotations

from datetime import date

from common.db import get_session, init_db
from common.models import Prediction
from predictor_agent.baselines import PredictionOutput
from predictor_agent.datasets import FeatureRecord
from predictor_agent.prediction_store import PredictionStore, classify_risk_level


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'prediction-store.db'}"


def _prediction_row(value: float = 4.0) -> dict:
    return {
        "country": "United States",
        "admin1": None,
        "disease": "norovirus",
        "target_date": date(2025, 3, 1),
        "iso_week": "2025-W08",
        "horizon_days": 7,
        "model_name": "moving_average_baseline",
        "model_version": "v1",
        "predicted_value": value,
        "lower_bound": max(0.0, value - 1),
        "upper_bound": value + 1,
        "uncertainty_method": "rolling_residual_std_95pct",
        "prediction_interval_json": {"level": 0.95},
        "metrics_json": {"overall": {"mae": 1.0}},
        "provenance_json": {"feature_row_id": 1, "human_review_required": True},
        "review_status": "pending",
    }


def test_prediction_store_saves_updates_and_skips_duplicates(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        store = PredictionStore(session)
        first = store.save_predictions([_prediction_row()])
        second = store.save_predictions([_prediction_row(6.0)], replace_existing=True)
        third = store.save_predictions([_prediction_row(8.0)], replace_existing=False)
        count = store.count_predictions(country="United States", disease="norovirus")
        saved = session.query(Prediction).one()

    assert first == {"rows_seen": 1, "saved": 1, "updated": 0, "skipped": 0}
    assert second == {"rows_seen": 1, "saved": 0, "updated": 1, "skipped": 0}
    assert third == {"rows_seen": 1, "saved": 0, "updated": 0, "skipped": 1}
    assert count == 1
    assert saved.predicted_value == 6.0
    assert saved.lower_bound <= saved.predicted_value <= saved.upper_bound


def test_prediction_store_keeps_distinct_models_for_same_target(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    seasonal_row = {**_prediction_row(), "model_name": "seasonal_naive", "predicted_value": 5.0}
    feature_row = {**_prediction_row(), "model_name": "feature_rule", "predicted_value": 7.0}

    with get_session(database_url) as session:
        summary = PredictionStore(session).save_predictions([seasonal_row, feature_row])
        rows = session.query(Prediction).order_by(Prediction.model_name).all()

    assert summary == {"rows_seen": 2, "saved": 2, "updated": 0, "skipped": 0}
    assert [row.model_name for row in rows] == ["feature_rule", "seasonal_naive"]
    assert {row.predicted_value for row in rows} == {5.0, 7.0}


def test_prediction_store_saves_stable_prediction_output(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    feature_record = FeatureRecord(
        id="feature-1",
        country="United States",
        admin1=None,
        disease="norovirus",
        target_date=date(2025, 3, 1),
        iso_week="2025-W08",
        horizon_days=7,
        feature_json={"current_week_cases": 9},
        target_value=10.0,
        target_outbreak_label=True,
        split_type="train",
    )
    output = PredictionOutput(
        predicted_cases=6.0,
        predicted_outbreak_probability=0.8,
        lower_bound=4.0,
        upper_bound=8.0,
        explanation={
            "model_name": "moving_average",
            "input_feature_keys": ["current_week_cases"],
            "training_rows": 8,
            "uncertainty_method": "baseline_interval",
        },
    )

    with get_session(database_url) as session:
        summary = PredictionStore(session).save_prediction(
            feature_record=feature_record,
            output=output,
            model_version="baseline:moving_average:h7:v1",
        )
        saved = session.query(Prediction).one()

    assert summary == {"rows_seen": 1, "saved": 1, "updated": 0, "skipped": 0}
    assert saved.model_name == "moving_average"
    assert saved.model_version == "baseline:moving_average:h7:v1"
    assert saved.metrics_json["risk_level"] == "critical"
    assert "feature_json" not in str(saved.provenance_json)
    assert classify_risk_level(0.1) == "low"
    assert classify_risk_level(0.3) == "medium"
    assert classify_risk_level(0.6) == "high"
    assert classify_risk_level(0.9) == "critical"
