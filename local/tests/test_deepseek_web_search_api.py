from __future__ import annotations

import json


def _payload(tmp_path) -> dict:
    return {
        "country": "United States",
        "disease": "influenza",
        "query_intent": "structured_time_series",
        "search_backend": "mock",
        "max_search_queries": 2,
        "max_search_results_per_query": 3,
        "max_pages_to_read": 0,
        "max_total_http_requests": 4,
        "output_dir": str(tmp_path / "api-reports"),
        "allow_deepseek_api": False,
        "allow_general_web_search": False,
        "allow_page_read": False,
        "terms_reviewed": False,
        "manual_reviewed": False,
    }


def test_deepseek_plan_and_run_api_are_mock_safe(api_client, tmp_path) -> None:
    plan_response = api_client.post("/deepseek-web-search/plan", json=_payload(tmp_path))
    run_response = api_client.post("/deepseek-web-search/run", json=_payload(tmp_path))

    assert plan_response.status_code == 200
    assert run_response.status_code == 200
    plan = plan_response.json()
    summary = run_response.json()["summary"]
    assert plan["status"] == "ok"
    assert summary["status"] == "ok"
    assert summary["search_backend"] == "mock"
    assert summary["deepseek_call_count"] == 0
    assert summary["page_read_request_count"] == 0
    rendered = json.dumps(run_response.json(), ensure_ascii=False).lower()
    assert "api_key" not in rendered
    assert "password" not in rendered
    assert "raw_text" not in rendered
    assert "<html" not in rendered


def test_deepseek_records_cleaning_and_3d_api(api_client, tmp_path) -> None:
    run_response = api_client.post("/deepseek-web-search/run", json=_payload(tmp_path))
    run_id = run_response.json()["summary"]["run_id"]

    records_response = api_client.get(
        "/deepseek-web-search/records",
        params={"country": "United States", "disease": "influenza", "run_id": run_id},
    )
    cleaning_response = api_client.post("/deepseek-web-search/send-to-cleaning", json={"run_id": run_id})
    visual_response = api_client.get(
        "/deepseek-web-search/visualization/3d-data",
        params={"country": "United States", "disease": "influenza"},
    )

    assert records_response.status_code == 200
    assert len(records_response.json()["records"]) > 0
    assert cleaning_response.status_code == 200
    assert "raw_documents_created" in cleaning_response.json()["summary"]
    assert visual_response.status_code == 200
    assert "points" in visual_response.json()
