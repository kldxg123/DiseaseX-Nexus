from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from worker_agent.job_types import JobDefinition, sanitize_for_json

JOB_STATUSES = {"pending", "running", "success", "failed"}


@dataclass
class JobRecord:
    job_id: str
    task_name: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None


class InMemoryJobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}

    def create_job(self, task_name: str) -> JobRecord:
        job = JobRecord(job_id=str(uuid4()), task_name=str(task_name), status="pending")
        self._jobs[job.job_id] = job
        return job

    def mark_running(self, job_id: str) -> JobRecord:
        job = self.get_job(job_id)
        job.status = "running"
        job.started_at = datetime.now(UTC)
        return job

    def mark_success(
        self,
        job_id: str,
        summary: dict | None = None,
        warnings: list[str] | None = None,
    ) -> JobRecord:
        job = self.get_job(job_id)
        job.status = "success"
        job.finished_at = datetime.now(UTC)
        job.summary = sanitize_for_json(summary or {})
        job.warnings = [str(item) for item in (warnings or [])]
        job.error = None
        return job

    def mark_failed(self, job_id: str, error: str, warnings: list[str] | None = None) -> JobRecord:
        job = self.get_job(job_id)
        job.status = "failed"
        job.finished_at = datetime.now(UTC)
        job.error = _safe_error(error)
        job.warnings = [str(item) for item in (warnings or [])]
        return job

    def get_job(self, job_id: str) -> JobRecord:
        try:
            return self._jobs[str(job_id)]
        except KeyError as exc:
            raise ValueError("job_id does not identify a known local job.") from exc

    def list_jobs(self, limit: int = 100) -> list[JobRecord]:
        rows = list(self._jobs.values())
        return rows[-max(0, int(limit)) :]


def safe_job_summary(job: JobRecord) -> dict:
    return sanitize_for_json(
        {
            "job_id": job.job_id,
            "task_name": job.task_name,
            "status": job.status if job.status in JOB_STATUSES else "failed",
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "summary": job.summary,
            "warnings": job.warnings,
            "error": job.error,
        }
    )


