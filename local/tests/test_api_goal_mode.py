from __future__ import annotations


def test_api_goal_mode_mock_returns_safe_summary(api_client) -> None:
    response = api_client.post(
        "/deepseek-web-search/table-review/goal-mode",
        json={
            "targets": "dengue:Global",
            "date_start": "2026-05-01",
            "date_end": "2026-07-01",
            "mock_search": True,
            "mock_deepseek": True,
            "output_dir": "reports/test_api_goal_mode",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    summary = payload["summary"]
    assert summary["network_accessed"] is False
    assert summary["attempts"][0]["review_rows_generated"] >= 1
    text = str(payload).lower()
    assert "prompt" not in text
    assert "completion" not in text
