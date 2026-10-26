from __future__ import annotations

from uuid import uuid4
from typing import Any

from pydantic import BaseModel, Field


class JobResponse(BaseModel):
    job_id: str = Field(default_factory=lambda: str(uuid4()))
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None
    warnings: list[str] = Field(default_factory=list)


class SourceSeedRequest(BaseModel):
    dry_run: bool = False


class SourceReviewRequest(BaseModel):
    decision: str = Field(pattern="^(activate|keep_pending|disable)$")
    terms_reviewed: bool = False
    manual_reviewed: bool = False
    reviewer: str | None = None
    notes: str | None = None


class MockGdeltDiscoveryRequest(BaseModel):
    country: str
    disease: str
    lookback_days: int = Field(default=30, ge=1, le=365)
    min_source_score: float = Field(default=0.35, ge=0.0, le=1.0)
    mock_response_json: dict[str, Any]


class SourceDiscoveryFixtureRequest(BaseModel):
    country: str
    disease: str
    search_results: dict[str, Any]
    min_source_score: float = Field(default=0.35, ge=0.0, le=1.0)
    generate_connector_specs: bool = True


class ControlledLiveSourceDiscoveryRequest(BaseModel):
    provider: str = "auto"
    country: str
    disease: str
    allow_network: bool = False
    terms_reviewed: bool = False
    manual_reviewed: bool = False
    max_requests: int = Field(default=3, ge=1, le=3)
    min_source_score: float = Field(default=0.35, ge=0.0, le=1.0)
    generate_connector_specs: bool = True


class LiveSourceSearchRequestModel(BaseModel):
    provider: str = "auto"
    providers: str | None = None
    country: str
    disease: str
    preferred_live_disease: str | None = None
    max_total_requests: int = Field(default=3, ge=1, le=3)
    allow_network: bool = False
    terms_reviewed: bool = False
    manual_reviewed: bool = False
    include_rate_limited_provider: bool = False
    curated_official_directory_url: str | None = None
    metadata_check: bool = False
    max_metadata_check_requests: int = Field(default=3, ge=0, le=3)
    metadata_check_top_n: int = Field(default=3, ge=0, le=3)
    min_source_score: float = Field(default=0.35, ge=0.0, le=1.0)
    output_dir: str = "reports/live_source_search"


class MockCrawlRequest(BaseModel):
    source_id: str
    url: str
    html: str
    country_detected: str | None = None
    disease_detected: str | None = None
    language: str | None = None


class OfficialConnectorParseRequest(BaseModel):
    connector: str
    fixture_content: str
    country: str | None = None
    disease: str | None = None


class CleanRunRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=1000)
    country: str | None = None
    disease: str | None = None


class FeatureBuildRequest(BaseModel):
    country: str | None = None
    disease: str | None = None
    admin1: str | None = None
    horizons_days: list[int] = Field(default_factory=lambda: [7, 28])
    outbreak_threshold: int = Field(default=3, ge=1, le=100000)
    split_type: str = "train"
    replace_existing: bool = True


class ModelBacktestRequest(BaseModel):
    country: str | None = None
    disease: str | None = None
    admin1: str | None = None
    horizon_days: int = Field(default=28, ge=1, le=365)
    model_name: str = "moving_average"
    min_train_size: int = Field(default=8, ge=1, le=10000)
    test_window: int = Field(default=1, ge=1, le=52)


class ModelPredictRequest(BaseModel):
    country: str | None = None
    disease: str | None = None
    admin1: str | None = None
    horizon_days: int = Field(default=28, ge=1, le=365)
    model_name: str = "moving_average"
    model_version: str | None = None
    limit: int = Field(default=10, ge=1, le=1000)
    min_train_size: int = Field(default=4, ge=1, le=10000)
    write_predictions: bool = True


class FeedbackCreateRequest(BaseModel):
    prediction_id: str
    reviewer: str | None = None
    verdict: str
    true_cases: float | None = None
    true_outbreak_label: bool | None = None
    reason: str | None = None
    notes: str | None = None


class MinimumDataRequest(BaseModel):
    country: str | None = None
    disease: str | None = None
    horizon_days: int = Field(default=28, ge=1, le=365)
    target_metric: str = "pr_auc"
    min_acceptable_score: float = 0.75
    candidate_feature_groups: list[str] | None = None
    history_windows_weeks: list[int] = Field(default_factory=lambda: [12, 26, 52, 104, 156, 260])
    model_name: str = "feature_rule"
    min_train_size: int = Field(default=8, ge=1, le=10000)


class ExperienceBuildRequest(BaseModel):
    feedback_id: str


class ModelEvaluationSummaryRequest(BaseModel):
    model_version: str
    model_name: str
    metrics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ModelPromotionCheckRequest(BaseModel):
    champion: ModelEvaluationSummaryRequest
    challenger: ModelEvaluationSummaryRequest
    primary_metric: str = "regression.mae"
    lower_is_better: bool = True
    min_relative_improvement: float = Field(default=0.02, ge=0.0, le=1.0)
    max_false_alarm_regression: float | None = Field(default=None, ge=0.0)
    max_missed_outbreak_regression: float | None = Field(default=None, ge=0.0)


class JobRunRequest(BaseModel):
    job_name: str
    params: dict[str, Any] = Field(default_factory=dict)


class CrawlerOptimizerAnalyzeRequest(BaseModel):
    country: str | None = None
    disease: str | None = None
    limit_sources: int = Field(default=100, ge=1, le=1000)
    limit_documents: int = Field(default=200, ge=1, le=2000)
    include_connector_specs: bool = True
    apply_safe_actions: bool = False


