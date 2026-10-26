from __future__ import annotations

import json

from scripts.smoke_test import run_smoke_test, sanitize_smoke_output


FORBIDDEN = (
    "服务器信息.txt",
    "ssh_key_path",
    "password",
    "token",
    "secret",
    "private key",
    "<html>",
    "raw_text",
    "raw_html",
    "feature_json",
    "lesson_json",
    "provenance_json",
    "metadata_json",
)


def test_sanitize_smoke_output_removes_sensitive_and_verbose_fields() -> None:
    payload = sanitize_smoke_output(
        {
            "password": 'REDACTED',
            "token": "do-not-show",
            "nested": {
                "html": "<html>do-not-show</html>",
                "raw_text": "do-not-show",
                "feature_json": {"current_week_cases": 1},
                "lesson_json": {"notes": "do-not-show"},
                "provenance_json": {"source": "do-not-show"},
                "metadata_json": {"token": "do-not-show"},
                "safe": "ok",
            },
        }
    )

    rendered = json.dumps(payload, ensure_ascii=False).lower()
    assert payload["nested"]["safe"] == "ok"
    for marker in FORBIDDEN:
        assert marker.lower() not in rendered


def test_smoke_test_output_is_safe() -> None:
    summary = run_smoke_test(include_api=True, include_worker=True)
    rendered = json.dumps(summary, ensure_ascii=False).lower()

    assert summary["tests_passed"] is True
    for marker in FORBIDDEN:
        assert marker.lower() not in rendered
