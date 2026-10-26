from __future__ import annotations

import json
from datetime import UTC, datetime

from crawler_agent.network_audit import (
    InMemoryNetworkAuditLog,
    NetworkAuditRecord,
    safe_audit_record,
)


def test_safe_audit_record_drops_sensitive_query_values() -> None:
    record = NetworkAuditRecord(
        source_key="cdc_norostat",
        url="https://REDACTED@example.invalid/path?token=hidden&password=hidden",
        allowed=False,
        reason="denied without credential detail",
        request_count=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        warnings=["contains secret marker"],
    )

    safe = safe_audit_record(record)
    dumped = json.dumps(safe).lower()

    assert safe["url"] == "https://example.invalid/path"
    assert "hidden" not in dumped
    assert "token" not in dumped
    assert "password" not in dumped
    assert safe["warnings"] == ["[redacted]"]


def test_in_memory_audit_summary_counts_by_source() -> None:
    audit_log = InMemoryNetworkAuditLog()
    audit_log.record(
        NetworkAuditRecord("gdelt", None, False, "denied", 1, datetime.now(UTC), [])
    )
    audit_log.record(
        NetworkAuditRecord("gdelt", None, True, "allowed", 1, datetime.now(UTC), [])
    )
    audit_log.record(
        NetworkAuditRecord("cdc_nors", None, False, "denied", 1, datetime.now(UTC), [])
    )

    assert audit_log.summary() == {
        "total": 3,
        "allowed": 1,
        "denied": 2,
        "by_source": {"cdc_nors": 1, "gdelt": 2},
    }
    assert len(audit_log.list_records(limit=2)) == 2
