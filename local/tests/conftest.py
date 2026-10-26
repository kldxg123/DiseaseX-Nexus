from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from apps.api.deps import get_db_session
from apps.api.main import app
from common.db import get_session, init_db
from common.models import (
    EpidemicEvent,
    ModelFeature,
    Prediction,
    RawDocument,
    SourceRegistry,
)


@pytest.fixture
def api_database_url(tmp_path) -> str:
    database_url = f"sqlite:///{tmp_path / 'api-test.db'}"
    init_db(database_url).dispose()
    return database_url


@pytest.fixture
def api_client(api_database_url: str):
    def override_session():
        with get_session(api_database_url) as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def add_api_source(session, *, name: str = "API Fixture Source") -> SourceRegistry:
    source = SourceRegistry(
        name=name,
        base_url=f"https://fixture.invalid/{name.lower().replace(' ', '-')}",
        country_scope=["global"],
        disease_scope=["all"],
        source_type="fixture",
        reliability_score=0.9,
        parser_type="fixture",
        status="active",
        metadata_json={},
    )
    session.add(source)
    session.flush()
    return source


def add_api_raw_document(session, source: SourceRegistry, *, body_text: str | None = None) -> RawDocument:
    body = body_text or "On January 31 2025, United States reported 12 norovirus cases and 1 outbreak."
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    document = RawDocument(
        source_registry_id=source.id,
        original_url=f"https://fixture.invalid/raw/{digest[:8]}",
        normalized_url=f"https://fixture.invalid/raw/{digest[:8]}",
        title="API raw fixture",
        body_text=body,
        content_hash=digest,
        status_code=200,
        content_type="text/plain",
        published_at=datetime(2025, 1, 31, tzinfo=UTC),
        fetched_at=datetime(2025, 1, 31, tzinfo=UTC),
        country_detected="United States",
        disease_detected="norovirus",
        extraction_status="pending",
        metadata_json={},
    )
    session.add(document)
    session.flush()
    return document


def add_api_events(session, *, count: int = 10) -> None:
    source = add_api_source(session, name="API Event Source")
    start = date(2025, 1, 6)
    for index in range(count):
        raw_document = add_api_raw_document(session, source, body_text=f"synthetic event {index}")
        event_date = start + timedelta(days=7 * index)
        session.add(
            EpidemicEvent(
                raw_document_id=raw_document.id,
                country="United States",
                admin1=None,
                admin2=None,
                disease="norovirus",
                event_date=event_date,
                iso_week=f"2025-W{index + 1:02d}",
                cases=index + 1,
                suspected_cases=None,
                confirmed_cases=index + 1,
                deaths=0,
                hospitalizations=0,
                tests=100 + index,
                positivity_rate=None,
                outbreaks_count=1 if index % 3 == 0 else 0,
                setting=None,
                pathogen_genotype=None,
                confidence_score=0.9,
                extraction_method="fixture",
                provenance_json={"fixture": True},
            )
        )
    session.flush()


def add_api_features(session, *, count: int = 12, horizon_days: int = 28) -> None:
    start = date(2025, 1, 6)
    for index in range(count):
        value = float(index + 1)
        session.add(
            ModelFeature(
                country="United States",
                admin1=None,
                disease="norovirus",
                target_date=start + timedelta(days=7 * index),
                iso_week=f"2025-W{index + 1:02d}",
                horizon_days=horizon_days,
                feature_json={
                    "current_week_cases": value,
                    "lag_1w_cases": value,
                    "lag_2w_cases": value,
                    "rolling_4w_mean_cases": value,
                    "data_quality_mean": 1.0,
                    "season_week_sin": 0.0,
                    "season_week_cos": 1.0,
                },
                target_value=value,
                target_outbreak_label=value >= 8,
                split_type="train",
            )
        )
    session.flush()


def add_api_prediction(session) -> Prediction:
    prediction = Prediction(
        country="United States",
        admin1=None,
        disease="norovirus",
        target_date=date(2025, 3, 1),
        iso_week="2025-W08",
        horizon_days=28,
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
