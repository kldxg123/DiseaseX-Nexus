from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

from deepseek_web_search_agent.config import DeepSeekWebSearchConfig, get_config


@dataclass(frozen=True)
class DeepSeekCallResult:
    parsed_json: dict[str, Any]
    prompt_hash: str
    completion_summary: str
    called: bool
    warnings: list[str]
    http_status_code: int | None = None
    failure_type: str | None = None
    json_mode: str = "plain_text_json"


class DeepSeekClient:
    def __init__(
        self,
        config: DeepSeekWebSearchConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or get_config()
        self.transport = transport

    def call_chat_json(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        allow_deepseek_api: bool = False,
        fallback_json: dict[str, Any] | None = None,
        json_mode: str = "plain_text_json",
    ) -> DeepSeekCallResult:
        prompt_hash = _hash_payload({"system": system_prompt, "user": user_payload})
        normalized_json_mode = _normalize_json_mode(json_mode)
        if not allow_deepseek_api:
            return DeepSeekCallResult(
                parsed_json=fallback_json or {},
                prompt_hash=prompt_hash,
                completion_summary="DeepSeek API not called because allow_deepseek_api is false.",
                called=False,
                warnings=["deepseek_api_skipped_not_authorized"],
                failure_type="not_authorized",
                json_mode=normalized_json_mode,
            )
        if not self.config.deepseek_api_key:
            return DeepSeekCallResult(
                parsed_json=fallback_json or {},
                prompt_hash=prompt_hash,
                completion_summary="DeepSeek API not called because API key is unavailable.",
                called=False,
                warnings=["deepseek_api_skipped_missing_key"],
                failure_type="missing_api_key",
                json_mode=normalized_json_mode,
            )
        payload = {
            "model": self.config.deepseek_model,
            "messages": [
                {"role": "system", "content": _system_prompt_for_mode(system_prompt, normalized_json_mode)},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
        }
        if normalized_json_mode == "strict_json":
            payload["response_format"] = {"type": "json_object"}
        try:
            with httpx.Client(
                base_url=self.config.deepseek_base_url.rstrip("/"),
                timeout=self.config.deepseek_timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.post(
                    "/chat/completions",
                    headers={"Authorization": f"Bearer {self.config.deepseek_api_key}"},
                    json=payload,
                )
            if response.status_code >= 400:
                failure_type = classify_http_status(response.status_code)
                return DeepSeekCallResult(
                    parsed_json=fallback_json or {},
                    prompt_hash=prompt_hash,
                    completion_summary=f"DeepSeek API returned HTTP {response.status_code}.",
                    called=True,
                    warnings=["deepseek_api_http_error"],
                    http_status_code=response.status_code,
                    failure_type=failure_type,
                    json_mode=normalized_json_mode,
                )
            content = _extract_message_content(response.json())
            parsed = parse_json_or_fallback(content, fallback_json or {})
            parse_failed = not _json_text_contains_object(content)
            return DeepSeekCallResult(
                parsed_json=parsed,
                prompt_hash=prompt_hash,
                completion_summary=_safe_summary(content),
                called=True,
                warnings=[] if not parse_failed else ["deepseek_json_parse_error"],
                failure_type="parse_error" if parse_failed else None,
                json_mode=normalized_json_mode,
            )
        except httpx.TimeoutException:
            return DeepSeekCallResult(
                parsed_json=fallback_json or {},
                prompt_hash=prompt_hash,
                completion_summary="DeepSeek API timed out.",
                called=True,
                warnings=["deepseek_api_timeout"],
                failure_type="timeout",
                json_mode=normalized_json_mode,
            )
        except Exception:
            return DeepSeekCallResult(
                parsed_json=fallback_json or {},
                prompt_hash=prompt_hash,
                completion_summary="DeepSeek API call failed safely.",
                called=True,
                warnings=["deepseek_api_failed"],
                failure_type="unknown_http_error",
                json_mode=normalized_json_mode,
            )

    def call_with_search_context(
        self,
        *,
        task: str,
        context: dict[str, Any],
        allow_deepseek_api: bool = False,
        fallback_json: dict[str, Any] | None = None,
    ) -> DeepSeekCallResult:
        return self.call_chat_json(
            system_prompt=(
                "Return only compact JSON. Do not include secrets, raw HTML, full prompts, "
                "or unsupported numeric claims."
            ),
            user_payload={"task": task, "context": context},
            allow_deepseek_api=allow_deepseek_api,
            fallback_json=fallback_json,
            json_mode="plain_text_json",
        )


def parse_json_or_fallback(text: str | None, fallback: dict[str, Any]) -> dict[str, Any]:
    if not text:
        return fallback
    text = _extract_json_object_text(text)
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else fallback
    except json.JSONDecodeError:
        return fallback


def classify_http_status(status_code: int, *, error_summary: str | None = None) -> str:
    summary = str(error_summary or "").lower()
    if status_code == 401:
        return "unauthorized"
    if status_code == 403:
        return "forbidden"
    if status_code == 429:
        return "rate_limited" if "quota" not in summary and "billing" not in summary else "quota_or_billing"
    if 500 <= status_code <= 599:
        return "server_error"
    if status_code == 400:
        if "model" in summary:
            return "model_not_available"
        return "invalid_payload"
    if status_code == 402:
        return "quota_or_billing"
    if status_code == 404:
        return "model_not_available"
    return "unknown_http_error"


def _extract_message_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    message = choices[0].get("message") if isinstance(choices[0], dict) else {}
    content = message.get("content") if isinstance(message, dict) else ""
    return str(content or "")


def _safe_summary(text: str) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) <= 240:
        return compact
    return compact[:237] + "..."


def _normalize_json_mode(value: str | None) -> str:
    normalized = str(value or "plain_text_json").strip().lower()
    if normalized in {"strict", "strict_json", "response_format"}:
        return "strict_json"
    return "plain_text_json"


def _system_prompt_for_mode(system_prompt: str, json_mode: str) -> str:
    if json_mode == "strict_json":
        return system_prompt
    return (
        f"{system_prompt}\n"
        "Return a single valid JSON object as plain text. Do not wrap it in markdown fences."
    )


def _extract_json_object_text(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return raw[start : end + 1]
    return raw


def _json_text_contains_object(text: str | None) -> bool:
    if not text:
        return False
    payload = _extract_json_object_text(text)
    try:
        return isinstance(json.loads(payload), dict)
    except json.JSONDecodeError:
        return False


def _hash_payload(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
