from __future__ import annotations

import json

from deepseek_web_search_agent.extraction_quality_audit import audit_visualization_points
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture


def test_visualization_audit_counts_points_and_forbidden_fields(tmp_path) -> None:
    report_dir, _database_url, _run_id = create_deepseek_audit_fixture(tmp_path)

    audit = audit_visualization_points(report_dir)

    assert audit.point_count == 2
    assert audit.safe_field_check_passed is True
    assert audit.forbidden_field_hits == []

    payload_path = report_dir / "visualization_3d_data.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["points"][0]["prompt"] = "forbidden raw model prompt"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    unsafe = audit_visualization_points(report_dir)
    assert unsafe.status == "unsafe"
    assert unsafe.safe_field_check_passed is False

