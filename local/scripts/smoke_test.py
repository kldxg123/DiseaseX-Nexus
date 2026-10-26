from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import warnings
from contextlib import suppress
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for import_path in (PROJECT_ROOT, PROJECT_ROOT / "packages"):
    text_path = str(import_path)
    if text_path not in sys.path:
        sys.path.insert(0, text_path)

from sqlalchemy import select

from apps.api.deps import get_db_session
from apps.api.main import app
from cleaner_agent.agent import CleanerAgent
from cleaner_agent.features import FeatureBuilder
from common.db import get_session, init_db
from common.models import HumanFeedback, ModelExperienceBank, ModelFeature, Prediction, RawDocument, SourceRegistry
from crawler_agent import load_access_policies, validate_access_policy
from crawler_agent.content_extraction import extract_content
from crawler_agent.default_sources import get_default_seed_sources
from crawler_agent.http_fetcher import FetchedPage
from crawler_agent.raw_document_store import RawDocumentStore
from crawler_agent.source_registry import SourceRegistryService
from crawler_optimizer_agent import CrawlerOptimizerAgent, summarize_optimizer_report
from predictor_agent.agent import PredictorAgent
from predictor_agent.feedback import FeedbackInput, FeedbackStore
from predictor_agent.minimum_data_analyzer import (
    MinimumDataAnalysisRequest,
    MinimumDataAnalyzer,
    result_to_dict,
)
from source_discovery_agent import SourceDiscoveryAgent

COUNTRY = "United States"
DISEASE = "norovirus"
HORIZON_DAYS = 7


def create_synthetic_raw_documents(session) -> dict:
    source = _ensure_smoke_source(session)
    store = RawDocumentStore(session)
    created = duplicates = 0
    start = datetime(2025, 1, 6, tzinfo=UTC)
    documents = _synthetic_documents(start)

    for index, payload in enumerate(documents, start=1):
        fetched_at = payload["published_at"]
        url = f"https://local.invalid/smoke/{index:02d}"
        html = _html_document(payload["title"], payload["body"])
        result = store.save(
            source=source,
            fetched_page=FetchedPage(
                url=url,
                final_url=url,
                status_code=200,
                content_type="text/html; charset=utf-8",
                text=html,
            ),
            extracted_content=extract_content(html),
        )
        if result.created:
            created += 1
        else:
            duplicates += 1
        result.document.published_at = fetched_at
        result.document.fetched_at = fetched_at
        result.document.country_detected = payload.get("country_detected")
        result.document.disease_detected = payload.get("disease_detected")
        result.document.metadata_json = {
            "fetcher": "smoke_fixture",
            "document_kind": payload["kind"],
            "raw_text_omitted": True,
        }

    total_raw_documents = _count(session, RawDocument)
    return {
        "ok": created > 0,
        "created": created,
        "duplicates": duplicates,
        "total_raw_documents": total_raw_documents,
    }


def run_cleaner_step(session) -> dict:
    summary = CleanerAgent(session).process_pending(limit=100)
    return {
        "ok": summary.get("events_created", 0) > 0,
        "processed": int(summary.get("processed", 0)),
        "events_created": int(summary.get("events_created", 0)),
        "no_event": int(summary.get("no_event", 0)),
        "errors": int(summary.get("errors", 0)),
    }


def run_feature_step(session) -> dict:
    summary = FeatureBuilder(session).build(
        country=COUNTRY,
        disease=DISEASE,
        horizons_days=(HORIZON_DAYS, 28),
        outbreak_threshold=1,
        split_type="train",
        replace_existing=True,
    )
    return {
        "ok": summary.get("feature_rows", 0) > 0,
        "events_seen": int(summary.get("events_seen", 0)),
        "weekly_rows": int(summary.get("weekly_rows", 0)),
        "feature_rows": int(summary.get("feature_rows", 0)),
        "saved": int(summary.get("saved", 0)),
        "updated": int(summary.get("updated", 0)),
        "skipped": int(summary.get("skipped", 0)),
    }


