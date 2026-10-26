from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from cleaner_agent.agent import CleanerAgent
from cleaner_agent.features import FeatureBuilder
from common.models import ModelFeature, Prediction, RawDocument, SourceRegistry
from crawler_agent.connector_ingest import ingest_connector_payload, parse_connector_payload
from crawler_agent.content_extraction import extract_content
from crawler_agent.default_sources import get_default_seed_sources
from crawler_agent.gdelt import parse_gdelt_articles
from crawler_agent.http_fetcher import FetchedPage
from crawler_agent import load_access_policies, validate_access_policy
from crawler_agent.raw_document_store import RawDocumentStore
from crawler_agent.source_discovery import candidates_from_gdelt_articles
from crawler_agent.source_registry import SourceRegistryService
from crawler_agent.url_utils import normalize_url
from crawler_optimizer_agent import CrawlerOptimizerAgent, summarize_optimizer_report
from data_quality_agent.report import build_data_quality_report, write_report_files
from predictor_agent.agent import PredictorAgent
from predictor_agent.experience_bank import ExperienceBankService
from predictor_agent.feedback import FeedbackService
from predictor_agent.minimum_data_analyzer import (
    MinimumDataAnalysisRequest,
    MinimumDataAnalyzer,
    result_to_dict,
)
from source_discovery_agent import SourceDiscoveryAgent, write_discovery_report
from source_discovery_agent.bridge_report import bridge_result_to_dict, write_bridge_report
from source_discovery_agent.fixture_mapping import infer_fixture_for_parser_strategy
from source_discovery_agent.generic_bridge import DiscoveryParserBridge, DiscoveryParserBridgeInput
from worker_agent.job_registry import InMemoryJobRegistry, safe_job_summary
from worker_agent.job_types import sanitize_error, sanitize_for_json


TASK_NAMES = {
    "seed_sources",
    "mock_crawl_url",
    "parse_official_fixture",
    "discover_gdelt_mock",
    "mock_source_discovery",
    "discover_sources_fixture",
    "source_discovery_bridge_fixture",
    "live_source_search_dry_run",
    "live_source_search_controlled",
    "live_source_metadata_check",
    "who_data_acquisition_dry_run",
    "who_data_acquisition_controlled",
    "who_data_acquisition_ingest_approved",
    "deepseek_web_search_plan",
    "deepseek_web_search_run",
    "deepseek_web_search_to_cleaning",
    "deepseek_web_search_3d_refresh",
    "deepseek_api_diagnose",
    "deepseek_extraction_replay",
    "deepseek_extraction_quality_audit",
    "deepseek_readiness_review_build",
    "deepseek_manual_review_queue_export",
    "deepseek_human_review_template_export",
    "deepseek_human_review_annotations_import",
    "deepseek_event_promotion_gate",
    "deepseek_annotation_workspace_export",
    "deepseek_annotation_draft_validate",
    "deepseek_annotation_promotion_preview",
    "deepseek_table_review_build_source_pack",
    "deepseek_table_review_mock",
    "deepseek_table_review_mock_extract",
    "deepseek_table_review_run",
    "deepseek_table_review_run_deepseek",
    "deepseek_table_review_validate",
    "deepseek_table_review_clean_validate",
    "deepseek_table_review_ingest_approved",
    "deepseek_table_review_goal_mode",
    "candidate_acquisition_plan",
    "candidate_acquisition_controlled",
    "candidate_acquisition_quality_diagnose",
    "clean_pending",
    "build_features",
    "backtest_model",
    "predict_latest",
    "summarize_feedback",
    "summarize_experiences",
    "minimum_data_analysis",
    "optimize_crawlers",
    "check_access_policies",
    "data_quality_report",
    "prepare_live_data_pilot",
    "full_local_pipeline",
}


