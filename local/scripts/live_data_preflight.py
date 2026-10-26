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

from crawler_agent.access_policy import SourceAccessPolicy, decide_live_access, load_access_policies

DEFAULT_POLICY = "configs/source_access_policy.example.json"
CDC_NOROSTAT_REVIEW_URL = "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local live-data preflight checks without network access.")
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    payload = run_preflight(source_key=args.source_key, policy_path=args.policy)
    print(json.dumps(_safe_json(payload), ensure_ascii=False, sort_keys=True))
    return 0 if payload["status"] == "ok" else 1


def run_preflight(
    *,
    source_key: str = "cdc_norostat",
    policy_path: str = DEFAULT_POLICY,
) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        policies = load_access_policies(policy_path)
    except Exception as exc:
        return {
            "status": "failed",
            "source_key": source_key,
            "checks": {"policy_loaded": False},
            "guardrail_denies_without_allow_network": False,
            "guardrail_allows_when_all_conditions_met": False,
            "warnings": [_safe_text(f"policy load failed: {type(exc).__name__}")],
        }

    policy = policies.get(source_key)
    checks = _policy_checks(policy, source_key=source_key)
    if not policy:
        warnings.append("source access policy was not found.")
        return {
            "status": "failed",
            "source_key": source_key,
            "checks": checks,
            "guardrail_denies_without_allow_network": False,
            "guardrail_allows_when_all_conditions_met": False,
            "warnings": warnings,
        }

    denied = decide_live_access(
        policy,
        allow_network=False,
        requested_url=_review_url_for_source(source_key),
        robots_allowed=True,
        terms_reviewed=True,
        manual_reviewed=True,
        request_count=1,
    )
    allowed = decide_live_access(
        policy,
        allow_network=True,
        requested_url=_review_url_for_source(source_key),
        robots_allowed=True,
        terms_reviewed=True,
        manual_reviewed=True,
        request_count=1,
    )
    guardrail_denies_without_allow_network = not denied.allowed
    guardrail_allows_when_all_conditions_met = bool(allowed.allowed)
    if not guardrail_denies_without_allow_network:
        warnings.append("guardrail did not deny when allow_network was false.")
    if not guardrail_allows_when_all_conditions_met:
        warnings.append("guardrail did not allow when all reviewed conditions were true.")

    status = "ok" if all(checks.values()) and not warnings else "failed"
    return {
        "status": status,
        "source_key": source_key,
        "checks": checks,
        "guardrail_denies_without_allow_network": guardrail_denies_without_allow_network,
        "guardrail_allows_when_all_conditions_met": guardrail_allows_when_all_conditions_met,
        "warnings": warnings,
        "network_accessed": False,
        "live_execution_enabled": False,
    }


def _policy_checks(policy: SourceAccessPolicy | None, *, source_key: str) -> dict[str, bool]:
    if policy is None:
        return {
            "policy_exists": False,
            "default_live_disabled": False,
            "requires_allow_network": False,
            "robots_required": False,
            "terms_required": False,
            "manual_review_required": False,
            "max_requests_lte_3": False,
            "allowed_domains_configured": False,
            "cdc_domain_allowed": False if source_key == "cdc_norostat" else True,
        }
    allowed_domains = {domain.lower() for domain in policy.allowed_domains}
    cdc_domain_allowed = (
        {"cdc.gov", "www.cdc.gov"} & allowed_domains
        if source_key == "cdc_norostat"
        else allowed_domains
    )
    return {
        "policy_exists": True,
        "default_live_disabled": policy.live_fetch_default_enabled is False,
        "requires_allow_network": policy.requires_allow_network_flag is True,
        "robots_required": policy.robots_check_required is True,
        "terms_required": policy.terms_review_required is True,
        "manual_review_required": policy.manual_review_required is True,
        "max_requests_lte_3": int(policy.max_requests_per_run) <= 3,
        "allowed_domains_configured": bool(policy.allowed_domains),
        "cdc_domain_allowed": bool(cdc_domain_allowed),
    }


def _review_url_for_source(source_key: str) -> str:
    if source_key == "cdc_norostat":
        return CDC_NOROSTAT_REVIEW_URL
    return "https://example.invalid/reviewed-placeholder"


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value]
    if isinstance(value, str):
        return _safe_text(value)
    return value


def _safe_text(value: str) -> str:
    text = str(value)
    lowered = text.lower()
    if any(marker in lowered for marker in ("password", "token", "secret", "ssh_key_path", "private key")):
        return "[redacted]"
    return text


if __name__ == "__main__":
    raise SystemExit(main())
