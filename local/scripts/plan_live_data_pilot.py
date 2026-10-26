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

from scripts.live_data_preflight import DEFAULT_POLICY, run_preflight


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Plan a controlled live-data pilot without executing it.")
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--disease", required=True)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--output-dir", default="reports/live_pilot")
    parser.add_argument("--max-requests", type=int, default=1)
    args = parser.parse_args(argv)

    result = write_live_pilot_plan(
        source_key=args.source_key,
        country=args.country,
        disease=args.disease,
        policy_path=args.policy,
        output_dir=args.output_dir,
        max_requests=args.max_requests,
    )
    print(json.dumps(_safe_json(result), ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


def build_live_pilot_plan(
    *,
    source_key: str,
    country: str,
    disease: str,
    policy_path: str = DEFAULT_POLICY,
    max_requests: int = 1,
) -> dict[str, Any]:
    preflight = run_preflight(source_key=source_key, policy_path=policy_path)
    request_count = max(1, min(int(max_requests), 3))
    return {
        "source_key": source_key,
        "country": country,
        "disease": disease,
        "max_requests": request_count,
        "required_flags": {
            "allow_network": True,
            "terms_reviewed": True,
            "manual_reviewed": True,
            "robots_allowed": True,
        },
        "guardrail_summary": {
            "preflight_status": preflight.get("status"),
            "guardrail_denies_without_allow_network": preflight.get("guardrail_denies_without_allow_network"),
            "guardrail_allows_when_all_conditions_met": preflight.get("guardrail_allows_when_all_conditions_met"),
            "network_accessed": False,
        },
        "future_live_command_template": (
            "python scripts/parse_official_connector.py --connector cdc_norostat "
            "--allow-network --live-url <CDC_NOROSTAT_URL_REVIEWED_BY_HUMAN> "
            "--terms-reviewed --manual-reviewed --robots-allowed --max-requests 1"
        ),
        "template_warnings": [
            "This command is for the next phase only after human approval.",
            "This planning script does not execute live fetch.",
            "The URL must be filled in only after manual review.",
            "Do not execute this command in automated tests.",
        ],
        "expected_outputs": [
            "reports/live_pilot/cdc_norostat_<timestamp>/command_summary.json",
            "reports/live_pilot/cdc_norostat_<timestamp>/connector_summary.json",
            "reports/live_pilot/cdc_norostat_<timestamp>/data_quality_report.md",
            "reports/live_pilot/cdc_norostat_<timestamp>/crawler_optimizer_report.json",
            "reports/live_pilot/cdc_norostat_<timestamp>/smoke_test_summary.json",
        ],
        "rollback_steps": [
            "Stop any pilot command still running.",
            "Archive safe summary reports if needed.",
            "Delete the pilot SQLite database file if rollback is required.",
            "Delete reports/live_pilot/cdc_norostat_<timestamp>/ if the run should be discarded.",
            "Remove pilot rows from a shared local database only after human confirmation.",
        ],
        "acceptance_criteria": [
            "request_count_lte_3",
            "no_sensitive_output",
            "raw_documents_written_or_safe_failure",
            "deduplication_checked",
            "cleaner_does_not_crash",
            "data_quality_report_generated",
            "crawler_optimizer_report_generated",
            "no_production_prediction",
        ],
        "current_step_executes_live_fetch": False,
        "network_accessed": False,
    }


def write_live_pilot_plan(
    *,
    source_key: str,
    country: str,
    disease: str,
    policy_path: str = DEFAULT_POLICY,
    output_dir: str = "reports/live_pilot",
    max_requests: int = 1,
) -> dict[str, Any]:
    plan = build_live_pilot_plan(
        source_key=source_key,
        country=country,
        disease=disease,
        policy_path=policy_path,
        max_requests=max_requests,
    )
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"{source_key}_plan_{timestamp}"
    json_path = directory / f"{prefix}.json"
    md_path = directory / f"{prefix}.md"
    json_path.write_text(json.dumps(_safe_json(plan), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(_render_plan_markdown(plan), encoding="utf-8")
    return {
        "status": "ok" if plan["guardrail_summary"]["preflight_status"] == "ok" else "failed",
        "plan_json": str(json_path),
        "plan_markdown": str(md_path),
        "source_key": source_key,
        "country": country,
        "disease": disease,
        "max_requests": plan["max_requests"],
        "live_execution_enabled": False,
        "network_accessed": False,
        "warnings": [] if plan["guardrail_summary"]["preflight_status"] == "ok" else ["preflight did not pass"],
    }


def _render_plan_markdown(plan: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# CDC NoroSTAT Controlled Live Pilot Plan",
            "",
            "This plan is not executed. It does not access the public internet.",
            "",
            "## Scope",
            "",
            f"- Source: `{plan['source_key']}`",
            f"- Country: {plan['country']}",
            f"- Disease: {plan['disease']}",
            f"- Max requests: {plan['max_requests']}",
            "",
            "## Required Flags",
            "",
            "```json",
            json.dumps(plan["required_flags"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Guardrail Summary",
            "",
            "```json",
            json.dumps(plan["guardrail_summary"], ensure_ascii=False, indent=2, sort_keys=True),
            "```",
            "",
            "## Future Live Command Template",
            "",
            "Do not execute this command in automated tests. It is only for the next phase after human approval.",
            "",
            "```bash",
            plan["future_live_command_template"],
            "```",
            "",
            "## Expected Outputs",
            "",
            "\n".join(f"- `{item}`" for item in plan["expected_outputs"]),
            "",
            "## Rollback Steps",
            "",
            "\n".join(f"- {item}" for item in plan["rollback_steps"]),
            "",
            "## Acceptance Criteria",
            "",
            "\n".join(f"- {item}" for item in plan["acceptance_criteria"]),
            "",
            "## Boundary",
            "",
            "No production prediction is triggered. Results require human review and do not guarantee prediction accuracy.",
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
        if any(marker in lowered for marker in ("password", "token", "secret", "ssh_key_path", "private key")):
            return "[redacted]"
    return value


if __name__ == "__main__":
    raise SystemExit(main())
