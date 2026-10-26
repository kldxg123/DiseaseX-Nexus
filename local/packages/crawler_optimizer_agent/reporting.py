from __future__ import annotations

from collections import Counter
from typing import Any
from uuid import uuid4

from crawler_optimizer_agent.types import OptimizationReport, safe_mapping, safe_text, safe_url


def summarize_optimizer_report(
    report: OptimizationReport | dict[str, Any],
    *,
    max_failure_cases: int = 20,
    max_actions: int = 20,
    max_connector_specs: int = 10,
    include_job_id: bool = False,
) -> dict[str, Any]:
    payload = report.to_dict() if hasattr(report, "to_dict") else dict(report)
    failure_cases = [
        summarize_failure_case(case) for case in list(payload.get("failure_cases") or [])[:max_failure_cases]
    ]
    actions = [summarize_action(action) for action in list(payload.get("recommended_actions") or [])[:max_actions]]
    connector_specs = [
        summarize_connector_spec(spec)
        for spec in list(payload.get("connector_specs") or [])[:max_connector_specs]
    ]
    summary = {
        "status": safe_text(payload.get("status")) or "unknown",
        "sources_analyzed": int(payload.get("sources_analyzed") or 0),
        "documents_analyzed": int(payload.get("documents_analyzed") or 0),
        "failure_case_count": len(payload.get("failure_cases") or []),
        "recommended_action_count": len(payload.get("recommended_actions") or []),
        "connector_spec_count": len(payload.get("connector_specs") or []),
        "top_failure_types": top_failure_types(payload.get("failure_cases") or []),
        "failure_cases": failure_cases,
        "recommended_actions": actions,
        "connector_specs": connector_specs,
        "warnings": [safe_text(warning) for warning in list(payload.get("warnings") or [])],
        "limitations": (
            "Local crawler optimization analysis only. No live fetches, crawler rewrites, "
            "source activation, or high-risk actions are performed automatically."
        ),
    }
    if include_job_id:
        summary = {"job_id": str(uuid4()), **summary}
    return safe_mapping(summary)


def summarize_failure_case(case: dict[str, Any]) -> dict[str, Any]:
    return safe_mapping(
        {
            "failure_type": case.get("failure_type"),
            "severity": case.get("severity"),
            "confidence": case.get("confidence"),
            "source_id": case.get("source_id"),
            "raw_document_id": case.get("raw_document_id"),
            "source_name": case.get("source_name"),
            "url": safe_url(case.get("url")),
            "created_at": case.get("created_at"),
        }
    )


def summarize_action(action: dict[str, Any]) -> dict[str, Any]:
    return safe_mapping(
        {
            "action_type": action.get("action_type"),
            "target_type": action.get("target_type"),
            "target_id": action.get("target_id"),
            "reason": action.get("reason"),
            "confidence": action.get("confidence"),
            "risk_level": action.get("risk_level"),
            "requires_human_review": bool(action.get("requires_human_review")),
        }
    )


def summarize_connector_spec(spec: dict[str, Any]) -> dict[str, Any]:
    sample_urls = list(spec.get("sample_urls") or [])
    return safe_mapping(
        {
            "connector_name": spec.get("connector_name"),
            "source_name": spec.get("source_name"),
            "base_url": safe_url(spec.get("base_url")),
            "source_type": spec.get("source_type"),
            "parser_strategy": spec.get("parser_strategy"),
            "expected_fields": list(spec.get("expected_fields") or []),
            "sample_url_count": len(sample_urls),
            "sample_urls": [safe_url(url) for url in sample_urls[:3]],
            "robots_required": bool(spec.get("robots_required")),
            "rate_limit_seconds": spec.get("rate_limit_seconds"),
            "requires_human_review": bool(spec.get("requires_human_review", True)),
            "test_fixture_required": bool(spec.get("test_fixture_required", True)),
            "live_fetch_supported": bool(spec.get("live_fetch_supported", False)),
        }
    )


def top_failure_types(failure_cases: list[dict[str, Any]], *, limit: int = 10) -> list[dict[str, Any]]:
    counts = Counter(str(case.get("failure_type") or "unknown") for case in failure_cases)
    return [{"failure_type": name, "count": count} for name, count in counts.most_common(limit)]
