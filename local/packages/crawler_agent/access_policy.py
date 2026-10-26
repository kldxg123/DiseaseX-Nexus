from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = PROJECT_ROOT / "configs" / "source_access_policy.example.json"

SENSITIVE_MARKERS = (
    "secret",
    "token",
    "password",
    "private key",
    "ssh_key_path",
    "credential",
)


@dataclass(frozen=True)
class SourceAccessPolicy:
    source_key: str
    name: str
    base_url: str
    allowed_access_method: str
    live_fetch_default_enabled: bool
    requires_allow_network_flag: bool
    robots_check_required: bool
    terms_review_required: bool
    manual_review_required: bool
    max_requests_per_run: int
    min_interval_seconds: float
    allowed_domains: tuple[str, ...]
    disallowed_paths: tuple[str, ...]
    notes: str | None = None


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str
    warnings: list[str]
    policy_summary: dict


def load_access_policies(path: str | None = None) -> dict[str, SourceAccessPolicy]:
    policy_path = Path(path) if path else DEFAULT_POLICY_PATH
    payload = json.loads(policy_path.read_text(encoding="utf-8"))
    entries = payload.get("sources", payload)
    if isinstance(entries, dict):
        entries = list(entries.values())
    if not isinstance(entries, list):
        raise ValueError("policy file must contain a list of sources.")

    policies: dict[str, SourceAccessPolicy] = {}
    for entry in entries:
        policy = _policy_from_mapping(entry)
        errors = validate_access_policy(policy)
        if errors:
            raise ValueError(f"invalid policy for {policy.source_key}: {'; '.join(errors)}")
        policies[policy.source_key] = policy
    return policies


def validate_access_policy(policy: SourceAccessPolicy) -> list[str]:
    errors: list[str] = []
    if not policy.source_key:
        errors.append("source identifier is required")
    if not policy.name:
        errors.append("source name is required")
    if not policy.base_url:
        errors.append("base URL is required")
    else:
        parsed_base = urlsplit(policy.base_url)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.hostname:
            errors.append("base URL must be an absolute HTTP(S) URL")
    if not policy.allowed_access_method:
        errors.append("allowed access method is required")
    if policy.live_fetch_default_enabled:
        errors.append("live fetch must be disabled by default")
    if not policy.requires_allow_network_flag:
        errors.append("explicit network flag must be required")
    if policy.max_requests_per_run < 1:
        errors.append("max requests per run must be at least 1")
    if policy.min_interval_seconds < 0:
        errors.append("minimum interval must be non-negative")
    if not policy.allowed_domains:
        errors.append("at least one allowed domain is required")
    for domain in policy.allowed_domains:
        if not domain or "/" in domain or ":" in domain:
            errors.append("allowed domains must be host names only")
            break
    return errors


def safe_policy_summary(policy: SourceAccessPolicy) -> dict:
    return {
        "source": _sanitize_text(policy.source_key),
        "name": _sanitize_text(policy.name),
        "base_url": _sanitize_url(policy.base_url),
        "allowed_access_method": _sanitize_text(policy.allowed_access_method),
        "live_fetch_default_enabled": policy.live_fetch_default_enabled,
        "requires_allow_network_flag": policy.requires_allow_network_flag,
        "robots_check_required": policy.robots_check_required,
        "terms_review_required": policy.terms_review_required,
        "manual_review_required": policy.manual_review_required,
        "max_requests_per_run": policy.max_requests_per_run,
        "min_interval_seconds": policy.min_interval_seconds,
        "allowed_domains": [_sanitize_text(domain) for domain in policy.allowed_domains],
        "disallowed_paths": [_sanitize_text(path) for path in policy.disallowed_paths],
        "notes": _sanitize_text(policy.notes) if policy.notes else None,
    }


def decide_live_access(
    policy: SourceAccessPolicy,
    *,
    allow_network: bool,
    requested_url: str | None = None,
    robots_allowed: bool | None = None,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    request_count: int = 1,
) -> AccessDecision:
    warnings = validate_access_policy(policy)
    summary = safe_policy_summary(policy)
    if warnings:
        return AccessDecision(False, "source access policy is invalid", warnings, summary)
    if not allow_network:
        return AccessDecision(False, "live network access was not explicitly allowed", [], summary)
    if policy.requires_allow_network_flag and not allow_network:
        return AccessDecision(False, "explicit network flag is required", [], summary)
    if policy.manual_review_required and not manual_reviewed:
        return AccessDecision(False, "manual review has not been completed", [], summary)
    if policy.terms_review_required and not terms_reviewed:
        return AccessDecision(False, "terms review has not been completed", [], summary)
    if policy.robots_check_required and robots_allowed is not True:
        return AccessDecision(False, "robots access check has not passed", [], summary)
    if request_count > policy.max_requests_per_run:
        return AccessDecision(False, "request count exceeds policy limit", [], summary)
    if request_count < 1:
        return AccessDecision(False, "request count must be positive", [], summary)
    if requested_url:
        url_error = _validate_requested_url(policy, requested_url)
        if url_error:
            return AccessDecision(False, url_error, [], summary)
    return AccessDecision(True, "live access guard checks passed", [], summary)


def _policy_from_mapping(entry: Any) -> SourceAccessPolicy:
    if not isinstance(entry, dict):
        raise ValueError("policy entry must be an object.")
    required_fields = {
        "source_key",
        "name",
        "base_url",
        "allowed_access_method",
        "live_fetch_default_enabled",
        "requires_allow_network_flag",
        "robots_check_required",
        "terms_review_required",
        "manual_review_required",
        "max_requests_per_run",
        "min_interval_seconds",
        "allowed_domains",
        "disallowed_paths",
    }
    missing = sorted(required_fields - set(entry))
    if missing:
        raise ValueError(f"policy entry is missing required fields: {', '.join(missing)}")
    return SourceAccessPolicy(
        source_key=str(entry["source_key"]),
        name=str(entry["name"]),
        base_url=str(entry["base_url"]),
        allowed_access_method=str(entry["allowed_access_method"]),
        live_fetch_default_enabled=bool(entry["live_fetch_default_enabled"]),
        requires_allow_network_flag=bool(entry["requires_allow_network_flag"]),
        robots_check_required=bool(entry["robots_check_required"]),
        terms_review_required=bool(entry["terms_review_required"]),
        manual_review_required=bool(entry["manual_review_required"]),
        max_requests_per_run=int(entry["max_requests_per_run"]),
        min_interval_seconds=float(entry["min_interval_seconds"]),
        allowed_domains=tuple(str(domain).lower() for domain in entry["allowed_domains"]),
        disallowed_paths=tuple(str(path) for path in entry["disallowed_paths"]),
        notes=str(entry["notes"]) if entry.get("notes") is not None else None,
    )


def _validate_requested_url(policy: SourceAccessPolicy, requested_url: str) -> str | None:
    parsed = urlsplit(requested_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not hostname:
        return "requested URL must be an absolute HTTP(S) URL"
    if hostname not in {domain.lower() for domain in policy.allowed_domains}:
        return "requested URL domain is not allowed by policy"
    path = parsed.path or "/"
    if any(disallowed and disallowed in path for disallowed in policy.disallowed_paths):
        return "requested URL path is disallowed by policy"
    return None


def _sanitize_url(value: str) -> str:
    parsed = urlsplit(str(value))
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path or "", "", ""))


def _sanitize_text(value: object) -> str:
    text = str(value)
    for marker in SENSITIVE_MARKERS:
        if marker.lower() in text.lower():
            return "[redacted]"
    return text
