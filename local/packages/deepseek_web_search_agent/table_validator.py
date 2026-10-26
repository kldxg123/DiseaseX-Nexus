from __future__ import annotations

from datetime import date
from typing import Any

from deepseek_web_search_agent.structured_table_types import (
    DiseaseTimeQueryRequest,
    SourceEvidenceItem,
    TableValidationIssue,
)
from deepseek_web_search_agent.table_response_parser import NUMERIC_FIELDS


ERROR_SEVERITIES = {"error"}


def validate_extracted_table_rows(
    rows: list[dict[str, Any]],
    query_request: DiseaseTimeQueryRequest,
    sources: list[SourceEvidenceItem] | list[dict[str, Any]],
    *,
    low_confidence_threshold: float = 0.5,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_ids = {_source_id(source) for source in sources}
    issues: list[TableValidationIssue] = []
    validated: list[dict[str, Any]] = []
    for row in rows:
        row_id = str(row.get("row_id") or "unknown")
        row_issues: list[TableValidationIssue] = []
        row_date = _date_or_none(row.get("date"))
        if row_date is None:
            row_issues.append(_issue(row_id, "date", "missing_or_invalid_date", "error", "Date is required."))
        elif not (query_request.start_date <= row_date <= query_request.end_date):
            row_issues.append(_issue(row_id, "date", "out_of_range_date", "error", "Date is outside the query range."))
        if not row.get("country"):
            row_issues.append(_issue(row_id, "country", "missing_country", "error", "Country is required."))
        if not row.get("source_id"):
            row_issues.append(_issue(row_id, "source_id", "missing_source", "error", "Source ID is required."))
        elif str(row.get("source_id")) not in source_ids:
            row_issues.append(_issue(row_id, "source_id", "unknown_source", "error", "Source ID is not in source pack."))
        numeric_present = False
        for field in NUMERIC_FIELDS:
            value = row.get(field)
            if value is not None:
                numeric_present = True
                if not isinstance(value, int):
                    row_issues.append(_issue(row_id, field, "invalid_numeric", "error", "Numeric field must be an integer or null."))
                elif value < 0:
                    row_issues.append(_issue(row_id, field, "negative_numeric", "error", "Numeric field cannot be negative."))
        if not numeric_present:
            row_issues.append(_issue(row_id, "metrics", "missing_numeric_signal", "error", "At least one numeric field is required."))
        deaths = row.get("confirmed_deaths")
        cases = row.get("confirmed_cases")
        if isinstance(deaths, int) and isinstance(cases, int) and deaths > cases:
            row_issues.append(_issue(row_id, "confirmed_deaths", "deaths_exceed_cases", "warning", "Deaths exceed confirmed cases."))
        if not row.get("region"):
            row_issues.append(_issue(row_id, "region", "missing_region", "warning", "Region is empty; confirm during review."))
        if row.get("requires_human_review") is not True:
            row_issues.append(_issue(row_id, "requires_human_review", "human_review_required", "error", "Human review must remain required."))
        if float(row.get("extraction_confidence") or 0.0) < low_confidence_threshold:
            row_issues.append(_issue(row_id, "extraction_confidence", "low_confidence", "warning", "Low extraction confidence."))
        if row.get("date_source") == "report_date_fallback":
            row_issues.append(
                _issue(
                    row_id,
                    "date_source",
                    "report_date_fallback_requires_review",
                    "warning",
                    "Date uses report publication fallback; confirm during human review.",
                )
            )
            if float(row.get("extraction_confidence") or 0.0) > 0.7:
                row_issues.append(
                    _issue(
                        row_id,
                        "extraction_confidence",
                        "fallback_date_confidence_too_high",
                        "error",
                        "Rows using report_date_fallback must not be high confidence.",
                    )
                )
        if row.get("date_source") and not row.get("date_anchor_id"):
            row_issues.append(_issue(row_id, "date_anchor_id", "missing_date_anchor", "warning", "Date source is set but date anchor id is missing."))
        if row.get("metric_source") and not row.get("metric_anchor_id"):
            row_issues.append(_issue(row_id, "metric_anchor_id", "missing_metric_anchor", "warning", "Metric source is set but metric anchor id is missing."))
        if not row.get("evidence_summary"):
            row_issues.append(_issue(row_id, "evidence_summary", "insufficient_source_grounding", "error", "Evidence summary is required."))
        issues.extend(row_issues)
        has_error = any(issue.severity == "error" for issue in row_issues)
        updated = dict(row)
        updated["validation_status"] = "ready_for_approval" if not has_error else "needs_manual_fix"
        validated.append(updated)
    return validated, [issue.to_dict() for issue in issues]


def validation_summary(rows: list[dict[str, Any]], issues: list[dict[str, Any]]) -> dict[str, Any]:
    errors = [issue for issue in issues if issue.get("severity") == "error"]
    warnings = [issue for issue in issues if issue.get("severity") == "warning"]
    bundle_issues = [issue for issue in issues if issue.get("row_id") == "__bundle__"]
    return {
        "rows_total": len(rows),
        "ready_for_approval": sum(1 for row in rows if row.get("validation_status") == "ready_for_approval"),
        "needs_manual_fix": sum(1 for row in rows if row.get("validation_status") != "ready_for_approval"),
        "issues_count": len(issues),
        "errors_count": len(errors),
        "warnings_count": len(warnings),
        "bundle_issues_count": len(bundle_issues),
        "has_structured_rows": bool(rows),
    }


def row_has_blocking_issues(row_id: str, issues: list[dict[str, Any]]) -> bool:
    return any(issue.get("row_id") == row_id and issue.get("severity") == "error" for issue in issues)


def _source_id(source: SourceEvidenceItem | dict[str, Any]) -> str:
    if isinstance(source, SourceEvidenceItem):
        return source.source_id
    return str(source.get("source_id") or "")


def _date_or_none(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _issue(row_id: str, field: str, issue_type: str, severity: str, message: str) -> TableValidationIssue:
    return TableValidationIssue(row_id=row_id, field=field, issue_type=issue_type, severity=severity, message=message)