class CrawlerOptimizerAnalyzeSourceRequest(BaseModel):
    source_id: str
    country: str | None = None
    disease: str | None = None


class ConnectorSpecRequest(BaseModel):
    source_id: str
    markdown: bool = False


class AccessPolicyCheckRequest(BaseModel):
    source_key: str
    url: str | None = None
    allow_network: bool = False
    terms_reviewed: bool = False
    manual_reviewed: bool = False
    robots_allowed: bool | None = None
    request_count: int = Field(default=1, ge=1, le=10000)


class DataQualityReportRequest(BaseModel):
    country: str | None = None
    disease: str | None = None
    write_files: bool = False
    output_dir: str = "reports"


class DeepSeekWebSearchRequestModel(BaseModel):
    disease: str
    country: str
    query_intent: str = "structured_time_series"
    search_backend: str = "mock"
    allow_deepseek_api: bool = False
    allow_general_web_search: bool = False
    allow_page_read: bool = False
    terms_reviewed: bool = False
    manual_reviewed: bool = False
    max_search_queries: int = Field(default=4, ge=1, le=6)
    max_search_results_per_query: int = Field(default=8, ge=1, le=8)
    max_pages_to_read: int = Field(default=8, ge=0, le=8)
    max_total_http_requests: int = Field(default=16, ge=0, le=16)
    max_readable_pages_success: int = Field(default=8, ge=0, le=8)
    stop_when_structured_candidates_found: bool = True
    prefer_official_and_structured_sources: bool = True
    prefer_situation_reports: bool = False
    prefer_dated_numeric_sources: bool = False
    include_document_candidates: bool = False
    do_not_download_documents: bool = True
    max_paragraph_signal_preview: int = Field(default=10, ge=1, le=10)
    output_dir: str = "reports/deepseek_web_search"


class DeepSeekCleaningRequest(BaseModel):
    run_id: str


class DeepSeekVisualizationRequest(BaseModel):
    disease: str
    country: str
    mode: str = "auto"


class DeepSeekApiDiagnoseRequest(BaseModel):
    allow_deepseek_api: bool = False
    timeout_seconds: float = Field(default=20.0, ge=1.0, le=60.0)


class DeepSeekReplayExtractionRequest(BaseModel):
    run_dir: str
    allow_deepseek_api: bool = False
    output_dir: str = "reports/deepseek_web_search"


class DeepSeekExtractionAuditRequest(BaseModel):
    report_dir: str
    database_url: str | None = None


class DeepSeekReadinessReviewExportRequest(BaseModel):
    report_dir: str = "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z"
    output_dir: str | None = None


class DeepSeekHumanReviewTemplateExportRequest(BaseModel):
    report_dir: str = "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z"
    output_dir: str = "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review"


class DeepSeekHumanReviewImportRequest(BaseModel):
    report_dir: str = "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z"
    annotation_file: str
    output_dir: str = "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review"
    database_url: str | None = None
    allow_persistence: bool = False


class DeepSeekHumanReviewPromotionReportRequest(BaseModel):
    output_dir: str = "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review"


class DeepSeekAnnotationWorkspaceRequest(BaseModel):
    report_dir: str = "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z"
    output_dir: str = "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review_workspace"


class DeepSeekAnnotationDraftValidationRequest(BaseModel):
    annotation_file: str


class DeepSeekTableReviewRequest(BaseModel):
    disease: str
    country_or_region: str
    date_start: str
    date_end: str
    max_sources: int = Field(default=3, ge=1, le=10)
    source_pack: str | None = None
    output_dir: str = "reports/deepseek_table_review"
    allow_deepseek: bool = False
    allow_web_search: bool = False
    allow_page_read: bool = False
    allow_live_source_search: bool = False
    allow_network: bool = False
    terms_reviewed: bool = False
    manual_reviewed: bool = False
    providers: str = "auto"
    max_total_provider_requests: int = Field(default=3, ge=1, le=3)
    metadata_check_top_n: int = Field(default=3, ge=0, le=3)
    max_metadata_check_requests: int = Field(default=3, ge=0, le=3)


class DeepSeekTableReviewValidateRequest(BaseModel):
    review_table: str


class DeepSeekTableReviewIngestRequest(BaseModel):
    review_table: str
    database_url: str | None = None
    allow_persistence: bool = False
    output_dir: str | None = None


class DeepSeekTableReviewGoalModeRequest(BaseModel):
    targets: str = "dengue:Global,measles:United States,cholera:Global,mpox:Global,Ebola:African Region"
    date_start: str
    date_end: str
    output_dir: str = "reports/deepseek_table_review_goal"
    database_url: str | None = None
    mock_deepseek: bool = False
    mock_search: bool = False
    allow_live_source_search: bool = False
    allow_deepseek: bool = False
    terms_reviewed: bool = False
    manual_reviewed: bool = False
    allow_document_preview: bool = False
    stop_when_rows_found: bool = True


class WHODataAcquisitionRequest(BaseModel):
    disease: str = "Ebola"
    country_or_region: str = "Democratic Republic of the Congo"
    date_start: str = "2025-01-01"
    date_end: str = "2025-12-31"
    provider: str = "tavily"
    output_dir: str = "reports/who_data_acquisition"
    allow_live_who: bool = False
    allow_network: bool = False
    terms_reviewed: bool = False
    manual_reviewed: bool = False
    max_provider_requests: int = Field(default=3, ge=0, le=5)
    max_metadata_checks: int = Field(default=3, ge=0, le=5)


class WHOApprovedIngestionRequest(BaseModel):
    review_table: str
    database_url: str | None = None
    allow_persistence: bool = False
    output_dir: str | None = None
