from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from deepseek_web_search_agent.structured_table_types import (
    DiseaseTimeQueryRequest,
    SourceEvidenceItem,
    StructuredTableReviewBundle,
)
from deepseek_web_search_agent.table_visualization import write_table_visualization_files

REVIEW_COLUMNS = [
    "row_id",
    "date",
    "country",
    "region",
    "confirmed_cases",
    "confirmed_deaths",
    "suspected_cases",
    "contacts_under_monitoring",
    "source_id",
    "disease",
    "extraction_confidence",
    "validation_status",
    "approval_status",
    "reviewer_note",
    "evidence_summary",
    "uncertainty_flags",
    "source_url_summary",
    "source_domain",
    "source_type",
    "date_source",
    "date_anchor_id",
    "metric_anchor_id",
    "date_confidence",
    "metric_source",
    "requires_human_review",
]


def approval_status_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("approval_status") or "pending_review")
        counts[status] = counts.get(status, 0) + 1
    return {
        "total_rows": len(rows),
        "pending_review": counts.get("pending_review", 0),
        "approved": counts.get("approved", 0),
        "rejected": counts.get("rejected", 0),
        "edit_required": counts.get("edit_required", 0),
        "requires_human_approval": True,
    }


def write_structured_table_review(
    *,
    output_dir: str | Path,
    query_request: DiseaseTimeQueryRequest,
    sources: list[SourceEvidenceItem],
    extracted_rows: list[dict[str, Any]],
    cleaned_rows: list[dict[str, Any]],
    validation_issues: list[dict[str, Any]],
    duplicate_groups: list[dict[str, Any]],
    prompt_hash: str | None = None,
    prompt_summary: dict[str, Any] | None = None,
    deepseek_called: bool = False,
    warnings: list[str] | None = None,
    source_pack_readiness: dict[str, Any] | None = None,
    deterministic_rows_generated: int = 0,
    deepseek_rows_generated: int = 0,
    extraction_mode: str = "deepseek_table_extraction",
) -> StructuredTableReviewBundle:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "query_request": str(root / "query_request.json"),
        "source_pack": str(root / "source_pack.json"),
        "source_map": str(root / "source_map.json"),
        "deepseek_table_extraction_summary": str(root / "deepseek_table_extraction_summary.json"),
        "review_json": str(root / "structured_event_table_review.json"),
        "review_csv": str(root / "structured_event_table_review.csv"),
        "review_markdown": str(root / "structured_event_table_review.md"),
        "validation_issues": str(root / "validation_issues.json"),
        "duplicate_report": str(root / "duplicate_report.json"),
        "approval_status_summary": str(root / "approval_status_summary.json"),
        "rollback_commands": str(root / "rollback_commands.md"),
        "ingestion_plan": str(root / "ingestion_plan.md"),
        "safety_audit": str(root / "safety_audit.md"),
        "next_action_plan": str(root / "next_action_plan.md"),
    }
    source_rows = [source.to_dict() for source in sources]
    cleaned_rows = _enrich_rows_with_sources(cleaned_rows, sources)
    readiness = dict(source_pack_readiness or {})
    validation_issues = list(validation_issues)
    if not cleaned_rows:
        validation_issues.extend(_zero_row_issues(readiness))
    summary = {
        "status": "ok",
        "deepseek_called": bool(deepseek_called),
        "deepseek_call_count": 1 if deepseek_called else 0,
        "extraction_mode": extraction_mode,
        "deterministic_rows_generated": int(deterministic_rows_generated),
        "deepseek_rows_generated": int(deepseek_rows_generated),
        "extraction_instruction_hash": prompt_hash,
        "extraction_instruction_summary": prompt_summary or {},
        "rows_extracted": len(extracted_rows),
        "cleaned_rows": len(cleaned_rows),
        "validation_issues_count": len(validation_issues),
        "duplicate_groups_count": len(duplicate_groups),
        "requires_human_approval": True,
        "source_pack_readiness": readiness,
        "warnings": list(warnings or []),
    }
    approval_summary = approval_status_summary(cleaned_rows)
    _write_json(paths["query_request"], query_request.to_dict())
    _write_json(paths["source_pack"], {"sources": source_rows})
    _write_json(paths["source_map"], {"sources": source_rows})
    _write_json(paths["deepseek_table_extraction_summary"], summary)
    _write_json(paths["review_json"], {"rows": cleaned_rows})
    _write_review_csv(paths["review_csv"], cleaned_rows)
    _write_review_markdown(paths["review_markdown"], cleaned_rows)
    _write_json(paths["validation_issues"], {"issues": validation_issues})
    _write_json(paths["duplicate_report"], {"duplicate_groups": duplicate_groups})
    _write_json(paths["approval_status_summary"], approval_summary)
    _write_text(paths["rollback_commands"], _rollback_markdown())
    _write_text(paths["ingestion_plan"], _ingestion_plan_markdown(approval_summary))
    _write_text(paths["safety_audit"], _safety_audit_markdown(summary))
    _write_text(paths["next_action_plan"], _next_action_plan_markdown(validation_issues, duplicate_groups))
    paths.update(write_table_visualization_files(root, cleaned_rows, sources))
    return StructuredTableReviewBundle(
        query_request=query_request.to_dict(),
        sources=source_rows,
        extracted_rows=extracted_rows,
        cleaned_rows=cleaned_rows,
        validation_issues=validation_issues,
        duplicate_groups=duplicate_groups,
        approval_status_summary=approval_summary,
        report_paths=paths,
    )