def run_predictor_step(session) -> dict:
    summary = PredictorAgent(session).run(
        country=COUNTRY,
        disease=DISEASE,
        horizons_days=(HORIZON_DAYS, 28),
        model_name="feature_rule",
        min_train_size=4,
        window_size=3,
        replace_existing=True,
    )
    predictions_count = _count(session, Prediction)
    metrics = summary.get("metrics", {}) if isinstance(summary.get("metrics"), dict) else {}
    overall = metrics.get("overall", {}) if isinstance(metrics.get("overall"), dict) else {}
    return {
        "ok": predictions_count > 0,
        "features_seen": int(summary.get("features_seen", 0)),
        "backtest_predictions": int(summary.get("backtest_records", 0)),
        "prediction_rows": int(summary.get("prediction_rows", 0)),
        "predictions_count": predictions_count,
        "metrics": {
            "model_name": metrics.get("model_name"),
            "model_version": metrics.get("model_version"),
            "n_predictions": overall.get("n"),
            "mae": overall.get("mae"),
            "rmse": overall.get("rmse"),
        },
    }


def run_feedback_step(session, prediction_id: str | None) -> dict:
    prediction = None
    if prediction_id is not None:
        prediction = session.get(Prediction, int(prediction_id))
    if prediction is None:
        prediction = session.scalar(select(Prediction).order_by(Prediction.id))
    if prediction is None:
        return {
            "ok": False,
            "feedback_created": False,
            "experience_created": False,
            "experience_count": _count(session, ModelExperienceBank),
        }

    result = FeedbackStore(session).add_feedback(
        FeedbackInput(
            prediction_id=prediction.id,
            feedback_status="partial_success",
            actual_value=float(prediction.predicted_value) + 2.0,
            actual_outbreak_label=True,
            failure_reason="under_prediction",
            notes="Synthetic smoke-test analyst note omitted from output.",
            reviewer_ref="smoke_test",
            metadata_json={"source": "smoke_test", "notes_omitted": True},
        )
    )
    return {
        "ok": bool(result.get("feedback_id")) and bool(result.get("experience_id")),
        "feedback_created": bool(result.get("feedback_created")),
        "experience_created": bool(result.get("experience_created")),
        "feedback_id": result.get("feedback_id"),
        "experience_id": result.get("experience_id"),
        "experience_count": _count(session, ModelExperienceBank),
    }


def run_minimum_data_step(session) -> dict:
    result = MinimumDataAnalyzer(session).analyze(
        MinimumDataAnalysisRequest(
            country=COUNTRY,
            disease=DISEASE,
            horizon_days=HORIZON_DAYS,
            target_metric="mae",
            min_acceptable_score=2.0,
            candidate_feature_groups=["case_counts", "data_quality", "seasonality"],
            history_windows_weeks=[6, 10, 14],
            model_name="feature_rule",
            min_train_size=4,
        )
    )
    payload = result_to_dict(result)
    return {
        "ok": bool(payload.get("learning_curve")),
        "minimum_feature_set": list(payload.get("minimum_feature_set") or []),
        "minimum_history_weeks": payload.get("minimum_history_weeks"),
        "expected_score": payload.get("expected_score"),
        "learning_curve_count": len(payload.get("learning_curve") or []),
        "ablation_count": len(payload.get("ablation_results") or []),
    }


def run_crawler_optimizer_step(session) -> dict:
    report = CrawlerOptimizerAgent(session).analyze(
        country=COUNTRY,
        disease=DISEASE,
        limit_sources=100,
        limit_documents=200,
        include_connector_specs=True,
        apply_safe_actions=False,
    )
    summary = summarize_optimizer_report(report, max_failure_cases=0, max_actions=0, max_connector_specs=0)
    return {
        "ok": summary.get("status") == "ok",
        "status": summary.get("status"),
        "sources_analyzed": summary.get("sources_analyzed", 0),
        "documents_analyzed": summary.get("documents_analyzed", 0),
        "failure_case_count": summary.get("failure_case_count", 0),
        "recommended_action_count": summary.get("recommended_action_count", 0),
        "connector_spec_count": summary.get("connector_spec_count", 0),
        "warnings": summary.get("warnings", []),
    }


def run_access_policy_step() -> dict:
    policies = load_access_policies("configs/source_access_policy.example.json")
    errors: list[str] = []
    for policy in policies.values():
        errors.extend(validate_access_policy(policy))
    return {
        "ok": not errors,
        "policies_checked": len(policies),
        "errors_count": len(errors),
        "guardrail_status": "ok" if not errors else "failed",
        "warnings": errors,
    }


