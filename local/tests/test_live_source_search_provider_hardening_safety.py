from __future__ import annotations

import json

from live_source_search_orchestrator.report import write_json


FORBIDDEN = [
    "password",
    "token",
    "secret",
    "ssh_key_path",
    "response_body",
    "raw_text",
    "metadata_json",
    "feature_json",
    "provenance_json",
    "lesson_json",
]


def test_hardened_report_sanitizer_drops_raw_and_sensitive_fields(tmp_path):
    path = tmp_path / "safe.json"
    write_json(
        path,
        {
            "provider_failure_diagnostics": [
                {
                    "provider": "data_gov",
                    "response_body": "<html>full body</html>",
                    "password": 'REDACTED',
                    "sanitized_error_summary": "safe summary",
                }
            ],
            "positive_yield_validation": {"achieved_positive_yield": False},
        },
    )

    rendered = path.read_text(encoding="utf-8").lower()

    assert "safe summary" in rendered
    assert "<html>full body</html>" not in rendered
    for term in FORBIDDEN:
        assert term not in rendered
