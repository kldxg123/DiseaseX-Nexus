from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from cleaner_agent.agent import CleanerAgent
from cleaner_agent.features import FeatureBuilder
from common.config import get_settings
from common.db import get_session, init_db
from crawler_agent.default_sources import get_default_seed_sources
from crawler_agent.gdelt import parse_gdelt_articles
from crawler_agent.source_discovery import candidates_from_gdelt_articles
from crawler_agent.source_registry import SourceRegistryService
from predictor_agent.agent import PredictorAgent
from predictor_agent.backtest import backtest_summary, rolling_origin_backtest
from predictor_agent.dataset import load_feature_rows
from predictor_agent.minimum_data_analyzer import (
    MinimumDataAnalysisRequest,
    MinimumDataAnalyzer,
    result_to_dict,
)
from worker_agent.job_registry import get_job_definition
from worker_agent.job_types import (
    JOB_STATUS_FAILED,
    JOB_STATUS_SKIPPED,
    JOB_STATUS_SUCCESS,
    JobRunResult,
    sanitize_error,
    sanitize_for_json,
)


class JobRunner:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or get_settings().database_url

    def run_job(self, name: str, params: dict | None = None) -> JobRunResult:
        started_at = datetime.now(UTC)
        job_name = str(name or "").strip()
        try:
            definition = get_job_definition(job_name)
            merged_params = {**definition.default_params, **(params or {})}
            if definition.allows_network and not _network_flags_approved(merged_params):
                return _result(
                    job_name,
                    JOB_STATUS_SKIPPED,
                    started_at,
                    {"reason": "network jobs are disabled in the local worker MVP"},
                )
            status, summary, warnings = self._dispatch(job_name, merged_params)
            return _result(job_name, status, started_at, summary, warnings=warnings)
        except Exception as exc:
            return _result(
                job_name or "unknown",
                JOB_STATUS_FAILED,
                started_at,
                {"ok": False},
                error=sanitize_error(exc),
            )

    def run_many(self, jobs: list[dict]) -> list[JobRunResult]:
        results: list[JobRunResult] = []
        for job in jobs:
            if not isinstance(job, dict):
                results.append(
                    self.run_job(
                        "",
                        {"invalid_job_payload": True},
                    )
                )
                continue
            results.append(self.run_job(str(job.get("name") or job.get("job_name") or ""), job.get("params") or {}))
        return results

    def _dispatch(self, name: str, params: dict[str, Any]) -> tuple[str, dict, list[str]]:
        if name == "seed_sources":
            return self._seed_sources(params)
        if name in {
            "mock_crawl_url",
            "parse_official_fixture",
            "discover_gdelt_mock",
            "discover_sources_fixture",
            "summarize_feedback",
            "summarize_experiences",
            "optimize_crawlers",
            "check_access_policies",
            "data_quality_report",
            "prepare_live_data_pilot",
            "full_local_pipeline",
            "live_source_search_dry_run",
            "live_source_search_controlled",
        }:
            return self._task_runner_job(name, params)
        if name == "mock_source_discovery":
            return self._mock_source_discovery(params)
        if name == "clean_pending":
            return self._clean_pending(params)
        if name == "build_features":
            return self._build_features(params)
        if name == "backtest_model":
            return self._backtest_model(params)
        if name == "predict_latest":
            return self._predict_latest(params)
        if name == "minimum_data_analysis":
            return self._minimum_data_analysis(params)
        if name == "smoke_test":
            return self._smoke_test(params)
        raise ValueError(f"Unknown job: {name}")

    def _task_runner_job(self, name: str, params: dict[str, Any]) -> tuple[str, dict, list[str]]:
        from worker_agent.tasks import TaskRunner

        init_db(self.database_url).dispose()
        with get_session(self.database_url) as session:
            result = TaskRunner(session).run_task(name, **params)
        job = result.get("job", {})
        status = JOB_STATUS_SUCCESS if result.get("status") == "success" else JOB_STATUS_FAILED
        return status, job.get("summary", {}), list(job.get("warnings", []))

    def _seed_sources(self, _params: dict[str, Any]) -> tuple[str, dict, list[str]]:
        init_db(self.database_url).dispose()
        with get_session(self.database_url) as session:
            service = SourceRegistryService(session)
            inserted_or_updated = 0
            for seed in get_default_seed_sources():
                service.upsert_source(**seed)
                inserted_or_updated += 1
            sources_count = len(service.list_active_sources()) + len(service.list_candidate_sources())
        return JOB_STATUS_SUCCESS, {"sources_count": sources_count, "inserted_or_updated": inserted_or_updated}, []

    def _mock_source_discovery(self, params: dict[str, Any]) -> tuple[str, dict, list[str]]:
        mock_payload = params.get("mock_response_json") or params.get("mock_fixture")
        if not mock_payload:
            return (
                JOB_STATUS_SKIPPED,
                {"reason": "no mock_response_json provided", "candidates": 0, "upserted": 0},
                ["mock_source_discovery skipped because no mock input was provided"],
            )
        init_db(self.database_url).dispose()
        country = _optional_str(params.get("country")) or "United States"
        disease = _optional_str(params.get("disease")) or "norovirus"
        articles = parse_gdelt_articles(mock_payload)
        candidates = candidates_from_gdelt_articles(articles=articles, country=country, disease=disease)
        min_score = float(params.get("min_source_score", 0.0))
        selected = [candidate for candidate in candidates if candidate.source_score >= min_score]
        with get_session(self.database_url) as session:
            service = SourceRegistryService(session)
            for candidate in selected:
                service.upsert_source(**candidate.source_payload)
        return (
            JOB_STATUS_SUCCESS,
            {"articles": len(articles), "candidates": len(candidates), "upserted": len(selected)},
            [],
        )

    def _clean_pending(self, params: dict[str, Any]) -> tuple[str, dict, list[str]]:
        init_db(self.database_url).dispose()
        with get_session(self.database_url) as session:
            summary = CleanerAgent(session).process_pending(
                limit=int(params.get("limit", 100)),
                country=_optional_str(params.get("country")),
                disease=_optional_str(params.get("disease")),
            )
        return JOB_STATUS_SUCCESS, _pick(summary, ["processed", "events_created", "no_event", "errors"]), []

    def _build_features(self, params: dict[str, Any]) -> tuple[str, dict, list[str]]:
        init_db(self.database_url).dispose()
        with get_session(self.database_url) as session:
            summary = FeatureBuilder(session).build(
                country=_optional_str(params.get("country")),
                disease=_optional_str(params.get("disease")),
                admin1=_optional_str(params.get("admin1")),
                horizons_days=_int_tuple(params.get("horizons_days"), default=(7, 28)),
                outbreak_threshold=int(params.get("outbreak_threshold", 3)),
                split_type=str(params.get("split_type", "train")),
                replace_existing=bool(params.get("replace_existing", True)),
            )
        return (
            JOB_STATUS_SUCCESS,
            _pick(summary, ["events_seen", "weekly_rows", "feature_rows", "saved", "updated", "skipped"]),
            [],
        )

    def _backtest_model(self, params: dict[str, Any]) -> tuple[str, dict, list[str]]:
        init_db(self.database_url).dispose()
        horizon_days = int(params.get("horizon_days", 28))
        model_name = str(params.get("model_name", "feature_rule"))
        min_train_size = int(params.get("min_train_size", 4))
        with get_session(self.database_url) as session:
            rows = load_feature_rows(
                session,
                country=_optional_str(params.get("country")),
                disease=_optional_str(params.get("disease")),
                admin1=_optional_str(params.get("admin1")),
                horizons_days=(horizon_days,),
            )
            records = rolling_origin_backtest(rows, model_name=model_name, min_train_size=min_train_size)
            metrics = backtest_summary(records, model_name=model_name)
        if not rows:
            return JOB_STATUS_SKIPPED, {"n_rows": 0, "n_splits": 0, "n_predictions": 0}, ["no feature rows matched"]
        return (
            JOB_STATUS_SUCCESS,
            {
                "n_rows": len(rows),
                "n_splits": len(records),
                "n_predictions": len(records),
                "regression": metrics.get("overall", {}),
                "classification": {},
                "model_name": metrics.get("model_name"),
                "model_version": metrics.get("model_version"),
            },
            [],
        )

    def _predict_latest(self, params: dict[str, Any]) -> tuple[str, dict, list[str]]:
        init_db(self.database_url).dispose()
        with get_session(self.database_url) as session:
            summary = PredictorAgent(session).run(
                country=_optional_str(params.get("country")),
                disease=_optional_str(params.get("disease")),
                admin1=_optional_str(params.get("admin1")),
                horizons_days=_int_tuple(params.get("horizons_days"), default=(7, 28)),
                model_name=str(params.get("model_name", "feature_rule")),
                min_train_size=int(params.get("min_train_size", 4)),
                window_size=int(params.get("window_size", 4)),
                replace_existing=bool(params.get("replace_existing", True)),
            )
        return (
            JOB_STATUS_SUCCESS,
            {
                "features_seen": summary.get("features_seen", 0),
                "backtest_records": summary.get("backtest_records", 0),
                "prediction_rows": summary.get("prediction_rows", 0),
                "saved": summary.get("saved", 0),
                "updated": summary.get("updated", 0),
                "skipped": summary.get("skipped", 0),
            },
            [],
        )

    def _minimum_data_analysis(self, params: dict[str, Any]) -> tuple[str, dict, list[str]]:
        init_db(self.database_url).dispose()
        with get_session(self.database_url) as session:
            result = MinimumDataAnalyzer(session).analyze(
                MinimumDataAnalysisRequest(
                    country=_optional_str(params.get("country")),
                    disease=_optional_str(params.get("disease")),
                    horizon_days=int(params.get("horizon_days", 28)),
                    target_metric=str(params.get("target_metric", "mae")),
                    min_acceptable_score=float(params.get("min_acceptable_score", 1.0)),
                    candidate_feature_groups=params.get("candidate_feature_groups"),
                    history_windows_weeks=[int(item) for item in params.get("history_windows_weeks", [8, 12, 20])],
                    model_name=str(params.get("model_name", "feature_rule")),
                    min_train_size=int(params.get("min_train_size", 4)),
                )
            )
        payload = result_to_dict(result)
        return (
            JOB_STATUS_SUCCESS,
            {
                "minimum_feature_set": payload.get("minimum_feature_set", []),
                "minimum_history_weeks": payload.get("minimum_history_weeks"),
                "expected_score": payload.get("expected_score"),
                "learning_curve_count": len(payload.get("learning_curve", [])),
            },
            [],
        )

    def _smoke_test(self, params: dict[str, Any]) -> tuple[str, dict, list[str]]:
        from scripts.smoke_test import run_smoke_test

        summary = run_smoke_test(
            database_url=params.get("database_url"),
            include_api=bool(params.get("include_api", True)),
            include_worker=bool(params.get("include_worker", False)),
            keep_db=bool(params.get("keep_db", False)),
        )
        status = JOB_STATUS_SUCCESS if summary.get("status") == "ok" else JOB_STATUS_FAILED
        return status, summary, list(summary.get("warnings", []))


def _result(
    job_name: str,
    status: str,
    started_at: datetime,
    summary: dict[str, Any],
    *,
    warnings: list[str] | None = None,
    error: str | None = None,
) -> JobRunResult:
    finished_at = datetime.now(UTC)
    return JobRunResult(
        job_name=job_name,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_seconds=max(0.0, (finished_at - started_at).total_seconds()),
        summary=sanitize_for_json(summary),
        warnings=list(warnings or []),
        error=error,
    )


def _pick(summary: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    return {key: summary.get(key, 0) for key in keys}


def _int_tuple(value: Any, *, default: tuple[int, ...]) -> tuple[int, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if isinstance(value, (list, tuple)):
        return tuple(int(item) for item in value)
    return (int(value),)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _network_flags_approved(params: dict[str, Any]) -> bool:
    return bool(params.get("allow_network")) and bool(params.get("terms_reviewed")) and bool(params.get("manual_reviewed"))
