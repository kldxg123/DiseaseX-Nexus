from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = PROJECT_ROOT / "packages"
for candidate in (PROJECT_ROOT, PACKAGES_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from source_discovery_agent.live_guard import (
    DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY,
    decide_live_discovery_access,
    load_live_discovery_policy,
    validate_live_discovery_policy,
)
from source_discovery_agent.live_providers import list_live_discovery_providers


def run_preflight(
    *,
    provider: str,
    country: str,
    disease: str,
    policy_path: str = DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY,
    max_requests: int = 1,
) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        policy = load_live_discovery_policy(policy_path)
    except Exception as exc:
        return {
            "status": "failed",
            "provider": provider,
            "country": country,
            "disease": disease,
            "policy_loaded": False,
            "warnings": [f"policy load failed: {type(exc).__name__}"],
            "network_accessed": False,
        }
    policy_errors = validate_live_discovery_policy(policy)
    providers = list_live_discovery_providers()
    provider_policy = _provider_policy(policy, provider)
    provider_exists = provider in providers and provider_policy is not None
    if policy_errors:
        warnings.extend(policy_errors)
    if not provider_exists:
        warnings.append("provider is not available in registry or policy.")
    decision_without = decide_live_discovery_access(
        provider=provider,
        policy=policy,
        allow_network=False,
        manual_reviewed=True,
        terms_reviewed=True,
        max_requests=max_requests,
    )
    decision_with_all = decide_live_discovery_access(
        provider=provider,
        policy=policy,
        allow_network=True,
        manual_reviewed=True,
        terms_reviewed=True,
        max_requests=max_requests,
    )
    checks = {
        "policy_loaded": True,
        "provider_exists": provider_exists,
        "default_live_disabled": provider_policy.get("live_discovery_default_enabled") is False
        if provider_policy
        else False,
        "requires_allow_network": provider_policy.get("requires_allow_network_flag") is True
        if provider_policy
        else False,
        "max_requests_lte_policy": int(max_requests) <= int(provider_policy.get("max_requests_per_run", 0))
        if provider_policy
        else False,
        "free_web_search_allowed": bool(provider_policy.get("free_web_search_allowed"))
        if provider_policy
        else False,
        "follow_links_allowed": bool(provider_policy.get("follow_links_allowed")) if provider_policy else False,
        "decision_without_allow_network": bool(decision_without.get("allowed")),
        "decision_with_all_conditions": bool(decision_with_all.get("allowed")),
    }
    status = "ok" if provider_exists and not policy_errors and not checks["decision_without_allow_network"] and checks["decision_with_all_conditions"] else "failed"
    return _safe_json(
        {
            "status": status,
            "provider": provider,
            "country": country,
            "disease": disease,
            "default_live_disabled": checks["default_live_disabled"],
            "requires_allow_network": checks["requires_allow_network"],
            "max_requests_lte_policy": checks["max_requests_lte_policy"],
            "free_web_search_allowed": checks["free_web_search_allowed"],
            "follow_links_allowed": checks["follow_links_allowed"],
            "decision_without_allow_network": checks["decision_without_allow_network"],
            "decision_with_all_conditions": checks["decision_with_all_conditions"],
            "policy_errors": policy_errors,
            "warnings": warnings,
            "network_accessed": False,
            "live_execution_enabled": False,
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run live source discovery preflight without network access.")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--disease", required=True)
    parser.add_argument("--policy", default=DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY)
    parser.add_argument("--max-requests", type=int, default=1)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = run_preflight(
        provider=args.provider,
        country=args.country,
        disease=args.disease,
        policy_path=args.policy,
        max_requests=args.max_requests,
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload.get("status") == "ok" else 1


def _provider_policy(policy: dict, provider: str) -> dict | None:
    target = str(provider or "").strip().lower()
    for entry in policy.get("providers", []):
        if isinstance(entry, dict) and str(entry.get("provider") or "").strip().lower() == target:
            return entry
    return None


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in ("password", "token", "secret", "credential", "private", "ssh")):
            return "[redacted]"
        return value[:300]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
