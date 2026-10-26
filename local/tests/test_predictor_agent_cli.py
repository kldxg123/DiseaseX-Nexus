from __future__ import annotations

import json
from datetime import date, timedelta

from common.db import get_session, init_db
from common.models import ModelFeature, Prediction
from predictor_agent.agent import PredictorAgent, main


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'predictor-agent.db'}"


def _seed_features(database_url: str) -> None:
    start = date(2025, 1, 6)
    with get_session(database_url) as session:
        for horizon_days in (7, 28):
            for index in range(10):
                session.add(
                    ModelFeature(
                        country="United States",
                        admin1=None,
                        disease="norovirus",
                        target_date=start + timedelta(days=7 * index),
                        iso_week=f"2025-W{index + 1:02d}",
                        horizon_days=horizon_days,
                        feature_json={
                            "current_week_cases": index + 1,
                            "lag_1w_cases": index,
                            "data_quality_mean": 0.8,
                        },
                        target_value=float(index + (1 if horizon_days == 7 else 4)),
                        target_outbreak_label=index % 3 == 0,
                        split_type="train",
                    )
                )


def test_predictor_agent_runs_backtest_and_saves_latest_predictions(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    with get_session(database_url) as session:
        summary = PredictorAgent(session).run(
            country="United States",
            disease="norovirus",
            horizons_days=(7, 28),
            min_train_size=4,
            window_size=2,
        )
        prediction_count = session.query(Prediction).count()
        predictions = session.query(Prediction).order_by(Prediction.horizon_days).all()

    assert summary["features_seen"] == 20
    assert summary["backtest_records"] == 12
    assert summary["prediction_rows"] == 2
    assert summary["saved"] == 2
    assert summary["updated"] == 0
    assert prediction_count == 2
    assert {prediction.horizon_days for prediction in predictions} == {7, 28}
    assert all(prediction.lower_bound <= prediction.predicted_value <= prediction.upper_bound for prediction in predictions)


def test_predictor_agent_supports_seasonal_naive_predictions(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    with get_session(database_url) as session:
        summary = PredictorAgent(session).run(
            country="United States",
            disease="norovirus",
            horizons_days=(7,),
            model_name="seasonal_naive",
            min_train_size=4,
            seasonal_lag_weeks=2,
            fallback_window_size=2,
        )
        predictions = session.query(Prediction).all()

    assert summary["prediction_rows"] == 1
    assert predictions[0].model_name == "seasonal_naive"
    assert predictions[0].provenance_json["predictor_family"] == "seasonal_naive"


def test_predictor_agent_supports_feature_rule_predictions(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    with get_session(database_url) as session:
        summary = PredictorAgent(session).run(
            country="United States",
            disease="norovirus",
            horizons_days=(7,),
            model_name="feature_rule",
            min_train_size=4,
            window_size=2,
        )
        predictions = session.query(Prediction).all()

    assert summary["prediction_rows"] == 1
    assert predictions[0].model_name == "feature_rule"
    assert predictions[0].provenance_json["predictor_family"] == "feature_rule"
    assert predictions[0].predicted_value >= 0


def test_predictor_agent_cli_outputs_safe_json(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    result = main(
        [
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "norovirus",
            "--horizons-days",
            "7,28",
            "--min-train-size",
            "4",
            "--window-size",
            "2",
        ]
    )
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert result == 0
    assert summary["features_seen"] == 20
    assert summary["prediction_rows"] == 2
    assert summary["saved"] == 2
    assert "current_week_cases" not in captured.out


def test_predictor_agent_cli_supports_seasonal_naive(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    result = main(
        [
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "norovirus",
            "--horizons-days",
            "7",
            "--model",
            "seasonal_naive",
            "--seasonal-lag-weeks",
            "2",
            "--fallback-window-size",
            "2",
        ]
    )
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert result == 0
    assert summary["prediction_rows"] == 1
    assert summary["saved"] == 1
    assert summary["metrics"]["model_name"] == "seasonal_naive"
    assert "current_week_cases" not in captured.out


def test_predictor_agent_cli_supports_feature_rule(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    result = main(
        [
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "norovirus",
            "--horizons-days",
            "7",
            "--model",
            "feature_rule",
        ]
    )
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert result == 0
    assert summary["prediction_rows"] == 1
    assert summary["saved"] == 1
    assert summary["metrics"]["model_name"] == "feature_rule"
    assert "current_week_cases" not in captured.out


def test_predictor_agent_stable_backtest_method(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    with get_session(database_url) as session:
        summary = PredictorAgent(session).backtest(
            country="United States",
            disease="norovirus",
            horizon_days=7,
            model_name="seasonal_naive",
            min_train_size=4,
        )

    assert summary["features_seen"] == 10
    assert summary["model_name"] == "seasonal_naive"
    assert summary["metrics"]["regression"]["n"] > 0
    assert "current_week_cases" not in json.dumps(summary)


def test_predictor_agent_stable_predict_latest_method_writes_prediction(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    with get_session(database_url) as session:
        summary = PredictorAgent(session).predict_latest(
            country="United States",
            disease="norovirus",
            horizon_days=7,
            model_name="moving_average",
            min_train_size=4,
        )
        saved = session.query(Prediction).one()

    assert summary["prediction_rows"] == 1
    assert summary["saved"] == 1
    assert saved.model_name == "moving_average"
    assert saved.model_version == "baseline:moving_average:h7:v1"
    assert saved.review_status == "pending"


def test_predictor_agent_cli_backtest_subcommand_outputs_safe_json(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    result = main(
        [
            "backtest",
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "norovirus",
            "--horizon-days",
            "7",
            "--model",
            "seasonal_naive",
            "--min-train-size",
            "4",
        ]
    )
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert result == 0
    assert summary["model_name"] == "seasonal_naive"
    assert summary["features_seen"] == 10
    assert "current_week_cases" not in captured.out


def test_predictor_agent_cli_predict_subcommand_writes_safe_prediction(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_features(database_url)

    result = main(
        [
            "predict",
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "norovirus",
            "--horizon-days",
            "7",
            "--model",
            "feature_rule",
            "--min-train-size",
            "4",
        ]
    )
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    with get_session(database_url) as session:
        saved = session.query(Prediction).one()

    assert result == 0
    assert summary["model_name"] == "feature_rule"
    assert summary["saved"] == 1
    assert saved.model_name == "feature_rule"
    assert "current_week_cases" not in captured.out
