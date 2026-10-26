from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = PROJECT_ROOT / "packages"
for candidate in (PROJECT_ROOT, PACKAGES_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from common.db import get_session, init_db
from common.models import SourceRegistry
from scripts.live_source_discovery_preflight import run_preflight
from scripts.plan_live_source_discovery import write_live_source_discovery_plan
from source_discovery_agent import SourceDiscoveryAgent
from source_discovery_agent.live_guard import (
    DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY,
    decide_live_discovery_access,
    load_live_discovery_policy,
)
from source_discovery_agent.live_providers import LiveDiscoveryRequest, get_live_discovery_provider
from source_discovery_agent.report import discovery_report_to_dict, render_discovery_report_markdown


def run_controlled_live_source_discovery(
    *,
    provider: str,
    country: str,
    disease: str,
    dry_run: bool = False,
    execute_live: bool = False,
    allow_network: bool = False,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    policy_path: str = DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY,
    output_dir: str = "reports/source_discovery/live_readiness",
    max_requests: int = 1,
    transport=None,
    directory_url: str | None = None,
) -> dict[str, Any]:
    if execute_live:
        return _run_execute_live(
            provider=provider,
            country=country,
            disease=disease,
            allow_network=allow_network,
            terms_reviewed=terms_reviewed,
            manual_reviewed=manual_reviewed,
            policy_path=policy_path,
            output_dir=output_dir,
            max_requests=max_requests,
            transport=transport,
            directory_url=directory_url,
        )
    if not dry_run:
        return {
            "status": "failed",
            "dry_run": False,
            "provider": provider,
            "live_execution_enabled": False,
            "network_accessed": False,
            "error": "real live discovery disabled in this readiness step; rerun with --dry-run or explicit --execute-live approval flags",
            "next_step_required": "human approval before live discovery",
        }
    preflight = run_preflight(
        provider=provider,
        country=country,
        disease=disease,
        policy_path=policy_path,
        max_requests=max_requests,
    )
    plan = write_live_source_discovery_plan(
        provider=provider,
        country=country,
        disease=disease,
        policy_path=policy_path,
        output_dir=output_dir,
        max_requests=max_requests,
    )
    provider_result = get_live_discovery_provider(provider).discover(
        LiveDiscoveryRequest(
            country=country,
            disease=disease,
            provider=provider,
            max_requests=max_requests,
            allow_network=False,
            terms_reviewed=False,
            manual_reviewed=False,
        )
    )
    return _safe_json(
        {
            "status": "ok" if preflight.get("status") == "ok" and plan.get("status") == "ok" else "failed",
            "dry_run": True,
            "provider": provider,
            "country": country,
            "disease": disease,
            "preflight_status": preflight.get("status"),
            "plan_json": plan.get("plan_json"),
            "plan_markdown": plan.get("plan_markdown"),
            "provider_dry_run": asdict(provider_result),
            "live_execution_enabled": False,
            "network_accessed": False,
            "next_step_required": "human approval before live discovery",
        }
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Controlled live source discovery runner.")
    parser.add_argument("--provider", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--disease", required=True)
    parser.add_argument("--policy", default=DEFAULT_LIVE_SOURCE_DISCOVERY_POLICY)
    parser.add_argument("--output-dir", default="reports/source_discovery/live_readiness")
    parser.add_argument("--max-requests", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute-live", action="store_true")
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--directory-url", default=None)
    args = parser.parse_args(argv)
    result = run_controlled_live_source_discovery(
        provider=args.provider,
        country=args.country,
        disease=args.disease,
        dry_run=args.dry_run,
        execute_live=args.execute_live,
        allow_network=args.allow_network,
        terms_reviewed=args.terms_reviewed,
        manual_reviewed=args.manual_reviewed,
        policy_path=args.policy,
        output_dir=args.output_dir,
        max_requests=args.max_requests,
        directory_url=args.directory_url,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "ok" else 2


def _run_execute_live(
    *,
    provider: str,
    country: str,
    disease: str,
    allow_network: bool,
    terms_reviewed: bool,
    manual_reviewed: bool,
    policy_path: str,
    output_dir: str,
    max_requests: int,
    transport=None,
    directory_url: str | None = None,
) -> dict[str, Any]:
    provider = str(provider or "").strip().lower()
    if provider not in {"gdelt", "official_directory"}:
        return _failure(provider, "execute-live is currently limited to provider=gdelt or provider=official_directory.")
    if int(max_requests) != 1:
        return _failure(provider, "execute-live requires max_requests=1.")
    preflight = run_preflight(
        provider=provider,
        country=country,
        disease=disease,
        policy_path=policy_path,
        max_requests=max_requests,
    )
    if preflight.get("status") != "ok":
        return _failure(provider, "preflight did not pass.", preflight=preflight)
    policy = load_live_discovery_policy(policy_path)
    decision = decide_live_discovery_access(
        provider=provider,
        policy=policy,
        allow_network=allow_network,
        manual_reviewed=manual_reviewed,
        terms_reviewed=terms_reviewed,
        max_requests=max_requests,
    )
    if not decision.get("allowed"):
        return _failure(provider, str(decision.get("reason") or "live discovery access denied"), preflight=preflight)
    if provider == "official_directory":
        directory_url = str(directory_url or "").strip()
        if not directory_url:
            return _failure(provider, "official_directory execute-live requires --directory-url.")
        if not _directory_url_allowed(directory_url, policy=policy, provider=provider):
            return _failure(provider, "official_directory directory URL domain is not allowlisted.")

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(output_dir) / f"{provider}_{_safe_slug(disease)}_live_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / f"{provider}_live_discovery.sqlite"
    database_url = f"sqlite:///{db_path.as_posix()}"
    init_db(database_url).dispose()

    provider_result = get_live_discovery_provider(provider).discover(
        LiveDiscoveryRequest(
            country=country,
            disease=disease,
            provider=provider,
            max_requests=max_requests,
            allow_network=allow_network,
            terms_reviewed=terms_reviewed,
            manual_reviewed=manual_reviewed,
            metadata=_provider_metadata(provider=provider, transport=transport, directory_url=directory_url),
        )
    )
    with get_session(database_url) as session:
        report = SourceDiscoveryAgent(session).discover_from_provider_result(
            country=country,
            disease=disease,
            provider_result=provider_result,
            min_source_score=0.0,
            generate_connector_specs=True,
        )
        _tag_run_sources(session, run_id=timestamp, provider=provider)
        pending_review_count = session.query(SourceRegistry).filter(SourceRegistry.status == "pending_review").count()
        top_domains = _top_candidate_domains(report)
        connector_specs = _connector_specs_summary(report)

    source_report_json = run_dir / "source_discovery_report.json"
    source_report_md = run_dir / "source_discovery_report.md"
    source_report_json.write_text(
        json.dumps(discovery_report_to_dict(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    source_report_md.write_text(render_discovery_report_markdown(report), encoding="utf-8")

    connector_specs_path = run_dir / "connector_specs_summary.json"
    connector_specs_path.write_text(
        json.dumps(connector_specs, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    rollback_path = run_dir / "rollback_commands.md"
    rollback_path.write_text(_rollback_markdown(db_path=db_path, run_id=timestamp), encoding="utf-8")

    status = "ok" if provider_result.request_count == 1 and report.candidates_found >= 1 else "failed"
    summary = _safe_json(
        {
            "status": status,
            "safe_failure_reason": None if status == "ok" else _safe_failure_reason(provider_result, report),
            "provider": provider,
            "country": country,
            "disease": disease,
            "live_output_dir": str(run_dir),
            "live_discovery_db_path": str(db_path),
            "preflight_status": preflight.get("status"),
            "network_accessed": bool(provider_result.network_accessed),
            "true_provider_accessed": provider,
            "true_request_count": int(provider_result.request_count),
            "candidate_urls_accessed": False,
            "follow_links": False,
            "search_results_seen": report.search_results_seen,
            "candidates_found": report.candidates_found,
            "candidates_inserted_or_updated": report.candidates_inserted_or_updated,
            "pending_review_count": int(pending_review_count),
            "connector_specs_generated": report.connector_specs_generated,
            "top_candidate_domains": top_domains[:5],
            "source_discovery_report_json": str(source_report_json),
            "source_discovery_report_markdown": str(source_report_md),
            "connector_specs_summary": str(connector_specs_path),
            "rollback_commands": str(rollback_path),
            "live_source_discovery_summary_json": str(run_dir / "live_source_discovery_summary.json"),
            "live_source_discovery_summary_markdown": str(run_dir / "live_source_discovery_summary.md"),
            "provider_response_summary": provider_result.metadata,
            "warnings": list(provider_result.warnings)[:10],
            "failure_type": provider_result.failure_type,
            "retry_allowed": provider_result.retry_allowed,
            "cooldown_recommended": provider_result.cooldown_recommended,
            "retry_after_seconds": provider_result.retry_after_seconds,
            "live_execution_enabled": True,
        }
    )
    summary_json = run_dir / "live_source_discovery_summary.json"
    summary_md = run_dir / "live_source_discovery_summary.md"
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary_md.write_text(_summary_markdown(summary), encoding="utf-8")
    return summary


def _failure(provider: str, reason: str, *, preflight: dict | None = None) -> dict[str, Any]:
    return _safe_json(
        {
            "status": "failed",
            "provider": provider,
            "network_accessed": False,
            "live_execution_enabled": False,
            "error": reason,
            "preflight_status": (preflight or {}).get("status"),
        }
    )


def _provider_metadata(*, provider: str, transport, directory_url: str | None) -> dict[str, Any]:
    metadata: dict[str, Any] = {"execute_live": True}
    if provider == "gdelt":
        metadata["max_records"] = 25
    if provider == "official_directory":
        metadata["directory_url"] = directory_url
    if transport is not None:
        metadata["transport"] = transport
    return metadata


def _directory_url_allowed(url: str, *, policy: dict, provider: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return False
    provider_policy = None
    for entry in policy.get("providers", []):
        if isinstance(entry, dict) and str(entry.get("provider") or "").lower() == provider:
            provider_policy = entry
            break
    allowed_domains = [str(item).lower() for item in (provider_policy or {}).get("allowed_domains", [])]
    return any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains)


def _tag_run_sources(session, *, run_id: str, provider: str) -> None:
    for source in session.query(SourceRegistry).all():
        source.status = "pending_review"
        source.metadata_json = {
            **(source.metadata_json or {}),
            "live_source_discovery_run_id": run_id,
            "live_source_discovery_provider": provider,
            "network_executed": True,
            "requires_human_review": True,
            "auto_activate": False,
        }
    session.flush()


def _top_candidate_domains(report) -> list[str]:
    domains: list[str] = []
    for candidate in report.top_candidates:
        domain = str(candidate.get("domain") or "").strip().lower()
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def _connector_specs_summary(report) -> dict[str, Any]:
    specs = []
    for candidate in report.top_candidates:
        spec = candidate.get("connector_spec")
        if not isinstance(spec, dict):
            continue
        specs.append(
            {
                "connector_name": spec.get("connector_name"),
                "source_name": spec.get("source_name"),
                "source_type": spec.get("source_type"),
                "parser_strategy": spec.get("parser_strategy"),
                "requires_human_review": spec.get("requires_human_review"),
                "test_fixture_required": spec.get("test_fixture_required"),
            }
        )
    return {"connector_specs_generated": len(specs), "specs": specs}


def _rollback_markdown(*, db_path: Path, run_id: str) -> str:
    return "\n".join(
        [
            "# GDELT Live Source Discovery Rollback Commands",
            "",
            "This pilot used a standalone SQLite database.",
            "",
            f"- Pilot DB: `{db_path}`",
            f"- Run ID: `{run_id}`",
            "",
            "Review rows before cleanup:",
            "",
            "```sql",
            "SELECT id, name, status FROM source_registry WHERE status = 'pending_review';",
            "```",
            "",
            "After human confirmation, cleanup candidate rows in the standalone pilot DB:",
            "",
            "```sql",
            "DELETE FROM source_registry WHERE status = 'pending_review';",
            "```",
            "",
            "File cleanup:",
            "",
            "- Delete this live run output directory if the run should be discarded.",
            "- Confirm no production database was used.",
            "- Confirm no candidate source was activated.",
            "",
        ]
    )


def _summary_markdown(summary: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# GDELT Influenza Live Source Discovery Summary",
            "",
            f"- Status: {summary.get('status')}",
            f"- Provider: {summary.get('provider')}",
            f"- True request count: {summary.get('true_request_count')}",
            f"- Search results seen: {summary.get('search_results_seen')}",
            f"- Candidates found: {summary.get('candidates_found')}",
            f"- Candidates inserted or updated: {summary.get('candidates_inserted_or_updated')}",
            f"- Pending review count: {summary.get('pending_review_count')}",
            f"- ConnectorSpec suggestions: {summary.get('connector_specs_generated')}",
            f"- Candidate URLs accessed: {summary.get('candidate_urls_accessed')}",
            f"- Follow links: {summary.get('follow_links')}",
            "",
            "## Top Candidate Domains",
            "\n".join(f"- {domain}" for domain in summary.get("top_candidate_domains", [])) or "- None",
            "",
            "## Safety",
            "- Exactly one GDELT provider request was permitted.",
            "- Candidate URLs were not accessed.",
            "- New sources remain pending_review.",
            "- No production prediction or connector deployment was triggered.",
            "",
        ]
    )


def _safe_failure_reason(provider_result, report) -> str:
    if provider_result.failure_type == "rate_limited":
        return "provider rate limited request"
    if provider_result.request_count != 1:
        return "provider request did not execute exactly once"
    if not provider_result.search_results:
        return "provider returned no search results"
    if report.candidates_found < 1:
        return "source evaluator found no candidate sources"
    return "unknown safe failure"


def _safe_slug(value: str) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "").strip())
    return "_".join(part for part in text.split("_") if part) or "source"


def _safe_json(value: Any) -> Any:
    blocked_key_parts = (
        "secret",
        "token",
        "password",
        "credential",
        "private",
        "ssh",
        "server",
        "username",
        "html",
        "raw_text",
        "body_text",
        "metadata_json",
        "provenance_json",
        "feature_json",
        "lesson_json",
        "response_body",
        "snippet",
    )
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in blocked_key_parts):
                continue
            result[key_text] = _safe_json(item)
        return result
    if isinstance(value, list):
        return [_safe_json(item) for item in value[:100]]
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value[:100]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    lowered = text.lower()
    if any(marker in lowered for marker in ("password", "token", "secret", "credential", "private", "ssh")):
        return "[redacted]"
    if "<html" in lowered:
        return "[omitted]"
    if _looks_like_candidate_url(text):
        host = urlsplit(text).hostname
        return host or "[url]"
    return text[:500]


def _looks_like_candidate_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


if __name__ == "__main__":
    raise SystemExit(main())
