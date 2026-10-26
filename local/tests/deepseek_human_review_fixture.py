from __future__ import annotations

import json
from pathlib import Path

from common.db import get_session
from common.models import EpidemicEvent
from deepseek_web_search_agent.extraction_quality_audit import write_deepseek_quality_audit_report
from deepseek_web_search_agent.human_review_promotion import export_human_review_template
from deepseek_web_search_agent.readiness_review import write_readiness_review_reports
from tests.deepseek_audit_fixture import create_deepseek_audit_fixture


def create_human_review_fixture(tmp_path: Path) -> tuple[Path, str, Path]:
    report_dir, database_url, _run_id = create_deepseek_audit_fixture(tmp_path)
    write_deepseek_quality_audit_report(report_dir, database_url=database_url)
    write_readiness_review_reports(report_dir)
    output_dir = report_dir / "human_review"
    export_human_review_template(report_dir, output_dir)
    return report_dir, database_url, output_dir


def make_annotation_file(
    template_file: Path,
    output_file: Path,
    *,
    decision: str = "approve_event",
    include_date: bool = True,
    include_value: bool = True,
    source_grounded: bool = True,
) -> Path:
    payload = json.loads(template_file.read_text(encoding="utf-8"))
    for index, row in enumerate(payload["annotations"]):
        annotation = row["annotation"]
        if index == 0:
            annotation.update(
                {
                    "reviewer_decision": decision,
                    "confirmed_disease": annotation.get("confirmed_disease") or row.get("disease"),
                    "confirmed_country": annotation.get("confirmed_country") or row.get("country"),
                    "confirmed_event_date": "2026-03-02" if include_date else "",
                    "confirmed_iso_week": "2026-W10" if include_date else "",
                    "metric_type": "cases",
                    "metric_value": 12 if include_value else "",
                    "value_unit": "count",
                    "source_grounding_confirmed": source_grounded,
                    "evidence_support_note": "Local reviewer confirmed safe public evidence summary.",
                    "reviewer_note": "local test note",
                    "reviewed_by": "local_reviewer",
                    "reviewed_at": "2026-07-08T00:00:00Z",
                }
            )
        else:
            annotation["reviewer_decision"] = "keep_evidence_only"
    output_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_file


def count_events(database_url: str) -> int:
    with get_session(database_url) as session:
        return session.query(EpidemicEvent).count()