def run_source_discovery_step(session) -> dict:
    report = SourceDiscoveryAgent(session).discover_from_search_results(
        country=COUNTRY,
        disease=DISEASE,
        search_results=_source_discovery_smoke_fixture(),
        min_source_score=0.35,
        generate_connector_specs=True,
    )
    pending_count = len(
        [
            source
            for source in session.scalars(select(SourceRegistry)).all()
            if source.status == "pending_review"
        ]
    )
    return {
        "ok": report.status == "ok" and report.candidates_found > 0,
        "status": report.status,
        "search_results_seen": report.search_results_seen,
        "candidates_found": report.candidates_found,
        "candidates_inserted_or_updated": report.candidates_inserted_or_updated,
        "connector_specs_generated": report.connector_specs_generated,
        "pending_review_sources": pending_count,
        "network_accessed": False,
        "limitations": "Fixture/mock source discovery only; human review is required before activation.",
    }


def run_api_smoke_step(database_url: str) -> dict:
    def override_session():
        with get_session(database_url) as session:
            yield session

    checks: dict[str, int] = {}
    app.dependency_overrides[get_db_session] = override_session
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            from fastapi.testclient import TestClient

            with TestClient(app) as client:
                _record_response(checks, "health", client.get("/health"))
                _record_response(checks, "sources_health", client.get("/sources/health"))
                _record_response(
                    checks,
                    "events",
                    client.get("/events", params={"country": COUNTRY, "disease": DISEASE}),
                )
                _record_response(
                    checks,
                    "features",
                    client.get(
                        "/features",
                        params={"country": COUNTRY, "disease": DISEASE, "horizon_days": HORIZON_DAYS},
                    ),
                )
                _record_response(
                    checks,
                    "predictions",
                    client.get(
                        "/predictions",
                        params={"country": COUNTRY, "disease": DISEASE, "horizon_days": HORIZON_DAYS},
                    ),
                )
                _record_response(checks, "feedback_summary", client.get("/feedback/summary"))
                _record_response(checks, "jobs_schedule", client.get("/jobs/schedule"))
                _record_response(checks, "access_policy", client.get("/access-policy"))
                _record_response(
                    checks,
                    "access_policy_check",
                    client.post(
                        "/access-policy/check",
                        json={
                            "source_key": "cdc_norostat",
                            "url": "https://example.invalid/test",
                            "allow_network": False,
                            "request_count": 1,
                        },
                    ),
                )
                _record_response(
                    checks,
                    "minimum_data",
                    client.post(
                        "/analysis/minimum-data",
                        json={
                            "country": COUNTRY,
                            "disease": DISEASE,
                            "horizon_days": HORIZON_DAYS,
                            "target_metric": "mae",
                            "min_acceptable_score": 2.0,
                            "candidate_feature_groups": ["case_counts", "data_quality", "seasonality"],
                            "history_windows_weeks": [6, 10, 14],
                            "model_name": "feature_rule",
                            "min_train_size": 4,
                        },
                    ),
                )
                _record_response(
                    checks,
                    "crawler_optimizer",
                    client.post(
                        "/crawler-optimizer/analyze",
                        json={"country": COUNTRY, "disease": DISEASE, "limit_sources": 100, "limit_documents": 200},
                    ),
                )
                _record_response(
                    checks,
                    "source_discovery",
                    client.post(
                        "/source-discovery/discover-fixture",
                        json={
                            "country": COUNTRY,
                            "disease": DISEASE,
                            "search_results": _source_discovery_smoke_fixture(),
                            "min_source_score": 0.35,
                            "generate_connector_specs": True,
                        },
                    ),
                )
    finally:
        app.dependency_overrides.clear()

    checked = len(checks)
    failures = {name: status for name, status in checks.items() if status >= 400}
    return {
        "ok": not failures and checked >= 7,
        "checked_endpoints": checked,
        "status_codes": checks,
        "failed_endpoints": sorted(failures),
    }


def run_worker_smoke_step(database_url: str) -> dict:
    from worker_agent import PipelineRunner, TaskRunner

    with get_session(database_url) as session:
        summary = PipelineRunner(TaskRunner(session)).run_full_local_pipeline(
            country=COUNTRY,
            disease=DISEASE,
            horizon_days=28,
        )
    return {
        "ok": summary.get("status") in {"success", "partial"},
        "status": summary.get("status"),
        "sources": summary.get("sources", 0),
        "raw_documents": summary.get("raw_documents", 0),
        "events": summary.get("events", 0),
        "features": summary.get("features", 0),
        "predictions": summary.get("predictions", 0),
        "warnings_count": len(summary.get("warnings", [])),
    }


