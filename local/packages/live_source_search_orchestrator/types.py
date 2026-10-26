from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class LiveSourceSearchRequest:
    country: str
    disease: str
    providers: str = "auto"
    preferred_live_disease: str | None = None
    max_total_requests: int = 3
    dry_run: bool = True
    allow_network: bool = False
    terms_reviewed: bool = False
    manual_reviewed: bool = False
    include_rate_limited_provider: bool = False
    curated_official_directory_url: str | None = None
    metadata_check: bool = False
    max_metadata_check_requests: int = 3
    metadata_check_top_n: int = 3
    min_source_score: float = 0.35
    output_dir: str = "reports/live_source_search"
    database_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DiseaseAliasSet:
    disease: str
    aliases: list[str]
    query_terms: list[str]
    alias_confidence: float
    unknown_disease_alias_expansion: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderPlan:
    providers_requested: str
    providers_planned: list[str]
    max_total_requests: int
    per_provider_max_requests: int = 1
    skipped_providers: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderSearchResult:
    provider: str
    request_count: int
    search_results: list[dict[str, Any]]
    network_accessed: bool
    status: str = "unknown"
    failure_type: str | None = None
    http_status_code: int | None = None
    retry_allowed: bool = False
    safe_failure: bool = True
    sanitized_error_summary: str | None = None
    candidate_urls_visited: bool = False
    follow_links: bool = False
    warnings: list[str] = field(default_factory=list)
    metadata_summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateEvidence:
    provider: str
    matched_aliases: list[str]
    matched_country_terms: list[str]
    data_keywords: list[str]
    rank: int | None
    snippet_summary: str | None
    evidence_summary: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SourceTypePrediction:
    source_type: str
    parser_strategy: str
    confidence: float
    evidence_keywords: list[str]
    needs_metadata_check: bool
    recommended_next_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NormalizedSourceCandidate:
    candidate_id: str
    normalized_title: str
    normalized_url: str
    domain: str
    provider: str
    source_name: str
    snippet_summary: str | None
    country_mentions: list[str]
    disease_mentions: list[str]
    data_keywords: list[str]
    authority_domain: bool
    candidate_source_type: str
    parser_strategy: str
    raw_rank: int | None
    authority_score: float = 0.0
    disease_relevance_score: float = 0.0
    country_relevance_score: float = 0.0
    data_likelihood_score: float = 0.0
    dated_numeric_likelihood_score: float = 0.0
    table_or_dataset_likelihood_score: float = 0.0
    situation_report_score: float = 0.0
    feature_ready_evidence_score: float = 0.0
    freshness_score: float = 0.0
    compliance_risk_score: float = 0.0
    total_source_score: float = 0.0
    refined_score: float = 0.0
    source_type_confidence: float = 0.0
    evidence_summary: str = ""
    recommended_action: str = "manual_review"
    quality_gate_status: str = "pass"
    quality_penalties: list[str] = field(default_factory=list)
    quality_boosts: list[str] = field(default_factory=list)
    rejection_reason: str | None = None
    metadata_check_status: str | None = None
    data_source_likelihood: float | None = None
    requires_human_review: bool = True
    registry_status: str = "pending_review"
    connector_spec: dict[str, Any] | None = None
    merged_from_providers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveSourceSearchDecision:
    status: str
    reason: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewQueueItem:
    candidate_id: str
    title: str
    domain: str
    source_score: float
    source_type: str
    parser_strategy: str
    evidence_summary: str
    recommended_action: str
    risk_flags: list[str]
    requires_human_review: bool
    suggested_next_step: str
    registry_status: str
    quality_gate_status: str = "pass"
    refined_score: float = 0.0
    metadata_check_status: str | None = None
    data_source_likelihood: float | None = None
    rejection_reason: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LiveSourceSearchReport:
    status: str
    run_id: str
    country: str
    disease: str
    aliases_used: list[str]
    providers_planned: list[str]
    providers_used: list[str]
    request_count_total: int
    provider_request_counts: dict[str, int]
    candidates_seen: int
    candidates_after_dedupe: int
    pending_review_inserted: int
    connector_specs_generated: int
    top_domains: list[str]
    failure_types: dict[str, str | None]
    warnings: list[str]
    quality_gate_passed: int = 0
    quality_gate_rejected: int = 0
    quality_gate_deprioritized: int = 0
    metadata_check_executed: bool = False
    metadata_check_request_count: int = 0
    metadata_confirmed_data_sources: int = 0
    metadata_rejected_non_data_sources: int = 0
    refined_top_domains: list[str] = field(default_factory=list)
    candidate_urls_visited_for_metadata_check: bool = False
    files_downloaded: bool = False
    full_body_saved: bool = False
    provider_failure_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    positive_yield_validation: dict[str, Any] = field(default_factory=dict)
    source_type_distribution: dict[str, int] = field(default_factory=dict)
    providers_skipped: list[dict[str, Any]] = field(default_factory=list)
    no_follow_links: bool = True
    candidate_urls_visited: bool = False
    output_dir: str | None = None
    review_queue_path: str | None = None
    connector_specs_path: str | None = None
    summary_path: str | None = None
    safety_audit_path: str | None = None
    provider_failure_diagnostics_path: str | None = None
    positive_yield_validation_path: str | None = None
    candidate_quality_gate_path: str | None = None
    metadata_check_results_path: str | None = None
    refined_review_queue_path: str | None = None
    metadata_check_safety_audit_path: str | None = None
    review_queue: list[dict[str, Any]] = field(default_factory=list)
    refined_review_queue: list[dict[str, Any]] = field(default_factory=list)
    connector_specs: list[dict[str, Any]] = field(default_factory=list)
    ranked_candidates: list[dict[str, Any]] = field(default_factory=list)
    candidate_quality_gate: list[dict[str, Any]] = field(default_factory=list)
    metadata_check_results: list[dict[str, Any]] = field(default_factory=list)
    limitations: str = (
        "Candidate source search and manual connection review only; not free crawling, "
        "not source activation, and not production forecasting."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
