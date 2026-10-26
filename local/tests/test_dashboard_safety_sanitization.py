from __future__ import annotations

import json

from apps.dashboard.formatters import sanitize_dashboard_payload


def test_dashboard_safety_sanitization_removes_nested_raw_payloads() -> None:
    safe = sanitize_dashboard_payload(
        {
            "status": "ok",
            "paths": {"summary": "reports/example/summary.json"},
            "records": [
                {
                    "record_id": "1",
                    "metadata_json": {"hidden": True},
                    "provenance_json": {"hidden": True},
                    "feature_json": {"hidden": True},
                    "lesson_json": {"hidden": True},
                    "response_body": "hidden",
                    "html": "<html>hidden</html>",
                    "api_key": 'REDACTED',
                    "token": "hidden",
                    "password": 'REDACTED',
                    "secret": 'REDACTED',
                }
            ],
        }
    )

    rendered = json.dumps(safe, ensure_ascii=False).lower()
    assert safe["status"] == "ok"
    assert safe["paths"]["summary"] == "reports/example/summary.json"
    assert safe["records"][0]["record_id"] == "1"
    for forbidden in (
        "metadata_json",
        "provenance_json",
        "feature_json",
        "lesson_json",
        "response_body",
        "<html",
        "api_key",
        "token",
        "password",
        "secret",
        "hidden",
    ):
        assert forbidden not in rendered
