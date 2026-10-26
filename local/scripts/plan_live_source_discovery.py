from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = PROJECT_ROOT / "packages"
for candidate in (PROJECT_ROOT, PACKAGES_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from scripts.live_source_discovery_preflight import run_preflight
from source_discovery_agent.live_guard import DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY, load_live_discovery_policy
from source_discovery_agent.live_providers.gdelt_provider import build_gdelt_query


def build_live_source_discovery_plan(
    *,
    provider: str,
    country: str,
    disease: str,
    policy_path: str = DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY,
    max_requests: int = 1,
) -> dict[str, Any]:
    preflight = run_preflight(
        provider=provider,
        country=country,
        disease=disease,
        policy_path=policy_path,
        max_requests=max_requests,
    )
    policy = load_live_discovery_policy(policy_path)
    provider_policy = _provider_policy(policy, provider) or {}
    query_candidates = _query_candidates(provider=provider, country=country, disease=disease)
    return _safe_json(
        {
            "provider": provider,
            "country": country,
            "disease": disease,
            "query_candidates": query_candidates,
            "max_requests": max(1, min(int(max_requests), int(provider_policy.get("max_requests_per_run", max_requests) or 1))),
            "policy_summary": {
                "default_live_disabled": provider_policy.get("live_discovery_default_enabled"),
                "requires_allow_network": provider_policy.get("requires_allow_network_flag"),
                "manual_review_required": provider_policy.get("manual_review_required"),
                "terms_review_required": provider_policy.get("terms_review_required"),
                "free_web_search_allowed": provider_policy.get("free_web_search_allowed"),
                "follow_links_allowed": provider_policy.get("follow_links_allowed"),
                "allowed_domains_count": len(provider_policy.get("allowed_domains", []) or []),
            },
            "preflight": {
                "status": preflight.get("status"),
                "decision_without_allow_network": preflight.get("decision_without_allow_network"),
                "decision_with_all_conditions": preflight.get("decision_with_all_conditions"),
                "network_accessed": False,
            },
            "future_command_template": (
                "python scripts/run_controlled_live_source_discovery.py "
                f"--provider {provider} --country \"{country}\" --disease \"{disease}\" "
                "--allow-network --terms-reviewed --manual-reviewed --max-requests 1"
            ),
            "expected_outputs": [
                "source discovery provider summary",
                "candidate source rows stored as pending_review",
                "connector spec suggestions for analyst review",
                "safe source discovery report",
            ],
            "rollback_steps": [
                "Delete readiness output directory if plan is discarded.",
                "Delete temporary SQLite database used for test runs.",
                "Remove only pending_review candidates associated with the approved run after human confirmation.",
                "Confirm no production database or production connector was modified.",
            ],
            "acceptance_criteria": [
                "request_count_within_policy",
                "provider_endpoint_allowlisted",
                "candidates_pending_review",
                "no_sensitive_output",
                "no_source_auto_activation",
                "no_production_prediction",
            ],
            "current_step_executes_live_discovery": False,
            "network_accessed": False,
            "live_execution_enabled": False,
        }
    )


def write_live_source_discovery_plan(
    *,
    provider: str,
    country: str,
    disease: str,
    policy_path: str = DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY,
    output_dir: str = "reports/source_discovery/live_readiness",
    max_requests: int = 1,
) -> dict[str, Any]:
    plan = build_live_source_discovery_plan(
        provider=provider,
        country=country,
        disease=disease,
        policy_path=policy_path,
        max_requests=max_requests,
    )
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"live_source_discovery_plan_{timestamp}"
    json_path = directory / f"{prefix}.json"
    markdown_path = directory / f"{prefix}.md"
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_plan_markdown(plan), encoding="utf-8")
    return {
        "status": "ok" if plan["preflight"]["status"] == "ok" else "failed",
        "plan_json": str(json_path),
        "plan_markdown": str(markdown_path),
        "provider": provider,
        "country": country,
        "disease": disease,
        "max_requests": plan["max_requests"],
        "network_accessed": False,
        "live_execution_enabled": False,
        "warnings": [] if plan["preflight"]["status"] == "ok" else ["preflight did not pass"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan controlled live source discovery without executing it.")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--disease", required=True)
    parser.add_argument("--policy", default=DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY)
    parser.add_argument("--output-dir", default="reports/source_discovery/live_readiness")
    parser.add_argument("--max-requests", type=int, default=1)
    args = parser.parse_args(argv)
    result = write_live_source_discovery_plan(
        provider=args.provider,
        country=args.country,
        disease=args.disease,
        policy_path=args.policy,
        output_dir=args.output_dir,
        max_requests=args.max_requests,
    )
    print(json.dumps(_safe_json(result), ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


def _query_candidates(*, provider: str, country: str, disease: str) -> list[str]:
    if provider == "gdelt":
        return [build_gdelt_query(country, disease)]
    return [f"{country} {disease} official surveillance data"]


def _provider_policy(policy: dict, provider: str) -> dict | None:
    for entry in policy.get("providers", []):
        if isinstance(entry, dict) and str(entry.get("provider") or "").strip().lower() == provider:
            return entry
    return None


def _render_plan_markdown(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Controlled Live Source Discovery Plan",
            "",
            "This plan is not executed and does not access the public internet.",
            "",
            "## Scope",
            f"- Provider: `{plan['provider']}`",
            f"- Country: {plan['country']}",
            f"- Disease: {plan['disease']}",
            f"- Max requests: {plan['max_requests']}",
            "",
            "## Query Candidates",
            "\n".join(f"- {item}" for item in plan["query_candidates"]),
            "",
            "## Policy Summary",
            "```json",
            json.dumps(plan["policy_summary"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Future Command Template",
            "For a later explicitly approved live discovery step only:",
            "```bash",
            plan["future_command_template"],
            "```",
            "",
            "## Expected Outputs",
            "\n".join(f"- {item}" for item in plan["expected_outputs"]),
            "",
            "## Rollback Steps",
            "\n".join(f"- {item}" for item in plan["rollback_steps"]),
            "",
            "## Acceptance Criteria",
            "\n".join(f"- {item}" for item in plan["acceptance_criteria"]),
            "",
            "## Boundary",
            "All discovered sources must remain pending_review. No live execution occurs in this readiness step.",
            "",
        ]
    )


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
        return value[:500]
    return value


if __name__ == "__main__":
    raise SystemExit(main())
