from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY = "configs/live_source_discovery_policy.example.json"


def load_live_discovery_policy(path: str | None = None) -> dict:
    policy_path = Path(path or DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY)
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("live discovery policy must be a JSON object.")
    return payload


def validate_live_discovery_policy(policy: dict) -> list[str]:
    errors: list[str] = []
    providers = policy.get("providers")
    if not isinstance(providers, list) or not providers:
        return ["policy.providers must be a non-empty list."]
    seen: set[str] = set()
    for index, entry in enumerate(providers):
        if not isinstance(entry, dict):
            errors.append(f"providers[{index}] must be an object.")
            continue
        provider = str(entry.get("provider") or "").strip().lower()
        if not provider:
            errors.append(f"providers[{index}] missing provider.")
            continue
        if provider in seen:
            errors.append(f"duplicate provider policy: {provider}.")
        seen.add(provider)
        if entry.get("live_discovery_default_enabled") is not False:
            errors.append(f"{provider}: live discovery must be disabled by default.")
        if entry.get("follow_links_allowed") is not False:
            errors.append(f"{provider}: follow links must be disabled in readiness policy.")
        if entry.get("free_web_search_allowed") is not False:
            errors.append(f"{provider}: free web search must be disabled in readiness policy.")
        if int(entry.get("max_requests_per_run", 9999)) > 3:
            errors.append(f"{provider}: max_requests_per_run must be <= 3.")
        allowed_domains = entry.get("allowed_domains")
        if not isinstance(allowed_domains, list) or not allowed_domains:
            errors.append(f"{provider}: allowed_domains must be configured.")
        rendered = json.dumps(entry, ensure_ascii=False).lower()
        if any(marker in rendered for marker in ("password", "token", "secret", "credential", "private")):
            errors.append(f"{provider}: policy must not contain credentials or secrets.")
    return errors


def decide_live_discovery_access(
    *,
    provider: str,
    policy: dict,
    allow_network: bool,
    manual_reviewed: bool,
    terms_reviewed: bool,
    max_requests: int,
) -> dict:
    provider_policy = _provider_policy(policy, provider)
    if provider_policy is None:
        return _decision(False, "provider not found in live discovery policy", provider=provider)
    warnings: list[str] = []
    if provider_policy.get("free_web_search_allowed") is not False:
        warnings.append("free web search is not explicitly disabled.")
    if provider_policy.get("follow_links_allowed") is not False:
        warnings.append("follow links are not explicitly disabled.")
    if not allow_network:
        return _decision(False, "allow_network flag is required", provider=provider, warnings=warnings, policy=provider_policy)
    if provider_policy.get("requires_allow_network_flag") is True and not allow_network:
        return _decision(False, "allow_network flag is required by policy", provider=provider, warnings=warnings, policy=provider_policy)
    if provider_policy.get("manual_review_required") is True and not manual_reviewed:
        return _decision(False, "manual review is required", provider=provider, warnings=warnings, policy=provider_policy)
    if provider_policy.get("terms_review_required") is True and not terms_reviewed:
        return _decision(False, "terms review is required", provider=provider, warnings=warnings, policy=provider_policy)
    max_allowed = int(provider_policy.get("max_requests_per_run", 0))
    if int(max_requests) > max_allowed:
        return _decision(False, "max_requests exceeds provider policy", provider=provider, warnings=warnings, policy=provider_policy)
    if provider_policy.get("free_web_search_allowed") is not False:
        return _decision(False, "generic free web search is not allowed", provider=provider, warnings=warnings, policy=provider_policy)
    if provider_policy.get("follow_links_allowed") is not False:
        return _decision(False, "following links is not allowed", provider=provider, warnings=warnings, policy=provider_policy)
    return _decision(True, "all live discovery access conditions satisfied", provider=provider, warnings=warnings, policy=provider_policy)


def _provider_policy(policy: dict, provider: str) -> dict | None:
    target = str(provider or "").strip().lower()
    for entry in policy.get("providers", []):
        if isinstance(entry, dict) and str(entry.get("provider") or "").strip().lower() == target:
            return entry
    return None


def _decision(
    allowed: bool,
    reason: str,
    *,
    provider: str,
    warnings: list[str] | None = None,
    policy: dict | None = None,
) -> dict:
    provider_policy = policy or {}
    return {
        "allowed": bool(allowed),
        "reason": reason,
        "provider": str(provider),
        "warnings": list(warnings or []),
        "policy_summary": {
            "provider": provider_policy.get("provider", provider),
            "live_discovery_default_enabled": provider_policy.get("live_discovery_default_enabled"),
            "requires_allow_network_flag": provider_policy.get("requires_allow_network_flag"),
            "manual_review_required": provider_policy.get("manual_review_required"),
            "terms_review_required": provider_policy.get("terms_review_required"),
            "max_requests_per_run": provider_policy.get("max_requests_per_run"),
            "follow_links_allowed": provider_policy.get("follow_links_allowed"),
            "free_web_search_allowed": provider_policy.get("free_web_search_allowed"),
            "allowed_domains_count": len(provider_policy.get("allowed_domains", []) or []),
        },
    }
