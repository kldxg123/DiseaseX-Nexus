from fastapi.testclient import TestClient

from src.api.server import app, health, predict_trend


def test_health_schema_function():
    result = health()
    assert result["status"] == "ok"
    assert "model_loaded" in result
    assert "baseline_report_ready" in result
    assert "online_model_ready" in result


def test_predict_trend_blocked_schema_without_online_artifact():
    result = predict_trend({"country": "A", "adm1": "X", "date": "2014-01-01", "case_series": []})
    assert result["trend"] is None
    assert result["confidence"] == 0
    assert result["risk_score"] == 0
    assert result["blocked_reason"] in {"online_baseline_artifact_missing", "online_prediction_not_enabled_in_p0a"}
    assert "data_quality_flags" in result


def test_fastapi_testclient_health_and_predict():
    client = TestClient(app)
    health_response = client.get("/health")
    assert health_response.status_code == 200
    assert health_response.json()["status"] == "ok"
    predict_response = client.post(
        "/predict_trend",
        json={
            "country": "A",
            "adm1": "X",
            "date": "2014-01-01",
            "case_series": [{"date": "2014-01-01", "new_cases": 1, "cum_cases": 1}],
        },
    )
    assert predict_response.status_code == 200
    payload = predict_response.json()
    assert payload["trend"] is None or payload["trend"] in {"\u4e0a\u5347", "\u4e0b\u964d", "\u7a33\u5b9a"}
    assert "blocked_reason" in payload