def run_smoke_test(
    *,
    database_url: str | None = None,
    include_api: bool = False,
    include_worker: bool = False,
    keep_db: bool = False,
) -> dict:
    temp_dir: Path | None = None
    using_temp = database_url is None
    if database_url is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="epidemic-smoke-"))
        database_url = f"sqlite:///{(temp_dir / 'smoke.db').as_posix()}"

    warnings: list[str] = []
    steps: dict[str, dict] = {}
    status = "ok"

    try:
        init_db(database_url).dispose()
        steps["access_policy"] = _run_step(warnings, "access_policy", run_access_policy_step)
        with get_session(database_url) as session:
            steps["seed_or_sources"] = _run_step(
                warnings,
                "seed_or_sources",
                lambda: _seed_source_step(session),
            )
            steps["source_discovery"] = _run_step(
                warnings,
                "source_discovery",
                lambda: run_source_discovery_step(session),
            )
            steps["raw_documents"] = _run_step(
                warnings,
                "raw_documents",
                lambda: create_synthetic_raw_documents(session),
            )
            steps["crawler_optimizer"] = _run_step(
                warnings,
                "crawler_optimizer",
                lambda: run_crawler_optimizer_step(session),
            )
            steps["cleaner"] = _run_step(warnings, "cleaner", lambda: run_cleaner_step(session))
            steps["features"] = _run_step(warnings, "features", lambda: run_feature_step(session))
            steps["predictor"] = _run_step(warnings, "predictor", lambda: run_predictor_step(session))
            prediction_id = _latest_prediction_id(session)
            steps["feedback"] = _run_step(
                warnings,
                "feedback",
                lambda: run_feedback_step(session, prediction_id),
            )
            steps["minimum_data"] = _run_step(
                warnings,
                "minimum_data",
                lambda: run_minimum_data_step(session),
            )

        if include_api:
            steps["api"] = _run_step(warnings, "api", lambda: run_api_smoke_step(database_url))
        else:
            steps["api"] = {"ok": True, "checked_endpoints": 0, "skipped": True}
        if include_worker:
            steps["worker"] = _run_step(warnings, "worker", lambda: run_worker_smoke_step(database_url))
        else:
            steps["worker"] = {"ok": True, "skipped": True}
    except Exception as exc:
        status = "failed"
        warnings.append(f"smoke test failed: {type(exc).__name__}")

    for name, payload in steps.items():
        if not payload.get("ok"):
            status = "failed"
            warnings.append(f"{name} did not meet smoke-test expectations")

    summary = {
        **_top_level_summary(status, steps, warnings),
        "database_scope": "temp" if using_temp else "custom",
        "steps": steps,
        "network_accessed": False,
        "services_started": False,
        "fastapi_service_started": False,
        "streamlit_service_started": False,
        "worker_daemon_started": False,
        "docker_started": False,
        "orm_or_alembic_changed": False,
    }
    safe_summary = sanitize_smoke_output(summary)

    if using_temp and temp_dir is not None and not keep_db:
        with suppress(Exception):
            shutil.rmtree(temp_dir)
    return safe_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local end-to-end smoke test without network or services.")
    parser.add_argument("--temp-db", action="store_true")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--include-api", action="store_true")
    parser.add_argument("--include-worker", action="store_true")
    parser.add_argument("--keep-db", action="store_true")
    args = parser.parse_args(argv)

    summary = run_smoke_test(
        database_url=None if args.temp_db else args.database_url,
        include_api=args.include_api,
        include_worker=args.include_worker,
        keep_db=args.keep_db,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary.get("status") == "ok" else 1


def _ensure_smoke_source(session) -> SourceRegistry:
    service = SourceRegistryService(session)
    existing = service.get_source_by_base_url("https://local.invalid/smoke-source")
    if existing is not None:
        return existing
    return service.add_source(
        name="Local Smoke Fixture Source",
        base_url="https://local.invalid/smoke-source",
        country_scope=[COUNTRY],
        disease_scope=[DISEASE],
        source_type="fixture",
        authority_score=0.8,
        relevance_score=0.9,
        freshness_score=0.8,
        reliability_score=0.9,
        structured_data_score=0.6,
        historical_success_score=0.8,
        language_country_match=0.9,
        legal_or_robots_penalty=0.0,
        robots_allowed=True,
        parser_type="smoke_fixture",
        status="active",
        metadata_json={"allowed_use": "local_smoke_fixture", "requires_network_for_fetching": False},
    )


def _seed_source_step(session) -> dict:
    service = SourceRegistryService(session)
    created_default = updated_default = 0
    for seed in get_default_seed_sources():
        existed = service.get_source_by_base_url(seed["base_url"]) is not None
        service.upsert_source(**seed)
        if existed:
            updated_default += 1
        else:
            created_default += 1
    source = _ensure_smoke_source(session)
    sources_count = _count(session, SourceRegistry)
    return {
        "ok": bool(source.id),
        "sources_count": sources_count,
        "created_default_sources": created_default,
        "updated_default_sources": updated_default,
        "active_sources_count": len(SourceRegistryService(session).list_active_sources(country=COUNTRY, disease=DISEASE)),
    }


def _synthetic_documents(start: datetime) -> list[dict]:
    documents: list[dict] = []
    for index in range(20):
        published_at = start + timedelta(days=7 * index)
        cases = 8 + index
        outbreaks = 1 if index % 2 == 0 else 0
        if index == 5:
            body = (
                f"本地合成报告，日期 {published_at.date().isoformat()}，美国学校报告 norovirus event "
                f"with {cases} cases and {outbreaks} outbreak. This is fixture data."
            )
        else:
            body = (
                f"On {published_at.strftime('%B')} {published_at.day}, {published_at.year}, "
                f"United States reported {cases} norovirus cases, {cases} confirmed cases, "
                f"{outbreaks} outbreak, 0 deaths, 1 hospitalizations, and {120 + index} tests "
                "in a school setting. This is synthetic fixture data."
            )
        documents.append(
            {
                "kind": "event",
                "title": f"Local norovirus fixture week {index + 1}",
                "body": body,
                "published_at": published_at,
                "country_detected": COUNTRY,
                "disease_detected": DISEASE,
            }
        )

    unrelated_at = start + timedelta(days=7 * 20)
    documents.append(
        {
            "kind": "unrelated",
            "title": "Local unrelated fixture",
            "body": (
                f"On {unrelated_at.strftime('%B')} {unrelated_at.day}, {unrelated_at.year}, "
                "a local exercise discussed weather readiness. No outbreak signal is present."
            ),
            "published_at": unrelated_at,
            "country_detected": None,
            "disease_detected": None,
        }
    )
    return documents


def _html_document(title: str, body: str) -> str:
    return f"<html><head><title>{title}</title></head><body><article><p>{body}</p></article></body></html>"


def _source_discovery_smoke_fixture() -> dict:
    return {
        "query": "United States norovirus outbreak data",
        "results": [
            {
                "title": "CDC NoroSTAT Data Table",
                "url": "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html",
                "snippet": "Data table for suspected and confirmed norovirus outbreaks.",
                "source_name": "CDC NoroSTAT",
                "rank": 1,
            },
            {
                "title": "State health norovirus outbreak dashboard",
                "url": "https://health.example.gov/norovirus/outbreak-dashboard",
                "snippet": "State health dashboard style page for norovirus outbreak indicators.",
                "source_name": "State Health Example",
                "rank": 2,
            },
            {
                "title": "Health news report on norovirus outbreaks",
                "url": "https://www.healthnews.example/norovirus-school-outbreaks",
                "snippet": "Short news summary about recent norovirus school outbreaks.",
                "source_name": "Health News Example",
                "rank": 3,
            },
        ],
    }


def _count(session, model) -> int:
    return len(session.scalars(select(model)).all())


def _latest_prediction_id(session) -> str | None:
    prediction = session.scalar(select(Prediction).order_by(Prediction.id.desc()))
    return str(prediction.id) if prediction is not None else None


def _run_step(warnings: list[str], step_name: str, func) -> dict:
    try:
        payload = func()
    except Exception as exc:
        warnings.append(f"{step_name} failed: {type(exc).__name__}")
        return {"ok": False, "error_type": type(exc).__name__}
    if isinstance(payload, dict):
        return sanitize_smoke_output(payload)
    return {"ok": False, "error_type": "InvalidStepPayload"}


def _record_response(checks: dict[str, int], name: str, response) -> None:
    checks[name] = int(response.status_code)


def sanitize_smoke_output(data: dict) -> dict:
    safe = _sanitize_summary(data)
    return safe if isinstance(safe, dict) else {"value": safe}


def _sanitize_summary(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _should_omit_key(key_text):
                continue
            if _is_sensitive_text(key_text):
                sanitized["redacted_field"] = "***"
            else:
                sanitized[key_text] = _sanitize_summary(item)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_summary(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_summary(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and _is_sensitive_text(value):
            return "***"
        return value
    if hasattr(value, "__dataclass_fields__"):
        return _sanitize_summary(asdict(value))
    return str(type(value).__name__)


def _is_sensitive_text(value: str) -> bool:
    lowered = value.lower()
    if lowered == "key" or (lowered.endswith("_key") and not lowered.endswith("_keys")):
        return True
    if "服务器信息.txt" in lowered:
        return True
    return any(
        marker in lowered
        for marker in (
            "secret",
            "token",
            "password",
            "private key",
            "api_key",
            "ssh_key",
            "credential",
            "服务器信息.txt",
        )
    )


def _should_omit_key(key: str) -> bool:
    lowered = key.lower()
    return any(
        marker in lowered
        for marker in (
            "raw_text",
            "raw_html",
            "html",
            "feature_json",
            "lesson_json",
            "provenance_json",
            "metadata_json",
        )
    )


def _top_level_summary(status: str, steps: dict[str, dict], warnings: list[str]) -> dict:
    predictor = steps.get("predictor", {})
    metrics = predictor.get("metrics", {}) if isinstance(predictor.get("metrics"), dict) else {}
    feedback = steps.get("feedback", {})
    minimum_data = steps.get("minimum_data", {})
    crawler_optimizer = steps.get("crawler_optimizer", {})
    access_policy = steps.get("access_policy", {})
    source_discovery = steps.get("source_discovery", {})
    api_smoke = steps.get("api", {})
    worker_smoke = steps.get("worker", {})
    return {
        "status": status,
        "database": "sqlite",
        "created_sources_count": int((steps.get("seed_or_sources") or {}).get("sources_count") or 0),
        "created_raw_documents_count": int((steps.get("raw_documents") or {}).get("total_raw_documents") or 0),
        "created_events_count": int((steps.get("cleaner") or {}).get("events_created") or 0),
        "features_count": int((steps.get("features") or {}).get("feature_rows") or 0),
        "backtest_metrics": {
            "model_name": metrics.get("model_name"),
            "n_predictions": metrics.get("n_predictions"),
            "mae": metrics.get("mae"),
            "rmse": metrics.get("rmse"),
        },
        "model_version": metrics.get("model_version"),
        "predictions_count": int(predictor.get("predictions_count") or 0),
        "feedback_count": 1 if feedback.get("feedback_id") else 0,
        "experience_count": int(feedback.get("experience_count") or 0),
        "minimum_data": {
            "minimum_feature_set": minimum_data.get("minimum_feature_set") or [],
            "minimum_history_weeks": minimum_data.get("minimum_history_weeks"),
            "expected_score": minimum_data.get("expected_score"),
        },
        "crawler_optimizer": {
            "status": crawler_optimizer.get("status"),
            "failure_case_count": int(crawler_optimizer.get("failure_case_count") or 0),
            "recommended_action_count": int(crawler_optimizer.get("recommended_action_count") or 0),
            "connector_spec_count": int(crawler_optimizer.get("connector_spec_count") or 0),
        },
        "access_policy": {
            "policies_checked": int(access_policy.get("policies_checked") or 0),
            "errors_count": int(access_policy.get("errors_count") or 0),
            "guardrail_status": access_policy.get("guardrail_status"),
        },
        "source_discovery": {
            "status": source_discovery.get("status"),
            "candidates_found": int(source_discovery.get("candidates_found") or 0),
            "candidates_inserted_or_updated": int(source_discovery.get("candidates_inserted_or_updated") or 0),
            "connector_specs_generated": int(source_discovery.get("connector_specs_generated") or 0),
            "network_accessed": bool(source_discovery.get("network_accessed", False)),
        },
        "api_smoke": api_smoke if api_smoke and not api_smoke.get("skipped") else {},
        "worker_smoke": worker_smoke if worker_smoke and not worker_smoke.get("skipped") else {},
        "warnings": sorted(set(warnings)),
        "tests_passed": status == "ok",
    }


if __name__ == "__main__":
    raise SystemExit(main())
