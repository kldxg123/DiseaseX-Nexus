from __future__ import annotations

from datetime import date

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select

from common.db import get_session
from common.models import HumanFeedback, ModelExperienceBank, Prediction, PredictionFeedback
from predictor_agent import ExperienceBankStore, FeedbackInput, FeedbackStore


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'feedback-table-name.db'}"


def _upgrade_head(database_url: str) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")


def _add_prediction(session) -> Prediction:
    prediction = Prediction(
        country="United States",
        admin1=None,
        disease="norovirus",
        target_date=date(2025, 3, 1),
        iso_week="2025-W08",
        horizon_days=7,
        model_name="feature_rule",
        model_version="v1",
        predicted_value=10.0,
        lower_bound=8.0,
        upper_bound=12.0,
        uncertainty_method="rolling_residual_std_95pct",
        prediction_interval_json={"level": 0.95},
        metrics_json={},
        provenance_json={"predicted_outbreak_label": False},
        review_status="pending",
    )
    session.add(prediction)
    session.flush()
    return prediction


def test_alembic_head_renames_feedback_table_and_foreign_key(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    _upgrade_head(database_url)

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        foreign_keys = inspector.get_foreign_keys("model_experience_bank")
    finally:
        engine.dispose()

    assert "human_feedback" in tables
    assert "model_experience_bank" in tables
    assert "prediction_feedback" not in tables
    assert any(
        key["referred_table"] == "human_feedback" and key["constrained_columns"] == ["feedback_id"]
        for key in foreign_keys
    )


def test_human_feedback_orm_and_old_alias_use_same_mapping(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    _upgrade_head(database_url)

    assert HumanFeedback.__tablename__ == "human_feedback"
    assert PredictionFeedback is HumanFeedback

    with get_session(database_url) as session:
        prediction = _add_prediction(session)
        feedback = HumanFeedback(
            prediction_id=prediction.id,
            feedback_status="success",
            actual_value=10.0,
            metadata_json={},
        )
        session.add(feedback)
        session.flush()
        feedback_id = feedback.id

    with get_session(database_url) as session:
        row = session.scalar(select(PredictionFeedback).where(PredictionFeedback.id == feedback_id))

    assert row is not None
    assert row.feedback_status == "success"


def test_feedback_store_writes_human_feedback_and_experience(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    _upgrade_head(database_url)

    with get_session(database_url) as session:
        prediction = _add_prediction(session)
        result = FeedbackStore(session).add_feedback(
            FeedbackInput(
                prediction_id=prediction.id,
                feedback_status="failure",
                actual_value=14.0,
                actual_outbreak_label=True,
            )
        )
        feedback = session.scalar(select(HumanFeedback).where(HumanFeedback.id == result["feedback_id"]))
        experience = session.scalar(
            select(ModelExperienceBank).where(ModelExperienceBank.feedback_id == result["feedback_id"])
        )
        summary = ExperienceBankStore(session).summarize_experiences()

    assert feedback is not None
    assert feedback.__table__.name == "human_feedback"
    assert experience is not None
    assert experience.feedback_id == feedback.id
    assert summary["experience_count"] == 1
