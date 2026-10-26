from __future__ import annotations

from fastapi import APIRouter

from apps.api.schemas import AccessPolicyCheckRequest
from crawler_agent import (
    LiveFetchGuard,
    load_access_policies,
    safe_policy_summary,
    validate_access_policy,
)
from crawler_agent.access_policy import AccessDecision

router = APIRouter(tags=["Access Policy"])


@router.get("/access-policy")
def list_access_policies() -> dict:
    policies = load_access_policies()
    errors = []
    for policy in policies.values():
        errors.extend(validate_access_policy(policy))
    return {
        "status": "ok" if not errors else "failed",
        "policies_checked": len(policies),
        "errors": errors,
        "policies": [safe_policy_summary(policy) for policy in policies.values()],
        "limitations": (
            "Static local policy summary only. This endpoint does not perform live network access."
        ),
    }


@router.post("/access-policy/check")
def check_access_policy(request: AccessPolicyCheckRequest) -> dict:
    guard = LiveFetchGuard(load_access_policies())
    decision = guard.check(
        request.source_key,
        allow_network=request.allow_network,
        requested_url=request.url,
        robots_allowed=request.robots_allowed,
        terms_reviewed=request.terms_reviewed,
        manual_reviewed=request.manual_reviewed,
        request_count=request.request_count,
    )
    return {
        "status": "ok",
        "decision": _decision_payload(decision),
        "audit_summary": guard.audit_log.summary(),
        "limitations": "Guardrail decision only. No HTTP request was made.",
    }


def _decision_payload(decision: AccessDecision) -> dict:
    return {
        "allowed": decision.allowed,
        "reason": decision.reason,
        "warnings": decision.warnings,
        "policy_summary": decision.policy_summary,
    }
