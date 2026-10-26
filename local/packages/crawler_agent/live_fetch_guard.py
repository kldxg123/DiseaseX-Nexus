from __future__ import annotations

from datetime import UTC, datetime

from crawler_agent.access_policy import AccessDecision, SourceAccessPolicy, decide_live_access
from crawler_agent.network_audit import InMemoryNetworkAuditLog, NetworkAuditRecord


class LiveFetchGuard:
    def __init__(
        self,
        policies: dict[str, SourceAccessPolicy],
        audit_log: InMemoryNetworkAuditLog | None = None,
    ):
        self.policies = dict(policies)
        self.audit_log = audit_log or InMemoryNetworkAuditLog()

    def check(
        self,
        source_key: str,
        *,
        allow_network: bool,
        requested_url: str | None = None,
        robots_allowed: bool | None = None,
        terms_reviewed: bool = False,
        manual_reviewed: bool = False,
        request_count: int = 1,
    ) -> AccessDecision:
        policy = self.policies.get(source_key)
        if policy is None:
            decision = AccessDecision(
                allowed=False,
                reason="unknown source access policy",
                warnings=[],
                policy_summary={"source": source_key},
            )
        else:
            decision = decide_live_access(
                policy,
                allow_network=allow_network,
                requested_url=requested_url,
                robots_allowed=robots_allowed,
                terms_reviewed=terms_reviewed,
                manual_reviewed=manual_reviewed,
                request_count=request_count,
            )
        self.audit_log.record(
            NetworkAuditRecord(
                source_key=source_key,
                url=requested_url,
                allowed=decision.allowed,
                reason=decision.reason,
                request_count=request_count,
                created_at=datetime.now(UTC),
                warnings=decision.warnings,
            )
        )
        return decision

    def require_allowed(self, source_key: str, **kwargs) -> AccessDecision:
        decision = self.check(source_key, **kwargs)
        if not decision.allowed:
            raise PermissionError(f"live access denied: {decision.reason}")
        return decision