def load_review_table(path: str | Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else payload
    return [dict(row) for row in rows] if isinstance(rows, list) else []


def _zero_row_issues(source_pack_readiness: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = set(source_pack_readiness.get("source_pack_level_blockers") or [])
    issues = [
        _bundle_issue("no_structured_rows_extracted", "No structured event rows were extracted from the available source pack."),
    ]
    if source_pack_readiness.get("source_count", 0) <= 0:
        issues.append(_bundle_issue("search_found_but_no_pages_read", "No readable page or source-pack material was available for extraction."))
    if "search_result_only_material" in blockers or "empty_source_pack" in blockers:
        issues.append(_bundle_issue("insufficient_source_material", "Source material is too weak for table extraction."))
    if "no_dated_signal" in blockers:
        issues.append(_bundle_issue("pages_read_but_no_date_signal", "Sources were available, but no explicit date or week signal was detected."))
    if "no_numeric_signal" in blockers:
        issues.append(_bundle_issue("pages_read_but_no_numeric_signal", "Sources were available, but no explicit case, death, suspected, contact, or comparable numeric signal was detected."))
    if "numeric_signal_without_date" in blockers:
        issues.append(_bundle_issue("numeric_signal_without_date", "Numeric disease indicators were detected without a usable date or week."))
    if "date_signal_without_numeric" in blockers:
        issues.append(_bundle_issue("date_signal_without_numeric", "Date or week signals were detected without a usable numeric metric."))
    if "api_hint_without_data" in blockers:
        issues.append(_bundle_issue("api_hint_without_data", "The source looked like an API or JSON page, but no extractable rows or numeric preview were available."))
    if "document_candidate_not_downloaded" in blockers:
        issues.append(_bundle_issue("document_candidate_not_downloaded", "The source appears to be a document candidate, but document download is disabled for this controlled run."))
    if "needs_structured_document_or_dataset" in blockers:
        issues.append(_bundle_issue("needs_structured_document_or_dataset", "The source pack needs a structured table, CSV, JSON row preview, or dated numeric paragraph material."))
    if "no_table_csv_json_preview" in blockers:
        issues.append(_bundle_issue("no_table_csv_json_preview", "No table, CSV, or JSON preview was available."))
    if len(issues) == 1:
        issues.append(_bundle_issue("insufficient_source_material", "The source pack did not contain enough structured evidence for row extraction."))
    return issues


def _bundle_issue(issue_type: str, message: str) -> dict[str, Any]:
    return {
        "row_id": "__bundle__",
        "field": "source_pack",
        "issue_type": issue_type,
        "severity": "error",
        "message": message,
        "suggested_fix": "Use page_read_summary, metadata check, or a CSV/table/API candidate before table extraction.",
    }


def _enrich_rows_with_sources(rows: list[dict[str, Any]], sources: list[SourceEvidenceItem]) -> list[dict[str, Any]]:
    by_id = {source.source_id: source for source in sources}
    enriched: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        source = by_id.get(str(item.get("source_id") or ""))
        if source:
            item.setdefault("source_url_summary", source.url_safe_summary)
            item.setdefault("source_domain", source.domain)
            item.setdefault("source_type", source.source_type)
        item.setdefault("requires_human_review", True)
        enriched.append(item)
    return enriched


def _write_json(path: str | Path, payload: Any) -> None:
    Path(path).write_text(json.dumps(_safe(payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_text(path: str | Path, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def _write_review_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _csv_value(row.get(key)) for key in REVIEW_COLUMNS})


def _write_review_markdown(path: str | Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Structured Event Table Review",
        "",
        "| row_id | date | country | region | confirmed_cases | deaths | source_id | validation | approval |",
        "|---|---|---|---|---:|---:|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {row_id} | {date} | {country} | {region} | {cases} | {deaths} | {source} | {validation} | {approval} |".format(
                row_id=row.get("row_id") or "",
                date=row.get("date") or "",
                country=row.get("country") or "",
                region=row.get("region") or "",
                cases=row.get("confirmed_cases") if row.get("confirmed_cases") is not None else "",
                deaths=row.get("confirmed_deaths") if row.get("confirmed_deaths") is not None else "",
                source=row.get("source_id") or "",
                validation=row.get("validation_status") or "",
                approval=row.get("approval_status") or "",
            )
        )
    _write_text(path, "\n".join(lines) + "\n")


def _ingestion_plan_markdown(summary: dict[str, Any]) -> str:
    return (
        "# Ingestion Plan\n\n"
        "- Default state: staging review only.\n"
        "- Only rows with `approval_status=approved` may be ingested.\n"
        "- Ingestion requires explicit `allow_persistence` and a local/pilot database URL.\n"
        f"- Pending review rows: {summary.get('pending_review', 0)}.\n"
    )


def _safety_audit_markdown(summary: dict[str, Any]) -> str:
    return (
        "# Safety Audit\n\n"
        f"- DeepSeek called in this run: {summary.get('deepseek_called', False)}.\n"
        "- Full extraction instructions and model output are not saved.\n"
        "- Candidate URLs are not visited by this review writer.\n"
        "- Full HTML/raw text/response body is not included in review outputs.\n"
    )


def _next_action_plan_markdown(issues: list[dict[str, Any]], duplicates: list[dict[str, Any]]) -> str:
    return (
        "# Next Action Plan\n\n"
        "- Review rows in the CSV/JSON table.\n"
        "- Fix rows with validation errors before approval.\n"
        f"- Validation issues: {len(issues)}.\n"
        f"- Duplicate/conflict groups: {len(duplicates)}.\n"
        "- Approve only source-grounded rows, then run approved-row ingestion with explicit persistence.\n"
    )


def _rollback_markdown() -> str:
    return (
        "# Rollback Commands\n\n"
        "- This staging run does not write to the database by default.\n"
        "- Delete this local report directory after manual confirmation if rollback is needed.\n"
        "- No remote rollback is required.\n"
    )


def _csv_value(value: Any) -> Any:
    if isinstance(value, list):
        return ";".join(str(item) for item in value)
    return value


def _safe(value: Any) -> Any:
    forbidden = {
        "raw_text",
        "html",
        "metadata_json",
        "feature_json",
        "provenance_json",
        "lesson_json",
        "prompt",
        "completion",
        "response_body",
    }
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items() if str(key).lower() not in forbidden}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        return value if len(value) <= 500 else value[:497] + "..."
    return value
