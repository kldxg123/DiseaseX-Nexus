from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = PROJECT_ROOT / "packages"
for candidate in (PROJECT_ROOT, PACKAGES_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from source_discovery_agent.live_providers.cooldown import recommend_provider_cooldown


def analyze_live_source_discovery_run(run_dir: str | Path) -> dict[str, Any]:
    directory = Path(run_dir)
    if not directory.exists() or not directory.is_dir():
        raise ValueError("run_dir must identify an existing live discovery run directory.")
    summary = _read_json(directory / "live_source_discovery_summary.json")
    source_report = _read_json(directory / "source_discovery_report.json")
    connector_specs = _read_json(directory / "connector_specs_summary.json")
    rollback_exists = (directory / "rollback_commands.md").exists()
    db_summary = _sqlite_summary(directory / "gdelt_live_discovery.sqlite")

    failure_reason = str(summary.get("safe_failure_reason") or summary.get("error") or "")
    status_code = _status_code(summary)
    failure_type = _failure_type(summary, failure_reason=failure_reason, status_code=status_code)
    retry_after_seconds = _optional_int(summary.get("retry_after_seconds"))
    if retry_after_seconds is None:
        retry_after_seconds = _optional_int((summary.get("provider_response_summary") or {}).get("retry_after_seconds"))
    cooldown = recommend_provider_cooldown(
        provider=str(summary.get("provider") or "unknown"),
        failure_type=failure_type,
        retry_after_seconds=retry_after_seconds,
    )
    postmortem = _safe_json(
        {
            "status": "ok",
            "provider": summary.get("provider"),
            "country": summary.get("country"),
            "disease": summary.get("disease"),
            "request_count": summary.get("true_request_count"),
            "network_accessed": summary.get("network_accessed"),
            "failure_reason": failure_reason or _failure_reason_from_status(status_code),
            "failure_type": failure_type,
            "retry_allowed": False,
            "cooldown_recommended": cooldown.cooldown_recommended,
            "retry_after_seconds": cooldown.retry_after_seconds,
            "minimum_cooldown_minutes": cooldown.minimum_cooldown_minutes,
            "search_results_seen": summary.get("search_results_seen", source_report.get("search_results_seen", 0)),
            "candidates_found": summary.get("candidates_found", source_report.get("candidates_found", 0)),
            "connector_specs_generated": summary.get(
                "connector_specs_generated",
                connector_specs.get("connector_specs_generated", 0),
            ),
            "whether_retry_happened": int(summary.get("true_request_count") or 0) > 1,
            "whether_candidate_urls_were_visited": bool(summary.get("candidate_urls_accessed")),
            "whether_follow_links_happened": bool(summary.get("follow_links")),
            "rollback_file_present": rollback_exists,
            "db_summary": db_summary,
            "recommended_next_step": [
                "do not retry immediately",
                "respect cooldown",
                "consider official_directory provider",
                "keep GDELT as rate-limited until reviewed",
            ],
        }
    )
    postmortem_json = directory / "live_discovery_postmortem.json"
    postmortem_md = directory / "live_discovery_postmortem.md"
    fallback_json = directory / "fallback_readiness_plan.json"
    fallback_md = directory / "fallback_readiness_plan.md"
    fallback = _fallback_plan(postmortem)

    postmortem["postmortem_json"] = str(postmortem_json)
    postmortem["postmortem_markdown"] = str(postmortem_md)
    postmortem["fallback_readiness_plan_json"] = str(fallback_json)
    postmortem["fallback_readiness_plan_markdown"] = str(fallback_md)

    postmortem_json.write_text(json.dumps(postmortem, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    postmortem_md.write_text(_render_postmortem_markdown(postmortem), encoding="utf-8")
    fallback_json.write_text(json.dumps(fallback, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    fallback_md.write_text(_render_fallback_markdown(fallback), encoding="utf-8")
    return postmortem


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Analyze a local live source discovery run without network access.")
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args(argv)
    result = analyze_live_source_discovery_run(args.run_dir)
    print(json.dumps(_safe_json(result), ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _sqlite_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False}
    try:
        with sqlite3.connect(path) as connection:
            source_count = _scalar_count(connection, "source_registry")
            pending_count = _scalar_count(connection, "source_registry", "status = 'pending_review'")
        return {"exists": True, "source_count": source_count, "pending_review_count": pending_count}
    except sqlite3.Error:
        return {"exists": True, "readable": False}


def _scalar_count(connection: sqlite3.Connection, table: str, where: str | None = None) -> int:
    statement = f"SELECT COUNT(*) FROM {table}"
    if where:
        statement = f"{statement} WHERE {where}"
    try:
        row = connection.execute(statement).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0] or 0)


def _status_code(summary: dict[str, Any]) -> int | None:
    provider_summary = summary.get("provider_response_summary")
    if not isinstance(provider_summary, dict):
        return None
    return _optional_int(provider_summary.get("status_code"))


def _failure_type(summary: dict[str, Any], *, failure_reason: str, status_code: int | None) -> str:
    explicit = str(summary.get("failure_type") or "").strip()
    if explicit:
        return explicit
    haystack = json.dumps(summary, ensure_ascii=False).lower() + " " + failure_reason.lower()
    if status_code == 429 or "429" in haystack:
        return "rate_limited"
    if status_code and status_code >= 400:
        return "http_error"
    if str(summary.get("status") or "").lower() == "failed":
        return "safe_failure"
    return "none"


def _failure_reason_from_status(status_code: int | None) -> str:
    if status_code == 429:
        return "provider returned HTTP 429"
    if status_code:
        return f"provider returned HTTP {status_code}"
    return "none"


def _fallback_plan(postmortem: dict[str, Any]) -> dict[str, Any]:
    return _safe_json(
        {
            "status": "ready",
            "previous_provider": postmortem.get("provider"),
            "failure_type": postmortem.get("failure_type"),
            "rate_limit_summary": "GDELT controlled request was rate-limited and stopped safely.",
            "retry_gdelt_immediately": False,
            "cooldown_recommended": postmortem.get("cooldown_recommended"),
            "minimum_cooldown_minutes": postmortem.get("minimum_cooldown_minutes"),
            "fallback_provider": "official_directory",
            "fallback_constraints": {
                "max_requests": 1,
                "follow_links": False,
                "candidate_urls_accessed": False,
                "parse_official_directory_or_curated_index_only": True,
                "all_results_pending_review": True,
            },
            "if_continuing_gdelt": [
                "wait for cooldown",
                "obtain human review",
                "keep max_requests=1",
                "do not retry automatically",
            ],
        }
    )


def _render_postmortem_markdown(postmortem: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Live Source Discovery Postmortem",
            "",
            f"- Provider: {postmortem.get('provider')}",
            f"- Country: {postmortem.get('country')}",
            f"- Disease: {postmortem.get('disease')}",
            f"- Request count: {postmortem.get('request_count')}",
            f"- Network accessed: {postmortem.get('network_accessed')}",
            f"- Failure type: {postmortem.get('failure_type')}",
            f"- Failure reason: {postmortem.get('failure_reason')}",
            f"- Retry happened: {postmortem.get('whether_retry_happened')}",
            f"- Candidate URLs visited: {postmortem.get('whether_candidate_urls_were_visited')}",
            f"- Retry allowed: {postmortem.get('retry_allowed')}",
            f"- Cooldown recommended: {postmortem.get('cooldown_recommended')}",
            f"- Minimum cooldown minutes: {postmortem.get('minimum_cooldown_minutes')}",
            "",
            "## Recommended Next Step",
            "\n".join(f"- {item}" for item in postmortem.get("recommended_next_step", [])),
            "",
            "## Safety Notes",
            "- No response body is included in this report.",
            "- Candidate URLs were not accessed.",
            "- No retry or provider switch occurred during the live run.",
            "",
        ]
    )


def _render_fallback_markdown(plan: dict[str, Any]) -> str:
    constraints = plan.get("fallback_constraints", {})
    return "\n".join(
        [
            "# Fallback Readiness Plan",
            "",
            f"- Previous provider: {plan.get('previous_provider')}",
            f"- Failure type: {plan.get('failure_type')}",
            f"- Fallback provider: {plan.get('fallback_provider')}",
            f"- Retry GDELT immediately: {plan.get('retry_gdelt_immediately')}",
            f"- Cooldown recommended: {plan.get('cooldown_recommended')}",
            f"- Minimum cooldown minutes: {plan.get('minimum_cooldown_minutes')}",
            "",
            "## Fallback Constraints",
            f"- Max requests: {constraints.get('max_requests')}",
            f"- Follow links: {constraints.get('follow_links')}",
            f"- Candidate URLs accessed: {constraints.get('candidate_urls_accessed')}",
            f"- Parse official directory or curated index only: {constraints.get('parse_official_directory_or_curated_index_only')}",
            f"- All results pending_review: {constraints.get('all_results_pending_review')}",
            "",
            "## Continuing GDELT Later",
            "\n".join(f"- {item}" for item in plan.get("if_continuing_gdelt", [])),
            "",
        ]
    )


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed


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
    return text[:500]


if __name__ == "__main__":
    raise SystemExit(main())
