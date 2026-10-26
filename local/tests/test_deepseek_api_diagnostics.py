from __future__ import annotations

import json

from deepseek_web_search_agent.deepseek_client import DeepSeekCallResult
from deepseek_web_search_agent.deepseek_diagnostics import (
    classify_deepseek_http_error,
    diagnose_deepseek_config,
    run_deepseek_health_check,
    suggested_fix_for_failure,
)


class _FakeDeepSeekClient:
    def __init__(self, result: DeepSeekCallResult) -> None:
        self.result = result

    def call_chat_json(self, **_kwargs) -> DeepSeekCallResult:
        return self.result


def test_diagnose_config_reports_presence_without_secret_value(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-placeholder")

    summary = diagnose_deepseek_config()
    rendered = json.dumps(summary, ensure_ascii=False)

    assert summary["api_key_present"] is True
    assert "unit-test-placeholder" not in rendered
    assert summary["retry_allowed"] is False


def test_health_check_skips_without_explicit_allow(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-placeholder")

    result = run_deepseek_health_check(allow_deepseek_api=False)

    assert result["health_check_status"] == "skipped"
    assert result["failure_type"] == "not_authorized"
    assert result["deepseek_call_attempted"] is False


def test_health_check_uses_safe_client_result(monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-placeholder")
    fake = _FakeDeepSeekClient(
        DeepSeekCallResult(
            parsed_json={"ok": True},
            prompt_hash="hash",
            completion_summary="ok",
            called=True,
            warnings=[],
        )
    )

    result = run_deepseek_health_check(allow_deepseek_api=True, client=fake)

    assert result["health_check_status"] == "ok"
    assert result["deepseek_call_attempted"] is True
    assert result["failure_type"] is None


def test_deepseek_error_classifier_and_fix_are_safe() -> None:
    classified = classify_deepseek_http_error({"status_code": 429, "message": "rate limited"})

    assert classified["failure_type"] == "rate_limited"
    assert classified["retry_allowed"] is False
    assert suggested_fix_for_failure("invalid_payload")

