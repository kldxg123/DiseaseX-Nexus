from __future__ import annotations

from typing import Any

from deepseek_web_search_agent.config import DeepSeekWebSearchConfig, get_config
from deepseek_web_search_agent.deepseek_client import DeepSeekClient, classify_http_status


SUGGESTED_FIXES = {
    "missing_api_key": 'REDACTED',
    "unauthorized": "Check whether the local DeepSeek API key is valid and active.",
    "forbidden": "Check account permissions, endpoint access, and regional/API policy restrictions.",
    "rate_limited": "Wait for cooldown or reduce request rate; do not retry automatically in this workflow.",
    "quota_or_billing": "Check DeepSeek account quota or billing status.",
    "model_not_available": "Check DEEPSEEK_MODEL and whether the account can access that model.",
    "invalid_payload": "Use plain-text JSON mode or simplify the request payload.",
    "timeout": "Increase timeout or retry later in a separately approved run.",
    "server_error": "Provider/server-side issue; retry later in a separately approved run.",
    "parse_error": "Model response was not valid JSON; keep fallback extraction and simplify schema.",
    "unknown_http_error": "Inspect provider dashboard/status without exposing response bodies.",
    "not_authorized": "Pass explicit allow_deepseek_api only after manual approval.",
}


def diagnose_deepseek_config(config: DeepSeekWebSearchConfig | None = None) -> dict[str, Any]:
    cfg = config or get_config()
    return {
        "api_key_present": bool(cfg.deepseek_api_key),
        "base_url_configured": bool(str(cfg.deepseek_base_url or "").strip()),
        "model_name": cfg.deepseek_model,
        "timeout_seconds": cfg.deepseek_timeout_seconds,
        "can_attempt_live_call": bool(cfg.deepseek_api_key and cfg.deepseek_base_url and cfg.deepseek_model),
        "retry_allowed": False,
    }


def run_deepseek_health_check(
    *,
    allow_deepseek_api: bool,
    timeout_seconds: float = 20.0,
    client: DeepSeekClient | None = None,
) -> dict[str, Any]:
    cfg = get_config()
    if timeout_seconds:
        cfg = DeepSeekWebSearchConfig(
            deepseek_api_key=cfg.deepseek_api_key,
            deepseek_base_url=cfg.deepseek_base_url,
            deepseek_model=cfg.deepseek_model,
            deepseek_timeout_seconds=float(timeout_seconds),
            general_web_search_provider=cfg.general_web_search_provider,
            provider_api_keys=cfg.provider_api_keys,
            max_search_queries=cfg.max_search_queries,
            max_search_results_per_query=cfg.max_search_results_per_query,
            max_pages_to_read=cfg.max_pages_to_read,
            max_total_http_requests=cfg.max_total_http_requests,
            max_readable_pages_success=cfg.max_readable_pages_success,
            stop_when_structured_candidates_found=cfg.stop_when_structured_candidates_found,
            prefer_official_and_structured_sources=cfg.prefer_official_and_structured_sources,
        )
    client = client or DeepSeekClient(cfg)
    config_summary = diagnose_deepseek_config(cfg)
    if not allow_deepseek_api:
        return _diagnostic_result(
            config_summary,
            status="skipped",
            failure_type="not_authorized",
            http_status_code=None,
            called=False,
        )
    if not cfg.deepseek_api_key:
        return _diagnostic_result(
            config_summary,
            status="skipped",
            failure_type="missing_api_key",
            http_status_code=None,
            called=False,
        )
    result = client.call_chat_json(
        system_prompt="Return compact JSON only.",
        user_payload={"task": "health_check", "expected": {"ok": True}},
        allow_deepseek_api=True,
        fallback_json={},
        json_mode="plain_text_json",
    )
    failure_type = result.failure_type
    status = "ok" if result.called and not failure_type else "failed"
    if result.called and failure_type == "parse_error":
        status = "failed"
    return _diagnostic_result(
        config_summary,
        status=status,
        failure_type=failure_type,
        http_status_code=result.http_status_code,
        called=result.called,
        json_mode=result.json_mode,
        warnings=result.warnings,
    )


def classify_deepseek_http_error(error: Any) -> dict[str, Any]:
    status_code: int | None = None
    summary = None
    if isinstance(error, int):
        status_code = error
    elif isinstance(error, dict):
        raw_status = error.get("status_code") or error.get("http_status_code")
        status_code = int(raw_status) if raw_status is not None else None
        summary = error.get("summary") or error.get("message")
    else:
        raw_status = getattr(error, "status_code", None)
        status_code = int(raw_status) if raw_status is not None else None
        summary = str(error.__class__.__name__)
    failure_type = classify_http_status(status_code, error_summary=summary) if status_code else "unknown_http_error"
    return {
        "http_status_code": status_code,
        "failure_type": failure_type,
        "retry_allowed": False,
        "suggested_fix": SUGGESTED_FIXES.get(failure_type, SUGGESTED_FIXES["unknown_http_error"]),
    }


def suggested_fix_for_failure(failure_type: str | None) -> str:
    return SUGGESTED_FIXES.get(str(failure_type or "unknown_http_error"), SUGGESTED_FIXES["unknown_http_error"])


def _diagnostic_result(
    config_summary: dict[str, Any],
    *,
    status: str,
    failure_type: str | None,
    http_status_code: int | None,
    called: bool,
    json_mode: str | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        **config_summary,
        "deepseek_call_attempted": bool(called),
        "health_check_status": status,
        "http_status_code": http_status_code,
        "failure_type": failure_type,
        "retry_allowed": False,
        "suggested_fix": suggested_fix_for_failure(failure_type) if failure_type else None,
        "json_mode": json_mode,
        "warnings": list(warnings or []),
    }
