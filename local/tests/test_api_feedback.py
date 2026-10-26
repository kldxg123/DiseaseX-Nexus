from __future__ import annotations

from common.db import get_session
from common.models import HumanFeedback, ModelExperienceBank
from tests.conftest import add_api_prediction


def test_feedback_endpoints_create_feedback_and_experience(api_client, api_database_url) -> None:
    with get_session(api_database_url) as session:
        prediction = add_api_prediction(session)
        prediction_id = prediction.id

    response = api_client.post(
        "/feedback",
        json={
            "prediction_id": str(prediction_id),
            "reviewer": "reviewer-fixture",
            "verdict": "failure",
            "true_cases": 14,
            "true_outbreak_label": True,
            "reason": "under_prediction",
            "notes": "do not echo this full analyst note",
        },
    )

    assert response.status_code == 200
    assert response.json()["summary"]["notes_present"] is True
    assert "analyst note" not in str(response.json())
    feedback_response = api_client.get("/feedback?verdict=failure")
    feedback_id = feedback_response.json()["feedback"][0]["id"]
    build_response = api_client.post("/experience/build", json={"feedback_id": str(feedback_id)})
    summary_response = api_client.get("/feedback/summary")
    experiences_response = api_client.get("/experiences?failure_type=under_prediction")
    experience_summary_response = api_client.get("/experiences/summary")
    singular_experience_summary_response = api_client.get("/experience/summary")

    assert feedback_response.status_code == 200
    assert feedback_response.json()["count"] == 1
    assert feedback_response.json()["feedback"][0]["notes_present"] is True
    assert build_response.status_code == 200
    assert build_response.json()["summary"]["experience_created_or_existing"] is True
    assert summary_response.json()["feedback_count"] == 1
    assert experiences_response.status_code == 200
    assert experiences_response.json()["count"] == 1
    assert experience_summary_response.json()["experience_count"] == 1
    assert singular_experience_summary_response.json()["experience_count"] == 1
    rendered = str(experiences_response.json()).lower()
    assert "auto_retrain_triggered" in rendered
    assert "lesson_json" not in rendered
    assert "do not echo" not in str(feedback_response.json()).lower()
    with get_session(api_database_url) as session:
        assert session.query(HumanFeedback).count() == 1
        assert session.query(ModelExperienceBank).count() == 1