class TaskRunner:
    def __init__(self, session: Session, registry: InMemoryJobRegistry | None = None):
        self.session = session
        self.registry = registry or InMemoryJobRegistry()

    def run_task(self, task_name: str, **kwargs) -> dict:
        normalized = str(task_name or "").strip()
        method = self._method_for_task(normalized)
        job = self.registry.create_job(normalized)
        self.registry.mark_running(job.job_id)
        try:
            summary = method(**kwargs)
        except Exception as exc:
            failed = self.registry.mark_failed(job.job_id, sanitize_error(exc))
            return {"status": "failed", "job": safe_job_summary(failed)}
        succeeded = self.registry.mark_success(job.job_id, summary=summary)
        return {"status": "success", "job": safe_job_summary(succeeded)}

    def seed_sources(self, **_kwargs) -> dict:
        service = SourceRegistryService(self.session)
        created = updated = 0
        for seed in get_default_seed_sources():
            existed = service.get_source_by_base_url(seed["base_url"]) is not None
            service.upsert_source(**seed)
            if existed:
                updated += 1
            else:
                created += 1
        return {
            "created": created,
            "updated": updated,
            "sources_count": _count(self.session, SourceRegistry),
        }

    def mock_crawl_url(self, **kwargs) -> dict:
        html = kwargs.get("html")
        if not isinstance(html, str) or not html.strip():
            raise ValueError("mock_crawl_url requires caller-provided html content.")
        source_id = kwargs.get("source_id")
        if source_id is None:
            raise ValueError("mock_crawl_url requires source_id for a registered source.")
        source = self.session.get(SourceRegistry, int(source_id))
        if source is None:
            raise ValueError("source_id must identify an existing source.")
        url = normalize_url(str(kwargs.get("url") or "https://fixture.invalid/local"))
        fetched = FetchedPage(
            url=url,
            final_url=url,
            status_code=200,
            content_type="text/html; charset=utf-8",
            text=html,
        )
        result = RawDocumentStore(self.session).save(
            source=source,
            fetched_page=fetched,
            extracted_content=extract_content(html),
        )
        result.document.country_detected = _optional_str(kwargs.get("country_detected"))
        result.document.disease_detected = _optional_str(kwargs.get("disease_detected"))
        result.document.metadata_json = {
            **(result.document.metadata_json or {}),
            "fetcher": "worker_mock",
            "language": _optional_str(kwargs.get("language")),
        }
        self.session.flush()
        return {
            "saved": result.created,
            "duplicate": not result.created,
            "raw_document_id": result.document.id,
            "source_id": source.id,
            "url": url,
            "content_hash_prefix": result.document.content_hash[:12],
        }

    def parse_official_fixture(self, **kwargs) -> dict:
        connector = str(kwargs.get("connector") or "").strip()
        fixture_content = kwargs.get("fixture_content")
        if not connector:
            raise ValueError("parse_official_fixture requires connector.")
        if not isinstance(fixture_content, str) or not fixture_content.strip():
            raise ValueError("parse_official_fixture requires caller-provided fixture_content.")
        parsed = parse_connector_payload(connector, fixture_content)
        results = ingest_connector_payload(self.session, connector, fixture_content)
        saved = sum(1 for result in results if result.created)
        return {
            "parsed": len(parsed),
            "saved": saved,
            "duplicates": len(results) - saved,
            "sources_count": _count(self.session, SourceRegistry),
            "raw_documents": _count(self.session, RawDocument),
        }

    def discover_gdelt_mock(self, **kwargs) -> dict:
        mock_payload = kwargs.get("mock_response_json") or kwargs.get("mock_fixture")
        if not isinstance(mock_payload, dict):
            raise ValueError("discover_gdelt_mock requires caller-provided mock_response_json dict.")
        country = _optional_str(kwargs.get("country")) or "United States"
        disease = _optional_str(kwargs.get("disease")) or "norovirus"
        min_source_score = float(kwargs.get("min_source_score", 0.0))
        articles = parse_gdelt_articles(mock_payload)
        candidates = candidates_from_gdelt_articles(
            articles=articles,
            country=country,
            disease=disease,
            query_url=None,
        )
        selected = [candidate for candidate in candidates if candidate.source_score >= min_source_score]
        service = SourceRegistryService(self.session)
        created = updated = 0
        for candidate in selected:
            existed = service.get_source_by_base_url(candidate.base_url) is not None
            service.upsert_source(**candidate.source_payload)
            if existed:
                updated += 1
            else:
                created += 1
        return {
            "article_count": len(articles),
            "candidate_count": len(candidates),
            "upserted": len(selected),
            "created": created,
            "updated": updated,
        }

    def discover_sources_fixture(self, **kwargs) -> dict:
        fixture_path = _optional_str(kwargs.get("fixture_path") or kwargs.get("fixture"))
        if not fixture_path:
            raise ValueError("discover_sources_fixture requires fixture path.")
        country = _optional_str(kwargs.get("country")) or "United States"
        disease = _optional_str(kwargs.get("disease")) or "norovirus"
        report = SourceDiscoveryAgent(self.session).discover_from_fixture(
            country=country,
            disease=disease,
            fixture_path=fixture_path,
            min_source_score=float(kwargs.get("min_source_score", 0.35)),
            generate_connector_specs=bool(kwargs.get("generate_connector_specs", True)),
        )
        files = write_discovery_report(
            report,
            output_dir=str(kwargs.get("output_dir") or "reports/source_discovery"),
        )
        return {
            "status": report.status,
            "search_results_seen": report.search_results_seen,
            "candidates_found": report.candidates_found,
            "candidates_inserted_or_updated": report.candidates_inserted_or_updated,
            "connector_specs_generated": report.connector_specs_generated,
            "report_json": files.get("report_json"),
            "report_markdown": files.get("report_markdown"),
            "network_accessed": False,
            "limitations": "Fixture-only discovery; human review is required before activation.",
        }

    def source_discovery_bridge_fixture(self, **kwargs) -> dict:
        country = _optional_str(kwargs.get("country")) or "United States"
        disease = _optional_str(kwargs.get("disease")) or "influenza"
        parser_strategy = _optional_str(kwargs.get("parser_strategy")) or "csv_dataset"
        discovery_fixture = _optional_str(kwargs.get("source_discovery_fixture") or kwargs.get("fixture"))
        discovery_fixture = discovery_fixture or "tests/fixtures/source_discovery_influenza_search_results.json"
        discovery_report = SourceDiscoveryAgent(self.session).discover_from_fixture(
            country=country,
            disease=disease,
            fixture_path=discovery_fixture,
            min_source_score=float(kwargs.get("min_source_score", 0.35)),
            generate_connector_specs=True,
        )
        mapping = infer_fixture_for_parser_strategy(
            parser_strategy=parser_strategy,
            disease=disease,
        )
        fixture_content = Path(mapping["fixture_path"]).read_text(encoding="utf-8")
        bridge_result = DiscoveryParserBridge(self.session).run(
            DiscoveryParserBridgeInput(
                country=country,
                disease=disease,
                source_candidate_name=str(mapping["source_name"]),
                parser_strategy=parser_strategy,
                fixture_content=fixture_content,
                fixture_url=str(mapping["fixture_url"]),
                source_name=str(mapping["source_name"]),
                source_type=str(mapping["source_type"]),
                field_mapping=mapping.get("field_mapping"),
                records_path=mapping.get("records_path"),
            ),
            build_features=bool(kwargs.get("build_features", True)),
        )
        files = write_bridge_report(
            [bridge_result],
            output_dir=str(kwargs.get("output_dir") or "reports/source_discovery"),
        )
        result = bridge_result_to_dict(bridge_result)
        return {
            "status": bridge_result.status,
            "parser_strategy": parser_strategy,
            "parsed_records": result.get("parsed_records"),
            "raw_documents_saved": result.get("raw_documents_saved"),
            "epidemic_events_created": result.get("epidemic_events_created"),
            "model_features_created": result.get("model_features_created"),
            "source_discovery_candidates_found": discovery_report.candidates_found,
            "connector_specs_generated": discovery_report.connector_specs_generated,
            "report_json": files.get("report_json"),
            "report_markdown": files.get("report_markdown"),
            "network_accessed": False,
            "human_review_required": True,
            "limitations": "Fixture-only bridge; sources remain pending_review.",
        }

    def live_source_search_dry_run(self, **kwargs) -> dict:
        from live_source_search_orchestrator import LiveSourceSearchRequest, run_live_source_search
        from live_source_search_orchestrator.orchestrator import report_summary

        request = LiveSourceSearchRequest(
            country=_optional_str(kwargs.get("country")) or "United States",
            disease=_optional_str(kwargs.get("preferred_live_disease") or kwargs.get("disease")) or "cholera",
            preferred_live_disease=_optional_str(kwargs.get("preferred_live_disease")),
            providers=_optional_str(kwargs.get("providers")) or "auto",
            max_total_requests=int(kwargs.get("max_total_requests", 3)),
            dry_run=True,
            include_rate_limited_provider=bool(kwargs.get("include_rate_limited_provider", False)),
            curated_official_directory_url=_optional_str(kwargs.get("curated_official_directory_url")),
            output_dir=str(kwargs.get("output_dir") or "reports/live_source_search"),
        )
        return report_summary(run_live_source_search(request, session=self.session))

    def live_source_search_controlled(self, **kwargs) -> dict:
        from live_source_search_orchestrator import LiveSourceSearchRequest, run_live_source_search
        from live_source_search_orchestrator.orchestrator import report_summary

        if not (
            bool(kwargs.get("allow_network"))
            and bool(kwargs.get("terms_reviewed"))
            and bool(kwargs.get("manual_reviewed"))
        ):
            raise ValueError("controlled live source search requires network, terms, and manual review flags.")
        request = LiveSourceSearchRequest(
            country=_optional_str(kwargs.get("country")) or "United States",
            disease=_optional_str(kwargs.get("preferred_live_disease") or kwargs.get("disease")) or "cholera",
            preferred_live_disease=_optional_str(kwargs.get("preferred_live_disease")),
            providers=_optional_str(kwargs.get("providers")) or "auto",
            max_total_requests=int(kwargs.get("max_total_requests", 3)),
            dry_run=False,
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            include_rate_limited_provider=bool(kwargs.get("include_rate_limited_provider", False)),
            curated_official_directory_url=_optional_str(kwargs.get("curated_official_directory_url")),
            output_dir=str(kwargs.get("output_dir") or "reports/live_source_search"),
        )
        return report_summary(run_live_source_search(request, session=self.session))

    def live_source_metadata_check(self, **kwargs) -> dict:
        from live_source_search_orchestrator import LiveSourceSearchRequest, run_live_source_search
        from live_source_search_orchestrator.orchestrator import report_summary

        if not (
            bool(kwargs.get("allow_network"))
            and bool(kwargs.get("terms_reviewed"))
            and bool(kwargs.get("manual_reviewed"))
        ):
            raise ValueError("controlled metadata check requires network, terms, and manual review flags.")
        request = LiveSourceSearchRequest(
            country=_optional_str(kwargs.get("country")) or "United States",
            disease=_optional_str(kwargs.get("preferred_live_disease") or kwargs.get("disease")) or "measles",
            preferred_live_disease=_optional_str(kwargs.get("preferred_live_disease")),
            providers=_optional_str(kwargs.get("providers")) or "auto",
            max_total_requests=int(kwargs.get("max_total_requests", 3)),
            dry_run=False,
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            include_rate_limited_provider=bool(kwargs.get("include_rate_limited_provider", False)),
            curated_official_directory_url=_optional_str(kwargs.get("curated_official_directory_url")),
            metadata_check=True,
            metadata_check_top_n=min(3, int(kwargs.get("metadata_check_top_n", 3))),
            max_metadata_check_requests=min(3, int(kwargs.get("max_metadata_check_requests", 3))),
            output_dir=str(kwargs.get("output_dir") or "reports/live_source_search"),
        )
        return report_summary(run_live_source_search(request, session=self.session))

    def who_data_acquisition_dry_run(self, **kwargs) -> dict:
        from deepseek_web_search_agent.who_downloadable_targeter import run_who_data_acquisition, safe_summary

        result = run_who_data_acquisition(
            disease=_optional_str(kwargs.get("disease")) or "Ebola",
            country_or_region=_optional_str(kwargs.get("country_or_region") or kwargs.get("country"))
            or "Democratic Republic of the Congo",
            date_start=_optional_str(kwargs.get("date_start")) or "2025-01-01",
            date_end=_optional_str(kwargs.get("date_end")) or "2025-12-31",
            provider=_optional_str(kwargs.get("provider")) or "tavily",
            output_dir=str(kwargs.get("output_dir") or "reports/who_data_acquisition"),
            dry_run=True,
            max_provider_requests=int(kwargs.get("max_provider_requests", 3)),
            max_metadata_checks=int(kwargs.get("max_metadata_checks", 3)),
        )
        return safe_summary(result.to_dict())

    def who_data_acquisition_controlled(self, **kwargs) -> dict:
        from deepseek_web_search_agent.who_downloadable_targeter import run_who_data_acquisition, safe_summary

        if not (
            bool(kwargs.get("allow_live_who"))
            and bool(kwargs.get("allow_network"))
            and bool(kwargs.get("terms_reviewed"))
            and bool(kwargs.get("manual_reviewed"))
        ):
            raise ValueError("controlled WHO acquisition requires live, network, terms, and manual review flags.")
        result = run_who_data_acquisition(
            disease=_optional_str(kwargs.get("disease")) or "Ebola",
            country_or_region=_optional_str(kwargs.get("country_or_region") or kwargs.get("country"))
            or "Democratic Republic of the Congo",
            date_start=_optional_str(kwargs.get("date_start")) or "2025-01-01",
            date_end=_optional_str(kwargs.get("date_end")) or "2025-12-31",
            provider=_optional_str(kwargs.get("provider")) or "tavily",
            output_dir=str(kwargs.get("output_dir") or "reports/who_data_acquisition"),
            dry_run=False,
            allow_live_who=True,
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            max_provider_requests=int(kwargs.get("max_provider_requests", 3)),
            max_metadata_checks=int(kwargs.get("max_metadata_checks", 3)),
        )
        return safe_summary(result.to_dict())

    def who_data_acquisition_ingest_approved(self, **kwargs) -> dict:
        from deepseek_web_search_agent.approval_ingestion import ingest_approved_table_rows

        review_table = _optional_str(kwargs.get("review_table"))
        if not review_table:
            raise ValueError("who_data_acquisition_ingest_approved requires review_table.")
        return ingest_approved_table_rows(
            review_table_path=review_table,
            database_url=_optional_str(kwargs.get("database_url")),
            allow_persistence=bool(kwargs.get("allow_persistence", False)),
            output_dir=_optional_str(kwargs.get("output_dir")),
        )

    def deepseek_web_search_plan(self, **kwargs) -> dict:
        from deepseek_web_search_agent import DeepSeekWebSearchRequest, deterministic_query_plan

        request = _deepseek_request_from_kwargs(kwargs, force_mock=True)
        plan = deterministic_query_plan(request)
        return {
            "status": "ok",
            "disease": request.disease.lower(),
            "country": request.country,
            "query_count": len(plan.search_queries),
            "aliases_used": plan.disease_aliases[:8],
            "search_queries": [query.to_dict() for query in plan.search_queries],
            "network_accessed": False,
            "deepseek_api_called": False,
            "limitations": "Plan-only task; no DeepSeek API call and no network search.",
        }

    def deepseek_web_search_run(self, **kwargs) -> dict:
        from deepseek_web_search_agent import DeepSeekWebSearchRequest, run_deepseek_web_search

        wants_live = bool(kwargs.get("allow_deepseek_api") or kwargs.get("allow_general_web_search"))
        if wants_live and not (
            bool(kwargs.get("allow_deepseek_api"))
            and bool(kwargs.get("allow_general_web_search"))
            and bool(kwargs.get("terms_reviewed"))
            and bool(kwargs.get("manual_reviewed"))
        ):
            raise ValueError("DeepSeek live web search requires API, search, terms, and manual review flags.")
        request = _deepseek_request_from_kwargs(kwargs, force_mock=not wants_live)
        summary = run_deepseek_web_search(
            request,
            session=self.session,
            run_cleaning=bool(kwargs.get("run_cleaning", False)),
        )
        payload = summary.to_dict()
        return {
            "status": payload.get("status"),
            "run_id": payload.get("run_id"),
            "disease": payload.get("disease"),
            "country": payload.get("country"),
            "search_backend": payload.get("search_backend"),
            "deepseek_call_count": payload.get("deepseek_call_count", 0),
            "search_request_count": payload.get("search_request_count", 0),
            "page_read_request_count": payload.get("page_read_request_count", 0),
            "records_extracted": payload.get("records_extracted", 0),
            "records_persisted": payload.get("records_persisted", 0),
            "raw_documents_created": payload.get("raw_documents_created", 0),
            "epidemic_events_created": payload.get("epidemic_events_created", 0),
            "visualization_points": payload.get("visualization_points", 0),
            "paths": sanitize_for_json(payload.get("paths", {})),
            "warnings": payload.get("warnings", []),
            "limitations": "All extracted evidence requires human review; no accuracy guarantee.",
        }

    def deepseek_web_search_to_cleaning(self, **kwargs) -> dict:
        from deepseek_web_search_agent import send_run_to_cleaning

        run_id = _optional_str(kwargs.get("run_id"))
        if not run_id:
            raise ValueError("deepseek_web_search_to_cleaning requires run_id.")
        return sanitize_for_json(send_run_to_cleaning(self.session, run_id=run_id))

    def deepseek_web_search_3d_refresh(self, **kwargs) -> dict:
        from deepseek_web_search_agent import build_3d_visualization_dataset

        disease = _optional_str(kwargs.get("disease")) or "influenza"
        country = _optional_str(kwargs.get("country")) or "United States"
        payload = build_3d_visualization_dataset(
            self.session,
            disease=disease,
            country=country,
            mode=str(kwargs.get("mode") or "auto"),
        )
        return {
            "status": "ok",
            "disease": payload.get("disease"),
            "country": payload.get("country"),
            "mode": payload.get("mode"),
            "point_count": len(payload.get("points") or []),
            "axes": payload.get("axes"),
            "warnings": payload.get("warnings", []),
        }

    def deepseek_api_diagnose(self, **kwargs) -> dict:
        from deepseek_web_search_agent import diagnose_deepseek_config, run_deepseek_health_check

        health = run_deepseek_health_check(
            allow_deepseek_api=bool(kwargs.get("allow_deepseek_api", False)),
            timeout_seconds=float(kwargs.get("timeout_seconds", 20.0)),
        )
        return sanitize_for_json(
            {
                "config": diagnose_deepseek_config(),
                "health": health,
                "network_accessed": bool(health.get("deepseek_call_attempted")),
                "limitations": "No credentials, full prompts, or completions are returned.",
            }
        )

    def deepseek_extraction_replay(self, **kwargs) -> dict:
        from deepseek_web_search_agent import run_structured_extraction_replay

        run_dir = _optional_str(kwargs.get("run_dir"))
        if not run_dir:
            raise ValueError("deepseek_extraction_replay requires run_dir.")
        replay = run_structured_extraction_replay(
            session=self.session,
            run_dir=run_dir,
            allow_deepseek_api=bool(kwargs.get("allow_deepseek_api", False)),
            output_dir=str(kwargs.get("output_dir") or "reports/deepseek_web_search"),
        )
        return sanitize_for_json(
            {
                "status": replay.get("status"),
                "run_id": replay.get("run_id"),
                "deepseek_call_count": replay.get("deepseek_call_count", 0),
                "records_extracted_by_deepseek": replay.get("records_extracted_by_deepseek", 0),
                "records_persisted": replay.get("records_persisted", 0),
                "raw_documents_created": replay.get("raw_documents_created", 0),
                "epidemic_events_created": replay.get("epidemic_events_created", 0),
                "feature_ready_records": replay.get("feature_ready_records", 0),
                "visualization_points": replay.get("visualization_points", 0),
                "fallback_extraction_used": replay.get("fallback_extraction_used", False),
                "no_tavily_rerun": True,
                "page_read_request_count": 0,
            }
        )

    def deepseek_extraction_quality_audit(self, **kwargs) -> dict:
        from deepseek_web_search_agent import write_deepseek_quality_audit_report

        report_dir = _optional_str(kwargs.get("report_dir"))
        if not report_dir:
            raise ValueError("deepseek_extraction_quality_audit requires report_dir.")
        result = write_deepseek_quality_audit_report(
            report_dir,
            database_url=_optional_str(kwargs.get("database_url")),
        )
        return sanitize_for_json(
            {
                "status": result.get("status"),
                "records_extracted": result.get("records_extracted"),
                "records_persisted": result.get("records_persisted"),
                "persistence_match": result.get("persistence_match"),
                "evidence_only_count": result.get("evidence_only_count"),
                "event_ready_count": result.get("event_ready_count"),
                "feature_ready_count": result.get("feature_ready_count"),
                "visualization_ready_count": result.get("visualization_ready_count"),
                "requires_human_review_count": result.get("requires_human_review_count"),
                "visualization_points": result.get("visualization_points"),
                "paths": result.get("paths", {}),
                "network_accessed": False,
                "deepseek_called_in_this_step": False,
                "tavily_rerun_in_this_step": False,
            }
        )

    def deepseek_readiness_review_build(self, **kwargs) -> dict:
        from deepseek_web_search_agent import write_readiness_review_reports

        report_dir = _optional_str(kwargs.get("report_dir"))
        if not report_dir:
            raise ValueError("deepseek_readiness_review_build requires report_dir.")
        result = write_readiness_review_reports(report_dir)
        return sanitize_for_json(
            {
                "status": result.get("status"),
                "total_records": result.get("total_records"),
                "evidence_only_count": result.get("evidence_only_count"),
                "event_ready_count": result.get("event_ready_count"),
                "feature_ready_count": result.get("feature_ready_count"),
                "visualization_ready_count": result.get("visualization_ready_count"),
                "requires_human_review_count": result.get("requires_human_review_count"),
                "top_blockers": result.get("top_blockers", []),
                "paths": result.get("paths", {}),
                "network_accessed": False,
                "deepseek_called_in_this_step": False,
                "tavily_rerun_in_this_step": False,
            }
        )

    def deepseek_manual_review_queue_export(self, **kwargs) -> dict:
        from deepseek_web_search_agent import build_readiness_review_rows, export_human_review_queue

        report_dir = _optional_str(kwargs.get("report_dir"))
        if not report_dir:
            raise ValueError("deepseek_manual_review_queue_export requires report_dir.")
        output_dir = _optional_str(kwargs.get("output_dir")) or report_dir
        rows = build_readiness_review_rows(report_dir)
        result = export_human_review_queue(rows, output_dir)
        return sanitize_for_json(
            {
                **result.to_dict(),
                "network_accessed": False,
                "deepseek_called_in_this_step": False,
                "tavily_rerun_in_this_step": False,
            }
        )

    def deepseek_human_review_template_export(self, **kwargs) -> dict:
        from deepseek_web_search_agent import export_human_review_template

        report_dir = _optional_str(kwargs.get("report_dir"))
        output_dir = _optional_str(kwargs.get("output_dir"))
        if not report_dir or not output_dir:
            raise ValueError("deepseek_human_review_template_export requires report_dir and output_dir.")
        result = export_human_review_template(report_dir, output_dir)
        return sanitize_for_json(result)

    def deepseek_human_review_annotations_import(self, **kwargs) -> dict:
        return self.deepseek_event_promotion_gate(**kwargs)

    def deepseek_event_promotion_gate(self, **kwargs) -> dict:
        from deepseek_web_search_agent import promote_reviewed_records_to_epidemic_events

        report_dir = _optional_str(kwargs.get("report_dir"))
        annotation_file = _optional_str(kwargs.get("annotation_file"))
        output_dir = _optional_str(kwargs.get("output_dir"))
        if not report_dir or not annotation_file or not output_dir:
            raise ValueError("deepseek_event_promotion_gate requires report_dir, annotation_file, and output_dir.")
        report = promote_reviewed_records_to_epidemic_events(
            report_dir=report_dir,
            annotation_file=annotation_file,
            output_dir=output_dir,
            database_url=_optional_str(kwargs.get("database_url")),
            allow_persistence=bool(kwargs.get("allow_persistence", False)),
        )
        result = report.result
        return sanitize_for_json(
            {
                "status": report.status,
                "imported_annotations": result.imported_annotations,
                "approved_event_count": result.approved_event_count,
                "blocked_count": result.blocked_count,
                "promoted_epidemic_events": result.promoted_epidemic_events,
                "feature_generation_allowed": result.feature_generation_allowed,
                "feature_eligibility_blockers": result.feature_eligibility_blockers,
                "paths": report.paths,
                "network_accessed": False,
                "deepseek_called_in_this_step": False,
                "tavily_rerun_in_this_step": False,
            }
        )

    def deepseek_annotation_workspace_export(self, **kwargs) -> dict:
        from deepseek_web_search_agent import export_annotation_workspace_bundle

        report_dir = _optional_str(kwargs.get("report_dir"))
        output_dir = _optional_str(kwargs.get("output_dir"))
        if not report_dir or not output_dir:
            raise ValueError("deepseek_annotation_workspace_export requires report_dir and output_dir.")
        result = export_annotation_workspace_bundle(report_dir, output_dir)
        return sanitize_for_json(
            {
                **result,
                "network_accessed": False,
                "deepseek_called_in_this_step": False,
                "tavily_rerun_in_this_step": False,
            }
        )

    def deepseek_annotation_draft_validate(self, **kwargs) -> dict:
        from deepseek_web_search_agent import validate_annotation_draft

        annotation_file = _optional_str(kwargs.get("annotation_file"))
        if not annotation_file:
            raise ValueError("deepseek_annotation_draft_validate requires annotation_file.")
        result = validate_annotation_draft(annotation_file)
        return sanitize_for_json(
            {
                **result.to_dict(),
                "network_accessed": False,
                "deepseek_called_in_this_step": False,
                "tavily_rerun_in_this_step": False,
            }
        )

    def deepseek_annotation_promotion_preview(self, **kwargs) -> dict:
        from deepseek_web_search_agent import preview_promotion_decisions

        annotation_file = _optional_str(kwargs.get("annotation_file"))
        if not annotation_file:
            raise ValueError("deepseek_annotation_promotion_preview requires annotation_file.")
        result = preview_promotion_decisions(annotation_file)
        return sanitize_for_json(
            {
                **result.to_dict(),
                "network_accessed": False,
                "deepseek_called_in_this_step": False,
                "tavily_rerun_in_this_step": False,
            }
        )

    def deepseek_table_review_mock(self, **kwargs) -> dict:
        from deepseek_web_search_agent import run_structured_table_review

        return sanitize_for_json(
            run_structured_table_review(
                disease=_optional_str(kwargs.get("disease")) or "Ebola",
                country_or_region=_optional_str(kwargs.get("country_or_region")) or "African Region",
                date_start=_optional_str(kwargs.get("date_start")) or "2026-05-01",
                date_end=_optional_str(kwargs.get("date_end")) or "2026-07-01",
                output_dir=str(kwargs.get("output_dir") or "reports/deepseek_table_review"),
                source_pack_path=_optional_str(kwargs.get("source_pack")),
                mock_deepseek=True,
                allow_deepseek=False,
            )
        )

    def deepseek_table_review_build_source_pack(self, **kwargs) -> dict:
        from deepseek_web_search_agent import build_table_query_source_pack

        result = build_table_query_source_pack(
            disease=_optional_str(kwargs.get("disease")) or "Ebola",
            country_or_region=_optional_str(kwargs.get("country_or_region")) or "African Region",
            date_start=_optional_str(kwargs.get("date_start")) or "2026-05-01",
            date_end=_optional_str(kwargs.get("date_end")) or "2026-07-01",
            max_sources=int(kwargs.get("max_sources", 3)),
            source_pack_path=_optional_str(kwargs.get("source_pack")),
            allow_live_source_search=bool(kwargs.get("allow_live_source_search", False)),
            allow_network=bool(kwargs.get("allow_network", False)),
            terms_reviewed=bool(kwargs.get("terms_reviewed", False)),
            manual_reviewed=bool(kwargs.get("manual_reviewed", False)),
            providers=_optional_str(kwargs.get("providers")) or "auto",
            output_dir=str(kwargs.get("output_dir") or "reports/deepseek_table_review"),
        )
        payload = result.to_dict()
        return sanitize_for_json(
            {
                "status": "ok",
                "source_count": len(payload["sources"]),
                "sources": payload["sources"],
                "network_accessed": payload["network_accessed"],
                "provider_request_count_total": payload["provider_request_count_total"],
                "warnings": payload["warnings"],
            }
        )

    def deepseek_table_review_mock_extract(self, **kwargs) -> dict:
        return self.deepseek_table_review_mock(**kwargs)

    def deepseek_table_review_run(self, **kwargs) -> dict:
        from deepseek_web_search_agent import run_structured_table_review

        if not bool(kwargs.get("allow_deepseek", False)):
            raise ValueError("deepseek_table_review_run requires explicit allow_deepseek.")
        return sanitize_for_json(
            run_structured_table_review(
                disease=_optional_str(kwargs.get("disease")) or "Ebola",
                country_or_region=_optional_str(kwargs.get("country_or_region")) or "African Region",
                date_start=_optional_str(kwargs.get("date_start")) or "2026-05-01",
                date_end=_optional_str(kwargs.get("date_end")) or "2026-07-01",
                output_dir=str(kwargs.get("output_dir") or "reports/deepseek_table_review"),
                source_pack_path=_optional_str(kwargs.get("source_pack")),
                mock_deepseek=False,
                allow_deepseek=True,
                allow_live_source_search=bool(kwargs.get("allow_live_source_search", False)),
                allow_network=bool(kwargs.get("allow_network", False)),
                terms_reviewed=bool(kwargs.get("terms_reviewed", False)),
                manual_reviewed=bool(kwargs.get("manual_reviewed", False)),
                providers=_optional_str(kwargs.get("providers")) or "auto",
            )
        )

    def deepseek_table_review_run_deepseek(self, **kwargs) -> dict:
        return self.deepseek_table_review_run(**kwargs)

    def deepseek_table_review_goal_mode(self, **kwargs) -> dict:
        from deepseek_web_search_agent.auto_target_runner import run_goal_mode

        result = run_goal_mode(
            targets=_optional_str(kwargs.get("targets"))
            or "dengue:Global,measles:United States,cholera:Global,mpox:Global,Ebola:African Region",
            date_start=_optional_str(kwargs.get("date_start")) or "2026-05-01",
            date_end=_optional_str(kwargs.get("date_end")) or "2026-07-01",
            output_dir=str(kwargs.get("output_dir") or "reports/deepseek_table_review_goal"),
            database_url=_optional_str(kwargs.get("database_url")),
            mock_deepseek=bool(kwargs.get("mock_deepseek", True)),
            mock_search=bool(kwargs.get("mock_search", True)),
            allow_live_source_search=bool(kwargs.get("allow_live_source_search", False)),
            allow_deepseek=bool(kwargs.get("allow_deepseek", False)),
            terms_reviewed=bool(kwargs.get("terms_reviewed", False)),
            manual_reviewed=bool(kwargs.get("manual_reviewed", False)),
            allow_document_preview=bool(kwargs.get("allow_document_preview", False)),
        )
        return sanitize_for_json(result.to_dict())

    def deepseek_table_review_validate(self, **kwargs) -> dict:
        from pathlib import Path

        from deepseek_web_search_agent import build_disease_time_query_request, build_source_pack, validate_extracted_table_rows
        from deepseek_web_search_agent.staging_review import load_review_table

        review_table = _optional_str(kwargs.get("review_table"))
        if not review_table:
            raise ValueError("deepseek_table_review_validate requires review_table.")
        review_path = Path(review_table)
        rows = load_review_table(review_path)
        query = build_disease_time_query_request(
            disease=_optional_str(kwargs.get("disease")) or "unknown",
            country_or_region=_optional_str(kwargs.get("country_or_region")) or "unknown",
            date_start=_optional_str(kwargs.get("date_start")) or "1900-01-01",
            date_end=_optional_str(kwargs.get("date_end")) or "2999-12-31",
        )
        sources = build_source_pack(query, source_pack_path=str(review_path.parent / "source_map.json"))
        validated, issues = validate_extracted_table_rows(rows, query, sources)
        return sanitize_for_json({"status": "ok", "rows": validated, "issues_count": len(issues), "network_accessed": False})

    def deepseek_table_review_clean_validate(self, **kwargs) -> dict:
        from deepseek_web_search_agent import build_table_visualization_data

        result = self.deepseek_table_review_validate(**kwargs)
        visualization = build_table_visualization_data(result.get("rows", []))
        return sanitize_for_json({**result, "visualization": visualization})

    def deepseek_table_review_ingest_approved(self, **kwargs) -> dict:
        from deepseek_web_search_agent import ingest_approved_table_rows

        review_table = _optional_str(kwargs.get("review_table"))
        if not review_table:
            raise ValueError("deepseek_table_review_ingest_approved requires review_table.")
        return sanitize_for_json(
            ingest_approved_table_rows(
                review_table_path=review_table,
                database_url=_optional_str(kwargs.get("database_url")),
                allow_persistence=bool(kwargs.get("allow_persistence", False)),
                output_dir=_optional_str(kwargs.get("output_dir")),
            )
        )

    def candidate_acquisition_plan(self, **kwargs) -> dict:
        from live_source_search_orchestrator.acquisition_bridge import create_plan_from_run_dir

        plan = create_plan_from_run_dir(
            str(kwargs.get("run_dir") or "reports/live_source_search/search_measles_united_states_20260706T150829Z"),
            output_dir=str(kwargs.get("output_dir") or "reports/live_source_search/acquisition_runs"),
            strategy=str(kwargs.get("strategy") or "top_confirmed"),
            max_requests=min(1, int(kwargs.get("max_requests", 1))),
        )
        candidate = plan.candidate or {}
        return {
            "status": "ok" if plan.preflight.allowed else "blocked",
            "run_id": plan.run_id,
            "output_dir": plan.output_dir,
            "candidate_title": candidate.get("title"),
            "candidate_domain": candidate.get("domain"),
            "preflight": plan.preflight.to_dict(),
            "network_accessed": False,
            "follow_links": False,
            "files_downloaded": False,
            "full_body_saved": False,
            "plan_path": str(Path(plan.output_dir) / "candidate_acquisition_plan.json"),
        }

    def candidate_acquisition_controlled(self, **kwargs) -> dict:
        from live_source_search_orchestrator.acquisition_bridge import create_plan_from_run_dir, run_candidate_acquisition

        if not (
            bool(kwargs.get("allow_network"))
            and bool(kwargs.get("terms_reviewed"))
            and bool(kwargs.get("manual_reviewed"))
        ):
            raise ValueError("controlled candidate acquisition requires network, terms, and manual review flags.")
        if int(kwargs.get("max_requests", 1)) > 1:
            raise ValueError("candidate acquisition max_requests must be <= 1.")
        plan = create_plan_from_run_dir(
            str(kwargs.get("run_dir") or "reports/live_source_search/search_measles_united_states_20260706T150829Z"),
            output_dir=str(kwargs.get("output_dir") or "reports/live_source_search/acquisition_runs"),
            strategy=str(kwargs.get("strategy") or "top_confirmed"),
            max_requests=1,
        )
        result = run_candidate_acquisition(
            plan,
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            session=self.session,
        )
        return result.to_dict()

    def candidate_acquisition_quality_diagnose(self, **kwargs) -> dict:
        from live_source_search_orchestrator.event_quality_diagnostics import write_event_quality_diagnostic_report

        database_url = _optional_str(kwargs.get("database_url"))
        if not database_url:
            raise ValueError("candidate_acquisition_quality_diagnose requires database_url.")
        report = write_event_quality_diagnostic_report(
            str(kwargs.get("run_dir") or "reports/live_source_search/acquisition_runs/acquisition_measles_united_states_20260706T154307Z"),
            database_url=database_url,
        )
        return {
            "status": report.status,
            "raw_documents_count": report.raw_documents_count,
            "epidemic_events_count": report.epidemic_events_count,
            "model_features_count": report.model_features_count,
            "feature_ready_event_count": report.event_quality.feature_ready_event_count,
            "event_quality_status": report.event_quality.quality_status,
            "blocker_types": [item.blocker_type for item in report.blockers],
            "recommended_next_action": report.recommended_next_action,
            "report_paths": report.report_paths,
        }

    def clean_pending(self, **kwargs) -> dict:
        summary = CleanerAgent(self.session).process_pending(
            limit=int(kwargs.get("limit", 100)),
            country=_optional_str(kwargs.get("country")),
            disease=_optional_str(kwargs.get("disease")),
        )
        return _pick(summary, ["processed", "events_created", "no_event", "duplicates", "errors"])

    def build_features(self, **kwargs) -> dict:
        summary = FeatureBuilder(self.session).build(
            country=_optional_str(kwargs.get("country")),
            disease=_optional_str(kwargs.get("disease")),
            admin1=_optional_str(kwargs.get("admin1")),
            horizons_days=_int_tuple(kwargs.get("horizons_days") or kwargs.get("horizon_days"), default=(7, 28)),
            outbreak_threshold=int(kwargs.get("outbreak_threshold", 3)),
            split_type=str(kwargs.get("split_type", "train")),
            replace_existing=bool(kwargs.get("replace_existing", True)),
        )
        return _pick(summary, ["events_seen", "weekly_rows", "feature_rows", "saved", "updated", "skipped"])

    def backtest_model(self, **kwargs) -> dict:
        result = PredictorAgent(self.session).backtest(
            country=_optional_str(kwargs.get("country")),
            disease=_optional_str(kwargs.get("disease")),
            admin1=_optional_str(kwargs.get("admin1")),
            horizon_days=int(kwargs.get("horizon_days", 28)),
            model_name=str(kwargs.get("model_name", "feature_rule")),
            min_train_size=int(kwargs.get("min_train_size", 4)),
            test_window=int(kwargs.get("test_window", 1)),
        )
        return {
            "model_name": result.get("model_name"),
            "horizon_days": result.get("horizon_days"),
            "features_seen": result.get("features_seen", 0),
            "backtest_records": len(result.get("predictions", [])),
            "splits": result.get("splits", 0),
            "metrics": sanitize_for_json(result.get("metrics", {})),
            "warnings": list(result.get("warnings", [])),
        }

    def predict_latest(self, **kwargs) -> dict:
        result = PredictorAgent(self.session).predict_latest(
            country=_optional_str(kwargs.get("country")),
            disease=_optional_str(kwargs.get("disease")),
            admin1=_optional_str(kwargs.get("admin1")),
            horizon_days=int(kwargs.get("horizon_days", 28)),
            model_name=str(kwargs.get("model_name", "feature_rule")),
            min_train_size=int(kwargs.get("min_train_size", 4)),
            replace_existing=bool(kwargs.get("replace_existing", True)),
            write_predictions=bool(kwargs.get("write_predictions", True)),
        )
        return {
            key: result.get(key)
            for key in (
                "model_name",
                "model_version",
                "horizon_days",
                "features_seen",
                "prediction_rows",
                "predictions_written",
                "saved",
                "updated",
                "skipped",
                "warnings",
            )
        }

    def summarize_feedback(self, **kwargs) -> dict:
        return FeedbackService(self.session).summarize_feedback(
            country=_optional_str(kwargs.get("country")),
            disease=_optional_str(kwargs.get("disease")),
        )

    def summarize_experiences(self, **kwargs) -> dict:
        return ExperienceBankService(self.session).summarize_experiences(
            country=_optional_str(kwargs.get("country")),
            disease=_optional_str(kwargs.get("disease")),
        )

    def minimum_data_analysis(self, **kwargs) -> dict:
        result = MinimumDataAnalyzer(self.session).analyze(
            MinimumDataAnalysisRequest(
                country=_optional_str(kwargs.get("country")),
                disease=_optional_str(kwargs.get("disease")),
                admin1=_optional_str(kwargs.get("admin1")),
                horizon_days=int(kwargs.get("horizon_days", 28)),
                target_metric=str(kwargs.get("target_metric", "mae")),
                min_acceptable_score=float(kwargs.get("min_acceptable_score", 1.0)),
                candidate_feature_groups=kwargs.get("candidate_feature_groups"),
                history_windows_weeks=[int(item) for item in kwargs.get("history_windows_weeks", [8, 12, 20])],
                model_name=str(kwargs.get("model_name", "feature_rule")),
                min_train_size=int(kwargs.get("min_train_size", 4)),
                test_window=int(kwargs.get("test_window", 1)),
            )
        )
        payload = result_to_dict(result)
        return {
            "minimum_feature_set": payload.get("minimum_feature_set", []),
            "minimum_history_weeks": payload.get("minimum_history_weeks"),
            "expected_score": payload.get("expected_score"),
            "evaluation_count": len(payload.get("evaluations", [])),
            "learning_curve_count": len(payload.get("learning_curve", [])),
            "warnings": payload.get("warnings", []),
            "limitations": "Auxiliary analysis only; human review is required.",
        }

    def optimize_crawlers(self, **kwargs) -> dict:
        report = CrawlerOptimizerAgent(self.session).analyze(
            country=_optional_str(kwargs.get("country")),
            disease=_optional_str(kwargs.get("disease")),
            limit_sources=int(kwargs.get("limit_sources", 100)),
            limit_documents=int(kwargs.get("limit_documents", 200)),
            include_connector_specs=bool(kwargs.get("include_connector_specs", True)),
            apply_safe_actions=bool(kwargs.get("apply_safe_actions", False)),
        )
        summary = summarize_optimizer_report(report, max_failure_cases=0, max_actions=0, max_connector_specs=0)
        return {
            "status": summary.get("status"),
            "sources_analyzed": summary.get("sources_analyzed", 0),
            "documents_analyzed": summary.get("documents_analyzed", 0),
            "failure_case_count": summary.get("failure_case_count", 0),
            "recommended_action_count": summary.get("recommended_action_count", 0),
            "connector_spec_count": summary.get("connector_spec_count", 0),
            "top_failure_types": summary.get("top_failure_types", []),
            "warnings": summary.get("warnings", []),
            "limitations": summary.get("limitations"),
        }

    def check_access_policies(self, **kwargs) -> dict:
        policy_path = str(kwargs.get("policy_path") or "configs/source_access_policy.example.json")
        policies = load_access_policies(policy_path)
        errors: list[str] = []
        for policy in policies.values():
            errors.extend(validate_access_policy(policy))
        return {
            "policies_checked": len(policies),
            "errors_count": len(errors),
            "warnings": errors,
            "guardrail_status": "ok" if not errors else "failed",
            "network_accessed": False,
        }

    def data_quality_report(self, **kwargs) -> dict:
        report = build_data_quality_report(
            self.session,
            country=_optional_str(kwargs.get("country")),
            disease=_optional_str(kwargs.get("disease")),
        )
        files = write_report_files(
            report,
            output_dir=str(kwargs.get("output_dir") or "reports"),
        )
        return {
            "report_json": files.get("report_json"),
            "report_markdown": files.get("report_markdown"),
            "warnings_count": files.get("warnings_count", 0),
            "key_counts": files.get("key_counts", {}),
            "limitations": "Data-quality report is a local summary only; human review is required.",
        }

    def prepare_live_data_pilot(self, **kwargs) -> dict:
        from scripts.live_data_preflight import run_preflight
        from scripts.plan_live_data_pilot import write_live_pilot_plan

        source_key = str(kwargs.get("source_key") or "cdc_norostat")
        policy_path = str(kwargs.get("policy_path") or "configs/source_access_policy.example.json")
        country = _optional_str(kwargs.get("country")) or "United States"
        disease = _optional_str(kwargs.get("disease")) or "norovirus"
        output_dir = str(kwargs.get("output_dir") or "reports/live_pilot")
        preflight = run_preflight(source_key=source_key, policy_path=policy_path)
        plan = write_live_pilot_plan(
            source_key=source_key,
            country=country,
            disease=disease,
            policy_path=policy_path,
            output_dir=output_dir,
            max_requests=int(kwargs.get("max_requests", 1)),
        )
        return {
            "source_key": source_key,
            "preflight_ok": preflight.get("status") == "ok",
            "plan_created": plan.get("status") == "ok",
            "plan_json": plan.get("plan_json"),
            "plan_markdown": plan.get("plan_markdown"),
            "live_execution_enabled": False,
            "network_accessed": False,
            "warnings": list(preflight.get("warnings", [])) + list(plan.get("warnings", [])),
        }

    def full_local_pipeline(self, **kwargs) -> dict:
        from worker_agent.pipelines import PipelineRunner

        return PipelineRunner(self).run_full_local_pipeline(
            country=_optional_str(kwargs.get("country")) or "United States",
            disease=_optional_str(kwargs.get("disease")) or "norovirus",
            horizon_days=int(kwargs.get("horizon_days", 28)),
        )

    def _method_for_task(self, task_name: str) -> Callable[..., dict]:
        aliases = {
            "mock_source_discovery": "discover_gdelt_mock",
            "deepseek_table_review_mock_extract": "deepseek_table_review_mock_extract",
            "deepseek_table_review_run_deepseek": "deepseek_table_review_run_deepseek",
            "deepseek_table_review_clean_validate": "deepseek_table_review_clean_validate",
        }
        resolved = aliases.get(task_name, task_name)
        if resolved not in TASK_NAMES:
            raise ValueError(f"Unknown task: {task_name}")
        return getattr(self, resolved)


