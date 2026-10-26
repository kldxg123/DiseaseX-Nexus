from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from live_source_search_orchestrator.review_queue import review_queue_to_markdown
from live_source_search_orchestrator.types import (
    LiveSourceSearchReport,
    NormalizedSourceCandidate,
    ProviderPlan,
    ProviderSearchResult,
    ReviewQueueItem,
)


def make_run_dir(*, output_dir: str | Path, country: str, disease: str, timestamp: str | None = None) -> Path:
    stamp = timestamp or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_id = f"search_{_safe_filename(disease)}_{_safe_filename(country)}_{stamp}"
    target = Path(output_dir) / run_id
    target.mkdir(parents=True, exist_ok=True)
    return target


def write_live_source_search_report(
    *,
    run_dir: str | Path,
    report: LiveSourceSearchReport,
    provider_plan: ProviderPlan,
    provider_results: list[ProviderSearchResult],
    normalized_candidates: list[NormalizedSourceCandidate],
    deduped_candidates: list[NormalizedSourceCandidate],
    review_queue: list[ReviewQueueItem],
    connector_specs: list[dict[str, Any]],
    refined_review_queue: list[ReviewQueueItem] | None = None,
    quality_assessments: list[Any] | None = None,
    metadata_check_summary: Any | None = None,
) -> dict[str, str]:
    target = Path(run_dir)
    target.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary_json": target / "live_source_search_summary.json",
        "summary_markdown": target / "live_source_search_summary.md",
        "provider_plan_json": target / "provider_plan.json",
        "provider_plan_markdown": target / "provider_plan.md",
        "provider_results_summary": target / "provider_results_summary.json",
        "provider_failure_diagnostics_json": target / "provider_failure_diagnostics.json",
        "provider_failure_diagnostics_markdown": target / "provider_failure_diagnostics.md",
        "positive_yield_validation_json": target / "positive_yield_validation.json",
        "positive_yield_validation_markdown": target / "positive_yield_validation.md",
        "normalized_candidates": target / "normalized_candidates.json",
        "deduped_ranked_candidates": target / "deduped_ranked_candidates.json",
        "candidate_quality_gate_json": target / "candidate_quality_gate.json",
        "candidate_quality_gate_markdown": target / "candidate_quality_gate.md",
        "metadata_check_results_json": target / "metadata_check_results.json",
        "metadata_check_results_markdown": target / "metadata_check_results.md",
        "review_queue_json": target / "review_queue.json",
        "review_queue_markdown": target / "review_queue.md",
        "refined_review_queue_json": target / "refined_review_queue.json",
        "refined_review_queue_markdown": target / "refined_review_queue.md",
        "connector_specs": target / "connector_specs_recommendations.json",
        "safety_audit": target / "safety_audit.md",
        "metadata_check_safety_audit": target / "metadata_check_safety_audit.md",
        "rollback_commands": target / "rollback_commands.md",
    }
    summary = report.to_dict()
    summary["output_dir"] = str(target)
    summary["review_queue_path"] = str(paths["review_queue_json"])
    summary["connector_specs_path"] = str(paths["connector_specs"])
    summary["summary_path"] = str(paths["summary_json"])
    summary["safety_audit_path"] = str(paths["safety_audit"])
    summary["provider_failure_diagnostics_path"] = str(paths["provider_failure_diagnostics_json"])
    summary["positive_yield_validation_path"] = str(paths["positive_yield_validation_json"])
    summary["candidate_quality_gate_path"] = str(paths["candidate_quality_gate_json"])
    summary["metadata_check_results_path"] = str(paths["metadata_check_results_json"])
    summary["refined_review_queue_path"] = str(paths["refined_review_queue_json"])
    summary["metadata_check_safety_audit_path"] = str(paths["metadata_check_safety_audit"])
    write_json(paths["summary_json"], summary)
    paths["summary_markdown"].write_text(render_summary_markdown(summary), encoding="utf-8")
    write_json(paths["provider_plan_json"], provider_plan.to_dict())
    paths["provider_plan_markdown"].write_text(render_provider_plan_markdown(provider_plan), encoding="utf-8")
    write_json(paths["provider_results_summary"], {"provider_results": [row.to_dict() for row in provider_results]})
    write_json(
        paths["provider_failure_diagnostics_json"],
        {"provider_failure_diagnostics": report.provider_failure_diagnostics},
    )
    paths["provider_failure_diagnostics_markdown"].write_text(
        render_provider_failure_diagnostics_markdown(report.provider_failure_diagnostics),
        encoding="utf-8",
    )
    write_json(paths["positive_yield_validation_json"], report.positive_yield_validation)
    paths["positive_yield_validation_markdown"].write_text(
        render_positive_yield_validation_markdown(report.positive_yield_validation),
        encoding="utf-8",
    )
    write_json(paths["normalized_candidates"], {"candidates": [row.to_dict() for row in normalized_candidates]})
    write_json(paths["deduped_ranked_candidates"], {"candidates": [row.to_dict() for row in deduped_candidates]})
    quality_rows = [_to_dict(row) for row in (quality_assessments or [])]
    write_json(paths["candidate_quality_gate_json"], {"candidate_quality_gate": quality_rows})
    paths["candidate_quality_gate_markdown"].write_text(render_quality_gate_markdown(quality_rows), encoding="utf-8")
    metadata_payload = metadata_check_summary.to_dict() if metadata_check_summary is not None else _empty_metadata_payload()
    write_json(paths["metadata_check_results_json"], metadata_payload)
    paths["metadata_check_results_markdown"].write_text(render_metadata_check_markdown(metadata_payload), encoding="utf-8")
    write_json(paths["review_queue_json"], {"review_queue": [row.to_dict() for row in review_queue]})
    paths["review_queue_markdown"].write_text(review_queue_to_markdown(review_queue), encoding="utf-8")
    refined_queue = refined_review_queue if refined_review_queue is not None else review_queue
    write_json(paths["refined_review_queue_json"], {"review_queue": [row.to_dict() for row in refined_queue]})
    paths["refined_review_queue_markdown"].write_text(review_queue_to_markdown(refined_queue), encoding="utf-8")
    write_json(paths["connector_specs"], {"connector_specs": connector_specs})
    paths["safety_audit"].write_text(render_safety_audit(summary), encoding="utf-8")
    paths["metadata_check_safety_audit"].write_text(render_metadata_check_safety_audit(summary), encoding="utf-8")
    paths["rollback_commands"].write_text(render_rollback_commands(), encoding="utf-8")
    return {key: str(value) for key, value in paths.items()}


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(_sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_summary_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# Live Source Search Summary",
        "",
        f"- Status: {summary.get('status')}",
        f"- Country: {summary.get('country')}",
        f"- Disease: {summary.get('disease')}",
        f"- Providers used: {', '.join(summary.get('providers_used') or []) or 'none'}",
        f"- Request count total: {summary.get('request_count_total')}",
        f"- Candidates after dedupe: {summary.get('candidates_after_dedupe')}",
        f"- Pending review inserted: {summary.get('pending_review_inserted')}",
        f"- Connector specs generated: {summary.get('connector_specs_generated')}",
        f"- Positive yield achieved: {(summary.get('positive_yield_validation') or {}).get('achieved_positive_yield')}",
        f"- Quality gate rejected: {summary.get('quality_gate_rejected')}",
        f"- Metadata check request count: {summary.get('metadata_check_request_count')}",
        f"- Candidate URLs visited: {summary.get('candidate_urls_visited')}",
        f"- Metadata candidate URLs visited: {summary.get('candidate_urls_visited_for_metadata_check')}",
        f"- Follow links: {not summary.get('no_follow_links', True)}",
        "",
        "## Top Domains",
    ]
    for domain in summary.get("top_domains") or []:
        lines.append(f"- {domain}")
    lines.extend(
        [
            "",
            "## Safety",
            "- Provider endpoints only.",
            "- Candidate URLs were not visited.",
            "- Sources remain pending_review.",
            "- Connector specs are recommendations only.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_provider_plan_markdown(plan: ProviderPlan) -> str:
    lines = [
        "# Provider Plan",
        "",
        f"- Requested: {plan.providers_requested}",
        f"- Max total requests: {plan.max_total_requests}",
        f"- Per-provider requests: {plan.per_provider_max_requests}",
        "",
        "## Planned Providers",
    ]
    for provider in plan.providers_planned:
        lines.append(f"- {provider}")
    if plan.skipped_providers:
        lines.extend(["", "## Skipped Providers"])
        for item in plan.skipped_providers:
            lines.append(f"- {item.get('provider')}: {item.get('reason')}")
    return "\n".join(lines) + "\n"


def render_provider_failure_diagnostics_markdown(diagnostics: list[dict[str, Any]]) -> str:
    lines = [
        "# Provider Failure Diagnostics",
        "",
        "| provider | status | failure_type | http_status_code | request_count | network_accessed | safe_failure |",
        "| --- | --- | --- | --- | ---: | --- | --- |",
    ]
    for item in diagnostics:
        lines.append(
            "| {provider} | {status} | {failure_type} | {http_status_code} | {request_count} | {network_accessed} | {safe_failure} |".format(
                provider=item.get("provider") or "",
                status=item.get("status") or "",
                failure_type=item.get("failure_type") or "",
                http_status_code=item.get("http_status_code") or "",
                request_count=item.get("request_count") or 0,
                network_accessed=item.get("network_accessed"),
                safe_failure=item.get("safe_failure"),
            )
        )
    lines.extend(
        [
            "",
            "Candidate URLs were not visited and links were not followed.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_positive_yield_validation_markdown(validation: dict[str, Any]) -> str:
    lines = [
        "# Positive Yield Validation",
        "",
        f"- Country: {validation.get('country')}",
        f"- Disease: {validation.get('disease')}",
        f"- Candidates seen: {validation.get('candidates_seen')}",
        f"- Candidates after dedupe: {validation.get('candidates_after_dedupe')}",
        f"- Pending review inserted: {validation.get('pending_review_inserted')}",
        f"- Connector specs generated: {validation.get('connector_specs_generated')}",
        f"- Achieved positive yield: {validation.get('achieved_positive_yield')}",
        f"- Without candidate URL visit: {validation.get('achieved_without_candidate_url_visit')}",
        f"- Without follow links: {validation.get('achieved_without_follow_links')}",
        f"- Request budget respected: {validation.get('request_budget_respected')}",
        "",
        "## Limitations",
        str(validation.get("limitations") or ""),
    ]
    return "\n".join(lines) + "\n"


def render_quality_gate_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Candidate Quality Gate",
        "",
        "| title | domain | status | refined_score | reason | next_step |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {title} | {domain} | {status} | {score:.3f} | {reason} | {next_step} |".format(
                title=str(row.get("title") or "")[:80],
                domain=row.get("domain") or "",
                status=row.get("quality_gate_status") or "",
                score=float(row.get("refined_score") or 0.0),
                reason=row.get("rejection_reason") or "",
                next_step=row.get("recommended_next_step") or "",
            )
        )
    return "\n".join(lines) + "\n"


def render_metadata_check_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Metadata Check Results",
        "",
        f"- Status: {payload.get('status')}",
        f"- Request count: {payload.get('request_count')}",
        f"- Confirmed data sources: {payload.get('confirmed_data_sources')}",
        f"- Rejected non-data sources: {payload.get('rejected_non_data_sources')}",
        f"- Follow links: {not payload.get('no_follow_links', True)}",
        f"- Files downloaded: {payload.get('files_downloaded')}",
        f"- Full body saved: {payload.get('full_body_saved')}",
        "",
        "| domain | status | likelihood | type | parser | action |",
        "| --- | --- | ---: | --- | --- | --- |",
    ]
    for row in payload.get("results") or []:
        lines.append(
            "| {domain} | {status} | {likelihood:.3f} | {source_type} | {parser} | {action} |".format(
                domain=row.get("domain") or "",
                status=row.get("status") or "",
                likelihood=float(row.get("data_source_likelihood") or 0.0),
                source_type=row.get("detected_source_type") or "",
                parser=row.get("parser_strategy_hint") or "",
                action=row.get("recommended_action") or "",
            )
        )
    return "\n".join(lines) + "\n"


def render_safety_audit(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Safety Audit",
            "",
            f"- Request count total: {summary.get('request_count_total')}",
            f"- Candidate URLs visited: {summary.get('candidate_urls_visited')}",
            f"- Candidate URLs visited for metadata check: {summary.get('candidate_urls_visited_for_metadata_check')}",
            f"- Follow links disabled: {summary.get('no_follow_links')}",
            f"- Files downloaded: {summary.get('files_downloaded')}",
            f"- Full body saved: {summary.get('full_body_saved')}",
            "- No source was automatically activated.",
            "- No connector code was generated or enabled.",
            "- No production prediction was executed.",
            "- Reports contain summaries only.",
        ]
    ) + "\n"


def render_metadata_check_safety_audit(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Metadata Check Safety Audit",
            "",
            f"- Metadata check executed: {summary.get('metadata_check_executed')}",
            f"- Metadata check request count: {summary.get('metadata_check_request_count')}",
            "- Per-candidate URL request limit: 1",
            "- Follow links: false",
            f"- Files downloaded: {summary.get('files_downloaded')}",
            f"- Full body saved: {summary.get('full_body_saved')}",
            "- Redirects are not followed.",
            "- Candidate page links are not visited.",
            "- Sources remain pending_review.",
        ]
    ) + "\n"


