from __future__ import annotations

import json

from tests.test_deepseek_structured_extraction_replay import make_safe_external_run_dir


def test_deepseek_diagnose_api_is_safe(api_client) -> None:
    response = api_client.post(
        "/deepseek-web-search/diagnose-api",
        json={"allow_deepseek_api": False, "timeout_seconds": 5.0},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "skipped"
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    assert "unit-test-placeholder" not in rendered
    assert "authorization" not in rendered
    assert "system_prompt" not in rendered
    assert "user_payload" not in rendered
    assert "completion_summary" not in rendered


def test_deepseek_replay_api_and_diagnostics_endpoint(api_client, tmp_path) -> None:
    run_dir = make_safe_external_run_dir(tmp_path)
    replay_response = api_client.post(
        "/deepseek-web-search/replay-extraction",
        json={
            "run_dir": str(run_dir),
            "allow_deepseek_api": False,
            "output_dir": str(tmp_path / "reports"),
        },
    )

    assert replay_response.status_code == 200
    summary = replay_response.json()["summary"]
    run_id = summary["run_id"]
    diagnostics_response = api_client.get(f"/deepseek-web-search/diagnostics/{run_id}")

    assert summary["no_tavily_rerun"] is True
    assert summary["page_read_request_count"] == 0
    assert diagnostics_response.status_code == 200
    assert diagnostics_response.json()["records"] == 1
