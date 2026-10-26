from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any


QUERY_INTENTS = {
    "outbreak_information",
    "structured_time_series",
    "structured_numeric_evidence",
    "surveillance_summary",
    "data_source_discovery",
    "model_feature_evidence",
}


@dataclass(frozen=True)
class DeepSeekWebSearchRequest:
    disease: str
    country: str
    query_intent: str = "structured_time_series"
    search_backend: str = "auto"
    allow_deepseek_api: bool = False
    allow_general_web_search: bool = False
    allow_page_read: bool = False
    terms_reviewed: bool = False
    manual_reviewed: bool = False
    max_search_queries: int = 4
    max_search_results_per_query: int = 8
    max_pages_to_read: int = 8
    max_total_http_requests: int = 16
    max_readable_pages_success: int = 8
    stop_when_structured_candidates_found: bool = True
    prefer_official_and_structured_sources: bool = True
    prefer_situation_reports: bool = False
    prefer_dated_numeric_sources: bool = False
    include_document_candidates: bool = False
    do_not_download_documents: bool = True
    max_paragraph_signal_preview: int = 10
    output_dir: str = "reports/deepseek_web_search"
    database_url: str | None = None

    def validated(self) -> "DeepSeekWebSearchRequest":
        disease = _required_text(self.disease, "disease")
        country = _required_text(self.country, "country")
        intent = str(self.query_intent or "structured_time_series").strip()
        if intent not in QUERY_INTENTS:
            intent = "structured_time_series"
        return DeepSeekWebSearchRequest(
            disease=disease,
            country=country,
            query_intent=intent,
            search_backend=str(self.search_backend or "auto").strip().lower() or "auto",
            allow_deepseek_api=bool(self.allow_deepseek_api),
            allow_general_web_search=bool(self.allow_general_web_search),
            allow_page_read=bool(self.allow_page_read),
            terms_reviewed=bool(self.terms_reviewed),
            manual_reviewed=bool(self.manual_reviewed),
            max_search_queries=_bounded_int(self.max_search_queries, default=4, low=1, high=6),
            max_search_results_per_query=_bounded_int(
                self.max_search_results_per_query,
                default=8,
                low=1,
                high=8,
            ),
            max_pages_to_read=_bounded_int(self.max_pages_to_read, default=8, low=0, high=8),
            max_total_http_requests=_bounded_int(self.max_total_http_requests, default=16, low=0, high=16),
            max_readable_pages_success=_bounded_int(self.max_readable_pages_success, default=8, low=0, high=8),
            stop_when_structured_candidates_found=bool(self.stop_when_structured_candidates_found),
            prefer_official_and_structured_sources=bool(self.prefer_official_and_structured_sources),
            prefer_situation_reports=bool(self.prefer_situation_reports),
            prefer_dated_numeric_sources=bool(self.prefer_dated_numeric_sources),
            include_document_candidates=bool(self.include_document_candidates),
            do_not_download_documents=bool(self.do_not_download_documents),
            max_paragraph_signal_preview=_bounded_int(self.max_paragraph_signal_preview, default=10, low=1, high=10),
            output_dir=str(self.output_dir or "reports/deepseek_web_search"),
            database_url=self.database_url,
        )

    @property
    def live_allowed(self) -> bool:
        return bool(
            self.allow_deepseek_api
            and self.allow_general_web_search
            and self.terms_reviewed
            and self.manual_reviewed
        )


@dataclass(frozen=True)
class PlannedQuery:
    query_text: str
    query_rank: int
    query_intent: str
    generated_by: str = "fallback"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QueryPlan:
    disease: str
    country: str
    query_intent: str
    disease_aliases: list[str]
    country_aliases: list[str]
    search_queries: list[PlannedQuery]
    exclusion_terms: list[str]
    source_priorities: list[str]
    information_need: str
    expected_metrics: list[str]
    extraction_schema: dict[str, Any]
    quality_criteria: list[str]
    generated_by: str = "fallback"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["search_queries"] = [query.to_dict() for query in self.search_queries]
        return payload


@dataclass(frozen=True)
class GeneralWebSearchRequest:
    query: str
    disease: str
    country: str
    max_results: int = 8
    allow_network: bool = False


@dataclass(frozen=True)
class GeneralWebSearchResult:
    query: str
    rank: int
    title: str
    url: str
    domain: str
    snippet: str
    published_date: str | None = None
    source_type_hint: str | None = None
    provider: str = "mock"
    safe_metadata: dict[str, Any] = field(default_factory=dict)
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PageReadSummary:
    url: str
    domain: str
    title: str | None
    text_summary: str | None
    content_type: str | None
    request_count: int = 0
    status: str = "skipped"
    hints: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RankedSearchResult:
    result: GeneralWebSearchResult
    disease_relevance: float
    country_relevance: float
    authority: float
    data_likelihood: float
    structured_time_series_likelihood: float
    recency: float
    evidence_quality: float
    safety_risk: float
    total_score: float
    source_type: str
    selected_for_page_read: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(self.result.to_dict())
        payload.pop("result", None)
        return payload


@dataclass(frozen=True)
class ExtractedInformationRecord:
    disease: str
    country: str
    source_url: str
    source_domain: str
    source_title: str | None
    information_type: str
    evidence_type: str
    source_type: str | None
    event_date: date | None
    iso_week: str | None
    region_name: str | None
    metric_name: str | None
    metric_value: float | None
    metric_unit: str | None
    text_summary: str | None
    evidence_summary: str | None
    confidence: float
    uncertainty_reason: str | None
    cleaning_hint: str | None
    feature_ready_potential: bool
    requires_human_review: bool = True

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event_date"] = self.event_date.isoformat() if self.event_date else None
        return payload


@dataclass(frozen=True)
class DeepSeekWebSearchSummary:
    status: str
    run_id: str
    disease: str
    country: str
    intent: str
    search_backend: str
    deepseek_call_count: int
    search_request_count: int
    page_read_request_count: int
    records_extracted: int
    records_persisted: int
    raw_documents_created: int
    epidemic_events_created: int
    feature_ready_records: int
    visualization_points: int
    search_results_seen: int = 0
    readable_pages_success: int = 0
    blocked_pages_count: int = 0
    structured_candidate_count: int = 0
    warnings: list[str] = field(default_factory=list)
    paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required.")
    return text


def _bounded_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(low, min(high, number))