def render_rollback_commands() -> str:
    return "\n".join(
        [
            "# Rollback Commands",
            "",
            "No remote rollback is required.",
            "For a local pilot SQLite DB, remove the pilot DB file only after manual confirmation.",
            "For persistent local DB rows, keep candidates pending_review or disable them through the review API.",
        ]
    ) + "\n"


def read_latest_report_dirs(output_dir: str | Path, *, limit: int = 20) -> list[dict[str, Any]]:
    root = Path(output_dir)
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("search_*"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
        summary_path = path / "live_source_search_summary.json"
        if not summary_path.exists():
            continue
        try:
            payload = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        rows.append(
            {
                "run_id": path.name,
                "output_dir": str(path),
                "status": payload.get("status"),
                "country": payload.get("country"),
                "disease": payload.get("disease"),
                "request_count_total": payload.get("request_count_total"),
                "candidates_after_dedupe": payload.get("candidates_after_dedupe"),
            }
        )
    return rows


def _safe_filename(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "").strip())
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "unknown"


def _empty_metadata_payload() -> dict[str, Any]:
    return {
        "status": "not_executed",
        "request_count": 0,
        "checked": 0,
        "confirmed_data_sources": 0,
        "rejected_non_data_sources": 0,
        "no_follow_links": True,
        "files_downloaded": False,
        "full_body_saved": False,
        "results": [],
        "warnings": [],
    }


def _to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return dict(value)


def _sanitize(value: Any) -> Any:
    forbidden_keys = {
        "html",
        "raw_text",
        "body",
        "body_text",
        "metadata_json",
        "provenance_json",
        "feature_json",
        "lesson_json",
        "response_body",
    }
    sensitive = ("password", "token", "secret", "credential", "private", "ssh")
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered in forbidden_keys or any(marker in lowered for marker in sensitive):
                continue
            result[key_text] = _sanitize(item)
        return result
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:200]]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value[:200]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    lowered = text.lower()
    if any(marker in lowered for marker in sensitive):
        return "[redacted]"
    return text if len(text) <= 300 else text[:300] + "...[truncated]"


__all__ = [
    "make_run_dir",
    "read_latest_report_dirs",
    "render_provider_plan_markdown",
    "render_provider_failure_diagnostics_markdown",
    "render_positive_yield_validation_markdown",
    "render_quality_gate_markdown",
    "render_metadata_check_markdown",
    "render_metadata_check_safety_audit",
    "render_rollback_commands",
    "render_safety_audit",
    "render_summary_markdown",
    "write_live_source_search_report",
]
