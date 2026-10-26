from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any


DEFAULT_REQUESTED_FIELDS = [
    "date",
    "country",
    "region",
    "confirmed_cases",
    "confirmed_deaths",
    "suspected_cases",
    "contacts_under_monitoring",
    "source_id",
]


@dataclass(frozen=True)
class DiseaseTimeQueryRequest:
    disease: str
    country_or_region: str
    date_start: str
    date_end: str
    requested_fields: list[str] = field(default_factory=lambda: list(DEFAULT_REQUESTED_FIELDS))
    max_sources: int = 3
    allow_web_search: bool = False
    allow_page_read: bool = False
    allow_deepseek: bool = False
    require_human_approval: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def start_date(self) -> date:
        return date.fromisoformat(self.date_start)

    @property
    def end_date(self) -> date:
        return date.fromisoformat(self.date_end)


@dataclass(frozen=True)
class SourceEvidenceItem:
    source_id: str
    title_summary: str
    url_safe_summary: str
    domain: str
    published_date_hint: str | None = None
    accessed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    evidence_summary: str = ""
    source_type: str = "evidence_summary"
    confidence: float = 0.0
    full_text_saved: bool = False
    material_strength: str = "search_result_only"
    has_table_preview: bool = False
    has_csv_preview: bool = False
    has_json_preview: bool = False
    table_headers: list[str] = field(default_factory=list)
    table_preview_rows: list[dict[str, str]] = field(default_factory=list)
    table_preview_rows_count: int = 0
    paragraph_signal_preview: list[dict[str, Any]] = field(default_factory=list)
    paragraph_signal_preview_count: int = 0
    dated_numeric_signal_count: int = 0
    rows_with_date_and_numeric_hint: int = 0
    situation_report_hint: bool = False
    document_candidate: bool = False
    dated_signal_count: int = 0
    numeric_signal_count: int = 0
    date_or_week_terms: list[str] = field(default_factory=list)
    numeric_terms: list[str] = field(default_factory=list)
    extraction_readiness: str = "insufficient_for_table_extraction"
    extraction_blockers: list[str] = field(default_factory=list)
    extraction_readiness_reason: str | None = None
    why_not_ready: list[str] = field(default_factory=list)
    recommended_next_action: str = "search_structured_source"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExtractedEventTableRow:
    row_id: str
    date: str | None
    country: str | None
    region: str | None
    confirmed_cases: int | None
    confirmed_deaths: int | None
    suspected_cases: int | None
    contacts_under_monitoring: int | None
    source_id: str | None
    disease: str
    extraction_confidence: float
    evidence_summary: str
    uncertainty_flags: list[str] = field(default_factory=list)
    date_source: str | None = None
    date_anchor_id: str | None = None
    metric_anchor_id: str | None = None
    date_confidence: str | None = None
    metric_source: str | None = None
    requires_human_review: bool = True
    approval_status: str = "pending_review"
    reviewer_note: str | None = None
    validation_status: str = "needs_validation"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TableValidationIssue:
    row_id: str
    field: str
    issue_type: str
    severity: str
    message: str
    suggested_fix: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StructuredTableReviewBundle:
    query_request: dict[str, Any]
    sources: list[dict[str, Any]]
    extracted_rows: list[dict[str, Any]]
    cleaned_rows: list[dict[str, Any]]
    validation_issues: list[dict[str, Any]]
    duplicate_groups: list[dict[str, Any]]
    approval_status_summary: dict[str, Any]
    report_paths: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
