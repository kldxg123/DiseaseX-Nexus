from __future__ import annotations

from pathlib import Path


def main() -> None:
    p = Path("tools/p1_14e_r2_2.py")
    s = p.read_text(encoding="utf-8")
    start = s.index("def expected_deltas_r2_2")
    end = s.index("\n\ndef state_machine", start)
    new = '''def expected_deltas_r2_2(qr: dict[str, Any], dep: dict[str, Any], protected: dict[str, Any], trusted: dict[str, Any]) -> dict[str, Any]:
    dep_objects = {}
    for rs in dep["direct_instance_rowsets"]:
        dep_objects[f'{rs["schema"]}.{rs["table"]}:{rs["scope"]}'] = {"before_count": rs.get("row_count", 0), "before_digest": rs.get("before_digest"), "expected_delta": 0, "expected_after_count": rs.get("row_count", 0), "expected_after_digest": rs.get("expected_after_digest"), "target_uuid_hits": rs.get("target_uuid_hits", 0)}
    protected_objects = {name: {"before_count": rs.get("row_count", 0), "before_digest": rs.get("before_digest"), "expected_delta": 0, "expected_after_count": rs.get("expected_after_count", rs.get("row_count", 0)), "expected_after_digest": rs.get("expected_after_digest", rs.get("before_digest")), "target_uuid_hits": rs.get("target_uuid_hits", 0)} for name, rs in protected["rowsets"].items()}
    trusted_objects = {name: {"before_count": rs.get("row_count", 0), "before_digest": rs.get("before_digest"), "expected_delta": 0, "expected_after_count": rs.get("expected_after_count", rs.get("row_count", 0)), "expected_after_digest": rs.get("expected_after_digest", rs.get("before_digest")), "target_uuid_hits": rs.get("target_uuid_hits", 0)} for name, rs in trusted["objects"].items()}
    result = {
        "query_requests": {"before_count": 9, "before_digest": qr["row_set_sha256"], "delete_count": 3, "after_count": 6, "expected_delta": -3, "delete_id_set": TARGET_IDS, "target_row_sha256": qr["target_row_sha256"], "preserve_row_sha256": qr["legitimate_row_sha256"], "expected_after_digest": qr["survivor_row_set_sha256"], "surviving_row_set_sha256": qr["survivor_row_set_sha256"]},
        "dependency_objects": dep_objects,
        "dashboard_snapshot_catalog_v1": {"before_count": dep["dashboard_snapshot_catalog_v1"].get("row_count", 0), "before_digest": dep["dashboard_snapshot_catalog_v1"].get("before_digest"), "expected_delta": 0, "expected_after_count": dep["dashboard_snapshot_catalog_v1"].get("row_count", 0), "expected_after_digest": dep["dashboard_snapshot_catalog_v1"].get("expected_after_digest", dep["dashboard_snapshot_catalog_v1"].get("before_digest")), "target_uuid_hits": dep["dashboard_snapshot_catalog_v1"].get("target_uuid_hits", 0)},
        "p1_14c_protected_objects": protected_objects,
        "trusted_and_formal_objects": trusted_objects,
        "review_decision_approval_promotion_publication": {"review_decisions": 0, "approval": 0, "promotion_runs": 0, "promotion_events": 0, "publication": 0, "expected_delta": 0},
        "audit_mechanism": {"event_triggers": len(dep["event_triggers"]), "query_requests_triggers": len(dep["query_requests_related_triggers"]), "expected_delete_audit_delta": 0},
        "schema_revision_acl_service_status": {"schema_expected_delta": 0, "revision_expected_delta": 0, "acl_expected_delta": 0, "service_restart_expected": False},
    }
    result["sha256"] = sha_obj(result)
    return result
'''
    p.write_text(s[:start] + new + s[end:], encoding="utf-8")


if __name__ == "__main__":
    main()