def get_job_definitions() -> dict[str, JobDefinition]:
    jobs = [
        JobDefinition(
            name="seed_sources",
            description="Seed local source registry metadata without network access.",
            default_params={},
        ),
        JobDefinition(
            name="mock_crawl_url",
            description="Store caller-provided local HTML as a raw document; no network fetch.",
            default_params={},
        ),
        JobDefinition(
            name="parse_official_fixture",
            description="Parse caller-provided official connector fixture content; no file or network fetch.",
            default_params={},
        ),
        JobDefinition(
            name="discover_gdelt_mock",
            description="Discover candidate sources from a provided mock GDELT JSON payload only.",
            default_params={"country": "United States", "disease": "norovirus", "lookback_days": 30},
        ),
        JobDefinition(
            name="mock_source_discovery",
            description=(
                "Discover candidate sources from a provided mock GDELT fixture only; "
                "it never requests the real GDELT service."
            ),
            default_params={"country": "United States", "disease": "norovirus", "lookback_days": 30},
        ),
        JobDefinition(
            name="discover_sources_fixture",
            description=(
                "Discover country+disease candidate sources from a local search-results fixture only; "
                "it writes pending_review source_registry rows and does not perform live search."
            ),
            default_params={"country": "United States", "disease": "norovirus"},
            safe_for_scheduler=False,
        ),
        JobDefinition(
            name="source_discovery_bridge_fixture",
            description=(
                "Run local Source Discovery to Generic Parser bridge using fixtures only; "
                "candidate sources remain pending_review and no network request is made."
            ),
            default_params={"country": "United States", "disease": "influenza", "parser_strategy": "csv_dataset"},
            safe_for_scheduler=False,
        ),
        JobDefinition(
            name="live_source_search_dry_run",
            description=(
                "Plan controlled provider-only source search and write local reports; no network request is made."
            ),
            default_params={"country": "United States", "disease": "cholera", "providers": "auto"},
            safe_for_scheduler=False,
        ),
        JobDefinition(
            name="live_source_search_controlled",
            description=(
                "Run controlled provider-only live source search with explicit network, terms, and manual review flags."
            ),
            default_params={"country": "United States", "disease": "cholera", "providers": "auto"},
            safe_for_scheduler=False,
            allows_network=True,
        ),
        JobDefinition(
            name="live_source_metadata_check",
            description=(
                "Run controlled provider-only source search plus bounded candidate metadata checks; "
                "requires explicit network, terms, and manual review flags."
            ),
            default_params={
                "country": "United States",
                "disease": "measles",
                "providers": "auto",
                "metadata_check_top_n": 3,
                "max_metadata_check_requests": 3,
            },
            safe_for_scheduler=False,
            allows_network=True,
        ),
        JobDefinition(
            name="who_data_acquisition_dry_run",
            description=(
                "Run WHO-first downloadable/tabular Ebola acquisition test with local mock material only; "
                "writes a staging review bundle and no database rows."
            ),
            default_params={
                "disease": "Ebola",
                "country_or_region": "Democratic Republic of the Congo",
                "date_start": "2025-01-01",
                "date_end": "2025-12-31",
                "output_dir": "reports/who_data_acquisition",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="who_data_acquisition_controlled",
            description=(
                "Run controlled WHO-first live search and metadata checks; requires explicit live/network/terms/manual flags."
            ),
            default_params={
                "disease": "Ebola",
                "country_or_region": "Democratic Republic of the Congo",
                "date_start": "2025-01-01",
                "date_end": "2025-12-31",
                "provider": "tavily",
                "max_provider_requests": 3,
                "max_metadata_checks": 3,
            },
            safe_for_scheduler=False,
            allows_network=True,
        ),
        JobDefinition(
            name="who_data_acquisition_ingest_approved",
            description=(
                "Ingest approved WHO acquisition review rows into a local pilot DB; requires explicit persistence."
            ),
            default_params={"review_table": "", "allow_persistence": False},
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_web_search_plan",
            description="Plan DeepSeek/general web disease-source search locally; no API call and no network search.",
            default_params={
                "country": "United States",
                "disease": "influenza",
                "query_intent": "structured_time_series",
            },
            safe_for_scheduler=False,
        ),
        JobDefinition(
            name="deepseek_web_search_run",
            description=(
                "Run DeepSeek/general web search intelligence. Defaults to mock; live mode requires explicit "
                "DeepSeek/search, terms, and manual review flags."
            ),
            default_params={
                "country": "United States",
                "disease": "influenza",
                "query_intent": "structured_time_series",
                "search_backend": "mock",
            },
            safe_for_scheduler=False,
            allows_network=True,
        ),
        JobDefinition(
            name="deepseek_web_search_to_cleaning",
            description="Bridge extracted DeepSeek web search records into raw_documents and cleaner outputs locally.",
            default_params={"run_id": ""},
            safe_for_scheduler=False,
        ),
        JobDefinition(
            name="deepseek_web_search_3d_refresh",
            description="Build local 3D visualization data from stored disease-country information records.",
            default_params={"country": "United States", "disease": "influenza", "mode": "auto"},
            safe_for_scheduler=False,
        ),
        JobDefinition(
            name="deepseek_api_diagnose",
            description="Diagnose DeepSeek API configuration and optional health check without exposing credentials.",
            default_params={"allow_deepseek_api": False, "timeout_seconds": 20.0},
            safe_for_scheduler=False,
            allows_network=True,
        ),
        JobDefinition(
            name="deepseek_extraction_replay",
            description="Replay DeepSeek structured extraction from an existing safe external-search summary; no search rerun.",
            default_params={
                "run_dir": "reports/deepseek_web_search/external_search_live_validation_20260708T011246Z",
                "allow_deepseek_api": False,
                "output_dir": "reports/deepseek_web_search",
            },
            safe_for_scheduler=False,
            allows_network=True,
        ),
        JobDefinition(
            name="deepseek_extraction_quality_audit",
            description="Audit existing DeepSeek structured extraction replay outputs and local persistence; no network.",
            default_params={
                "report_dir": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_readiness_review_build",
            description="Build DeepSeek readiness review table and dashboard summary from local audit outputs.",
            default_params={
                "report_dir": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_manual_review_queue_export",
            description="Export local DeepSeek manual review queue from readiness rows.",
            default_params={
                "report_dir": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_human_review_template_export",
            description="Export DeepSeek human review annotation template from local readiness rows.",
            default_params={
                "report_dir": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
                "output_dir": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_human_review_annotations_import",
            description="Import and validate DeepSeek human review annotations without default persistence.",
            default_params={
                "report_dir": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
                "annotation_file": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review/deepseek_human_review_template.json",
                "output_dir": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review",
                "allow_persistence": False,
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_event_promotion_gate",
            description="Run DeepSeek human-review event promotion gate; persistence requires explicit local DB approval.",
            default_params={
                "report_dir": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
                "annotation_file": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review/deepseek_human_review_template.json",
                "output_dir": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review",
                "allow_persistence": False,
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_annotation_workspace_export",
            description="Export local DeepSeek annotation workspace bundle.",
            default_params={
                "report_dir": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z",
                "output_dir": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review_workspace",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_annotation_draft_validate",
            description="Validate a local DeepSeek annotation draft without persistence.",
            default_params={
                "annotation_file": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review_workspace/annotation_draft_template.json",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_annotation_promotion_preview",
            description="Preview DeepSeek annotation promotion decisions without writing a database.",
            default_params={
                "annotation_file": "reports/deepseek_web_search/deepseek_api_diagnostics_20260708T022808Z/human_review_workspace/annotation_draft_template.json",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_table_review_build_source_pack",
            description="Build a safe source pack for DeepSeek table review; controlled live search requires explicit flags.",
            default_params={
                "disease": "Ebola",
                "country_or_region": "African Region",
                "date_start": "2026-05-01",
                "date_end": "2026-07-01",
                "output_dir": "reports/deepseek_table_review",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_table_review_mock",
            description="Create a mock DeepSeek structured event table review bundle; no network and no DeepSeek call.",
            default_params={
                "disease": "Ebola",
                "country_or_region": "African Region",
                "date_start": "2026-05-01",
                "date_end": "2026-07-01",
                "output_dir": "reports/deepseek_table_review",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_table_review_mock_extract",
            description="Compatibility task name for mock structured event table extraction; no network.",
            default_params={
                "disease": "Ebola",
                "country_or_region": "African Region",
                "date_start": "2026-05-01",
                "date_end": "2026-07-01",
                "output_dir": "reports/deepseek_table_review",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_table_review_run",
            description="Run controlled DeepSeek structured event table extraction; requires explicit allow_deepseek.",
            default_params={
                "disease": "Ebola",
                "country_or_region": "African Region",
                "date_start": "2026-05-01",
                "date_end": "2026-07-01",
                "allow_deepseek": False,
                "output_dir": "reports/deepseek_table_review",
            },
            safe_for_scheduler=False,
            allows_network=True,
        ),
        JobDefinition(
            name="deepseek_table_review_run_deepseek",
            description="Compatibility task name for controlled DeepSeek table extraction; requires explicit allow_deepseek.",
            default_params={
                "disease": "Ebola",
                "country_or_region": "African Region",
                "date_start": "2026-05-01",
                "date_end": "2026-07-01",
                "allow_deepseek": False,
                "output_dir": "reports/deepseek_table_review",
            },
            safe_for_scheduler=False,
            allows_network=True,
        ),
        JobDefinition(
            name="deepseek_table_review_validate",
            description="Validate a local structured event table review file; no network.",
            default_params={"review_table": ""},
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_table_review_clean_validate",
            description="Validate a local structured event table review file and build visualization summaries; no network.",
            default_params={"review_table": ""},
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_table_review_ingest_approved",
            description="Ingest approved table review rows into a pilot database; requires explicit persistence.",
            default_params={"review_table": "", "allow_persistence": False},
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="deepseek_table_review_goal_mode",
            description="Run Goal Mode target loop for table review; defaults to mock search and mock DeepSeek.",
            default_params={
                "targets": "dengue:Global,measles:United States,cholera:Global,mpox:Global,Ebola:African Region",
                "date_start": "2026-05-01",
                "date_end": "2026-07-01",
                "mock_search": True,
                "mock_deepseek": True,
                "output_dir": "reports/deepseek_table_review_goal",
            },
            safe_for_scheduler=False,
            allows_network=False,
        ),
        JobDefinition(
            name="candidate_acquisition_plan",
            description="Plan controlled acquisition for one metadata-confirmed review candidate; no network request.",
            default_params={
                "run_dir": "reports/live_source_search/search_measles_united_states_20260706T150829Z",
                "strategy": "top_confirmed",
                "max_requests": 1,
            },
            safe_for_scheduler=False,
        ),
        JobDefinition(
            name="candidate_acquisition_controlled",
            description=(
                "Run a controlled single-URL acquisition for one approved candidate; requires explicit network and review flags."
            ),
            default_params={
                "run_dir": "reports/live_source_search/search_measles_united_states_20260706T150829Z",
                "strategy": "top_confirmed",
                "max_requests": 1,
            },
            safe_for_scheduler=False,
            allows_network=True,
        ),
        JobDefinition(
            name="candidate_acquisition_quality_diagnose",
            description=(
                "Diagnose controlled acquisition event quality and feature-generation blockers from local reports and SQLite only."
            ),
            default_params={
                "run_dir": "reports/live_source_search/acquisition_runs/acquisition_measles_united_states_20260706T154307Z",
            },
            safe_for_scheduler=False,
        ),
        JobDefinition(
            name="clean_pending",
            description="Run local cleaner_agent on pending raw_documents.",
            default_params={"limit": 100},
        ),
        JobDefinition(
            name="build_features",
            description="Build local model_features from epidemic_events.",
            default_params={"horizons_days": [7, 28], "outbreak_threshold": 3, "split_type": "train"},
        ),
        JobDefinition(
            name="backtest_model",
            description="Run local baseline rolling backtest from model_features.",
            default_params={"horizon_days": 28, "model_name": "feature_rule", "min_train_size": 4},
        ),
        JobDefinition(
            name="predict_latest",
            description="Write latest local baseline predictions from model_features.",
            default_params={"horizons_days": [7, 28], "model_name": "feature_rule", "min_train_size": 4},
        ),
        JobDefinition(
            name="minimum_data_analysis",
            description="Run local minimum data analysis from existing model_features.",
            default_params={
                "horizon_days": 28,
                "target_metric": "mae",
                "min_acceptable_score": 1.0,
                "history_windows_weeks": [8, 12, 20],
                "model_name": "feature_rule",
                "min_train_size": 4,
            },
        ),
        JobDefinition(
            name="optimize_crawlers",
            description=(
                "Run local crawler optimization diagnostics over source_registry and raw_documents; "
                "no live fetches, crawler rewrites, or source activation."
            ),
            default_params={
                "limit_sources": 100,
                "limit_documents": 200,
                "include_connector_specs": True,
                "apply_safe_actions": False,
            },
        ),
        JobDefinition(
            name="check_access_policies",
            description="Validate local source access policy guardrails without network access.",
            default_params={"policy_path": "configs/source_access_policy.example.json"},
        ),
        JobDefinition(
            name="data_quality_report",
            description="Build local data-quality and label-audit report files without network access.",
            default_params={"output_dir": "reports"},
        ),
        JobDefinition(
            name="prepare_live_data_pilot",
            description="Run local preflight and planning for a guarded live data pilot; no live fetch.",
            default_params={
                "source_key": "cdc_norostat",
                "country": "United States",
                "disease": "norovirus",
                "output_dir": "reports/live_pilot",
            },
            safe_for_scheduler=False,
        ),
        JobDefinition(
            name="summarize_feedback",
            description="Summarize local human feedback rows.",
            default_params={},
        ),
        JobDefinition(
            name="summarize_experiences",
            description="Summarize local experience-bank rows.",
            default_params={},
        ),
        JobDefinition(
            name="full_local_pipeline",
            description="Run local synthetic seed, crawl, clean, feature, prediction, and analysis pipeline.",
            default_params={"country": "United States", "disease": "norovirus", "horizon_days": 28},
        ),
        JobDefinition(
            name="smoke_test",
            description="Run the local end-to-end smoke test with synthetic data.",
            default_params={},
            safe_for_scheduler=False,
        ),
    ]
    return {job.name: job for job in jobs}


def get_job_definition(name: str) -> JobDefinition:
    normalized = str(name or "").strip()
    definitions = get_job_definitions()
    try:
        return definitions[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown job: {normalized}") from exc


def _safe_error(error: str) -> str:
    safe = sanitize_for_json(str(error))
    return safe[:240] if isinstance(safe, str) else "error"