def list_task_names() -> list[str]:
    return sorted(TASK_NAMES)


def _count(session: Session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def _pick(summary: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return sanitize_for_json({key: summary.get(key, 0) for key in keys})


def _int_tuple(value: Any, *, default: tuple[int, ...]) -> tuple[int, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if isinstance(value, (list, tuple)):
        return tuple(int(item) for item in value)
    return (int(value),)


def _deepseek_request_from_kwargs(kwargs: dict[str, Any], *, force_mock: bool):
    from deepseek_web_search_agent import DeepSeekWebSearchRequest

    return DeepSeekWebSearchRequest(
        country=_optional_str(kwargs.get("country")) or "United States",
        disease=_optional_str(kwargs.get("disease")) or "influenza",
        query_intent=str(kwargs.get("query_intent") or "structured_time_series"),
        search_backend="mock" if force_mock else str(kwargs.get("search_backend") or "auto"),
        allow_deepseek_api=bool(kwargs.get("allow_deepseek_api") and not force_mock),
        allow_general_web_search=bool(kwargs.get("allow_general_web_search") and not force_mock),
        allow_page_read=bool(kwargs.get("allow_page_read") and not force_mock),
        terms_reviewed=bool(kwargs.get("terms_reviewed")),
        manual_reviewed=bool(kwargs.get("manual_reviewed")),
        max_search_queries=int(kwargs.get("max_search_queries", 4)),
        max_search_results_per_query=int(kwargs.get("max_search_results_per_query", 8)),
        max_pages_to_read=int(kwargs.get("max_pages_to_read", 8)),
        max_total_http_requests=int(kwargs.get("max_total_http_requests", 16)),
        max_readable_pages_success=int(kwargs.get("max_readable_pages_success", 8)),
        stop_when_structured_candidates_found=bool(kwargs.get("stop_when_structured_candidates_found", True)),
        prefer_official_and_structured_sources=bool(kwargs.get("prefer_official_and_structured_sources", True)),
        output_dir=str(kwargs.get("output_dir") or "reports/deepseek_web_search"),
        database_url=_optional_str(kwargs.get("database_url")),
    ).validated()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
