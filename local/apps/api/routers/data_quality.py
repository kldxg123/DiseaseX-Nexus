from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from apps.api.deps import get_db_session
from apps.api.safety import sanitize_mapping
from apps.api.schemas import DataQualityReportRequest
from data_quality_agent.label_audit import audit_model_feature_labels
from data_quality_agent.profile import build_data_quality_profile
from data_quality_agent.report import (
    build_data_quality_report,
    data_quality_profile_to_dict,
    label_audit_to_dict,
    write_report_files,
)

router = APIRouter(tags=["data-quality"])


@router.get("/data-quality/profile")
def data_quality_profile(session: Session = Depends(get_db_session)) -> dict:
    profile = data_quality_profile_to_dict(build_data_quality_profile(session))
    return {
        "profile": sanitize_mapping(profile, max_items=200),
        "limitations": "Data-quality profiling is local and summary-only; human review is required.",
    }


@router.get("/data-quality/label-audit")
def data_quality_label_audit(
    country: str | None = Query(default=None),
    disease: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> dict:
    audit = label_audit_to_dict(audit_model_feature_labels(session, country=country, disease=disease))
    return {
        "label_audit": sanitize_mapping(audit, max_items=200),
        "limitations": "Rule-based label audit only; it does not guarantee label correctness.",
    }


@router.post("/data-quality/report")
def data_quality_report(
    request: DataQualityReportRequest,
    session: Session = Depends(get_db_session),
) -> dict:
    report = build_data_quality_report(session, country=request.country, disease=request.disease)
    summary = sanitize_mapping(report.get("summary", {}), max_items=80)
    payload = {
        "summary": summary,
        "warnings": sanitize_mapping(report.get("warnings", []), max_items=80),
        "limitations": report.get("limitations", []),
        "write_files": bool(request.write_files),
    }
    if request.write_files:
        payload["files"] = sanitize_mapping(
            write_report_files(report, output_dir=request.output_dir),
            max_items=80,
        )
    return payload
