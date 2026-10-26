from __future__ import annotations

import os
from typing import Any

import httpx

from apps.dashboard.safety import sanitize_dict, truncate_text

DEFAULT_API_BASE_URL = "http://127.0.0.1:8001"


class DashboardApiError(Exception):
    pass


class DashboardApiClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
        *,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = (base_url or os.getenv("API_BASE_URL") or DEFAULT_API_BASE_URL).rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.transport = transport

    def health(self) -> dict:
        return self._get("/health")

    def get_sources(
        self,
        *,
        status: str | None = None,
        country: str | None = None,
        disease: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        payload = self._get(
            "/sources",
            params={"status": status, "country": country, "disease": disease, "limit": limit},
        )
        return list(payload.get("sources", []))

    def get_source_health(self) -> dict:
        return self._get("/sources/health")

    def seed_sources(self, *, dry_run: bool = False) -> dict:
        return self._post("/sources/seed", json={"dry_run": dry_run})

    def review_source(self, source_id: int, payload: dict) -> dict:
        return self._post(f"/sources/{int(source_id)}/review", json=payload)

    def discover_gdelt_mock(self, payload: dict) -> dict:
        return self._post("/crawl/discover-gdelt-mock", json=payload)

    def source_discovery_discover_fixture(
        self,
        *,
        country: str,
        disease: str,
        search_results: dict,
        min_source_score: float = 0.35,
        generate_connector_specs: bool = True,
    ) -> dict:
        return self._post(
            "/source-discovery/discover-fixture",
            json={
                "country": country,
                "disease": disease,
                "search_results": search_results,
                "min_source_score": min_source_score,
                "generate_connector_specs": generate_connector_specs,
            },
        )

    def source_discovery_live_controlled(self, payload: dict) -> dict:
        return self._post("/source-discovery/discover-live-controlled", json=payload, timeout_seconds=45.0)

    def live_source_search_dry_run(self, payload: dict) -> dict:
        return self._post("/live-source-search/dry-run", json=payload, timeout_seconds=20.0)

    def live_source_search_run(self, payload: dict) -> dict:
        return self._post("/live-source-search/run", json=payload, timeout_seconds=60.0)

    def live_source_search_metadata_check(self, payload: dict) -> dict:
        return self._post("/live-source-search/metadata-check", json=payload, timeout_seconds=60.0)

    def live_source_search_reports(self) -> dict:
        return self._get("/live-source-search/reports")

    def live_source_search_review_queue(self, run_id: str) -> dict:
        return self._get(f"/live-source-search/review-queue/{run_id}")

    def live_source_search_metadata_results(self, run_id: str) -> dict:
        return self._get(f"/live-source-search/metadata-check-results/{run_id}")

    def live_source_search_review_candidates(self, run_id: str) -> dict:
        return self._get(f"/live-source-search/review-queue/{run_id}/candidates")

    def live_source_acquisition_plan(self, payload: dict) -> dict:
        return self._post("/live-source-search/acquisition/plan", json=payload, timeout_seconds=20.0)

    def live_source_acquisition_run(self, payload: dict) -> dict:
        return self._post("/live-source-search/acquisition/run", json=payload, timeout_seconds=60.0)

    def live_source_acquisition_report(self, run_id: str) -> dict:
        return self._get(f"/live-source-search/acquisition/reports/{run_id}")

    def live_source_acquisition_quality_diagnose(self, payload: dict) -> dict:
        return self._post("/live-source-search/acquisition/diagnose-quality", json=payload, timeout_seconds=30.0)

    def live_source_acquisition_quality_report(self, run_id: str) -> dict:
        return self._get(f"/live-source-search/acquisition/quality-report/{run_id}")

    def crawl_mock(self, payload: dict) -> dict:
        return self._post("/crawl/mock", json=payload)

    def crawl_mock_url(self, payload: dict) -> dict:
        return self._post("/crawl/mock-url", json=payload)

    def parse_official_connector(self, payload: dict) -> dict:
        return self._post("/connectors/official/parse", json=payload)

    def parse_official_fixture(self, payload: dict) -> dict:
        return self._post("/crawl/parse-official-fixture", json=payload)

    def run_cleaner(self, payload: dict) -> dict:
        return self._post("/clean/run", json=payload)

    def get_events(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        payload = self._get("/events", params={"country": country, "disease": disease, "limit": limit})
        return list(payload.get("events", []))

    def build_features(self, payload: dict) -> dict:
        return self._post("/features/build", json=payload)

    def get_features(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        horizon_days: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        payload = self._get(
            "/features",
            params={"country": country, "disease": disease, "horizon_days": horizon_days, "limit": limit},
        )
        return list(payload.get("features", []))

    def backtest_model(self, payload: dict) -> dict:
        return self._post("/models/backtest", json=payload)

    def get_backtest_metrics(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        admin1: str | None = None,
        horizon_days: int = 28,
        model_name: str = "moving_average",
        min_train_size: int = 8,
        test_window: int = 1,
    ) -> dict:
        return self._get(
            "/metrics/backtest",
            params={
                "country": country,
                "disease": disease,
                "admin1": admin1,
                "horizon_days": horizon_days,
                "model_name": model_name,
                "min_train_size": min_train_size,
                "test_window": test_window,
            },
        )

    def predict_model(self, payload: dict) -> dict:
        return self._post("/models/predict", json=payload)

    def get_predictions(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        horizon_days: int | None = None,
        limit: int = 100,
    ) -> list[dict]:
        payload = self._get(
            "/predictions",
            params={"country": country, "disease": disease, "horizon_days": horizon_days, "limit": limit},
        )
        return list(payload.get("predictions", []))

    def add_feedback(self, payload: dict) -> dict:
        return self._post("/feedback", json=payload)

    def get_feedback(self, *, verdict: str | None = None, country: str | None = None, disease: str | None = None, limit: int = 100) -> list[dict]:
        payload = self._get(
            "/feedback",
            params={"verdict": verdict, "country": country, "disease": disease, "limit": limit},
        )
        return list(payload.get("feedback", []))

    def get_feedback_summary(self, *, country: str | None = None, disease: str | None = None) -> dict:
        _unused = (country, disease)
        return self._get("/feedback/summary")

    def get_experiences(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        failure_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        payload = self._get(
            "/experiences",
            params={"country": country, "disease": disease, "failure_type": failure_type, "limit": limit},
        )
        return list(payload.get("experiences", []))

    def get_experience_summary(self, *, country: str | None = None, disease: str | None = None) -> dict:
        _unused = (country, disease)
        return self._get("/experience/summary")

    def build_experience(self, payload: dict) -> dict:
        return self._post("/experience/build", json=payload)

    def minimum_data_analysis(self, payload: dict) -> dict:
        return self._post("/analysis/minimum-data", json=payload)

    def model_promotion_check(self, payload: dict) -> dict:
        return self._post("/analysis/model-promotion-check", json=payload)

    def crawler_optimizer_analyze(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
        limit_sources: int = 100,
        limit_documents: int = 200,
        include_connector_specs: bool = True,
        apply_safe_actions: bool = False,
    ) -> dict:
        return self._post(
            "/crawler-optimizer/analyze",
            json={
                "country": country,
                "disease": disease,
                "limit_sources": limit_sources,
                "limit_documents": limit_documents,
                "include_connector_specs": include_connector_specs,
                "apply_safe_actions": apply_safe_actions,
            },
        )

    def crawler_optimizer_analyze_source(
        self,
        *,
        source_id: str,
        country: str | None = None,
        disease: str | None = None,
    ) -> dict:
        return self._post(
            "/crawler-optimizer/analyze-source",
            json={"source_id": source_id, "country": country, "disease": disease},
        )

    def crawler_optimizer_connector_spec(
        self,
        *,
        source_id: str,
        markdown: bool = False,
    ) -> dict:
        return self._post(
            "/crawler-optimizer/connector-spec",
            json={"source_id": source_id, "markdown": markdown},
        )

    def get_access_policies(self) -> dict:
        return self._get("/access-policy")

    def check_access_policy(
        self,
        *,
        source_key: str,
        url: str | None = None,
        allow_network: bool = False,
        terms_reviewed: bool = False,
        manual_reviewed: bool = False,
        robots_allowed: bool | None = None,
        request_count: int = 1,
    ) -> dict:
        return self._post(
            "/access-policy/check",
            json={
                "source_key": source_key,
                "url": url,
                "allow_network": allow_network,
                "terms_reviewed": terms_reviewed,
                "manual_reviewed": manual_reviewed,
                "robots_allowed": robots_allowed,
                "request_count": request_count,
            },
        )

    def get_data_quality_profile(self) -> dict:
        return self._get("/data-quality/profile")

    def get_data_quality_label_audit(
        self,
        *,
        country: str | None = None,
        disease: str | None = None,
    ) -> dict:
        return self._get("/data-quality/label-audit", params={"country": country, "disease": disease})

    def build_data_quality_report(self, payload: dict) -> dict:
        return self._post("/data-quality/report", json=payload)

    def deepseek_web_search_plan(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/plan", json=payload, timeout_seconds=20.0)

    def deepseek_web_search_run(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/run", json=payload, timeout_seconds=120.0)

    def deepseek_web_search_runs(self, *, limit: int = 50) -> dict:
        return self._get("/deepseek-web-search/runs", params={"limit": limit})

    def deepseek_web_search_records(
        self,
        *,
        disease: str | None = None,
        country: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
    ) -> dict:
        return self._get(
            "/deepseek-web-search/records",
            params={"disease": disease, "country": country, "run_id": run_id, "limit": limit},
        )

    def deepseek_web_search_send_to_cleaning(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/send-to-cleaning", json=payload, timeout_seconds=30.0)

    def deepseek_web_search_3d_data(
        self,
        *,
        disease: str,
        country: str,
        mode: str = "auto",
    ) -> dict:
        return self._get(
            "/deepseek-web-search/visualization/3d-data",
            params={"disease": disease, "country": country, "mode": mode},
        )

    def deepseek_api_diagnose(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/diagnose-api", json=payload, timeout_seconds=30.0)

    def deepseek_replay_extraction(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/replay-extraction", json=payload, timeout_seconds=60.0)

    def deepseek_diagnostics(self, run_id: str) -> dict:
        return self._get(f"/deepseek-web-search/diagnostics/{run_id}")

    def deepseek_extraction_quality_audit(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/extraction-quality-audit", json=payload, timeout_seconds=30.0)

    def deepseek_readiness_review_summary(self, *, report_dir: str | None = None) -> dict:
        return self._get("/deepseek-web-search/readiness-review/summary", params={"report_dir": report_dir})

    def deepseek_readiness_review_table(
        self,
        *,
        report_dir: str | None = None,
        readiness: str | None = None,
        blocker: str | None = None,
    ) -> dict:
        return self._get(
            "/deepseek-web-search/readiness-review/table",
            params={"report_dir": report_dir, "readiness": readiness, "blocker": blocker},
        )

    def deepseek_readiness_manual_review_queue(self, *, report_dir: str | None = None) -> dict:
        return self._get("/deepseek-web-search/readiness-review/manual-review-queue", params={"report_dir": report_dir})

    def deepseek_readiness_review_export(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/readiness-review/export", json=payload, timeout_seconds=30.0)

    def deepseek_human_review_template(self, *, report_dir: str | None = None) -> dict:
        return self._get("/deepseek-web-search/human-review/template", params={"report_dir": report_dir})

    def deepseek_human_review_export_template(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/human-review/export-template", json=payload, timeout_seconds=30.0)

    def deepseek_human_review_import_annotations(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/human-review/import-annotations", json=payload, timeout_seconds=30.0)

    def deepseek_human_review_promote_events(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/human-review/promote-events", json=payload, timeout_seconds=30.0)

    def deepseek_human_review_promotion_report(self, *, output_dir: str | None = None) -> dict:
        return self._get("/deepseek-web-search/human-review/promotion-report", params={"output_dir": output_dir})

    def deepseek_annotation_workspace_rows(self, *, report_dir: str | None = None) -> dict:
        return self._get("/deepseek-web-search/annotation-workspace/rows", params={"report_dir": report_dir})

    def deepseek_annotation_workspace_export_template(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/annotation-workspace/export-template", json=payload, timeout_seconds=30.0)

    def deepseek_annotation_workspace_validate(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/annotation-workspace/validate", json=payload, timeout_seconds=30.0)

    def deepseek_annotation_workspace_preview_promotion(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/annotation-workspace/preview-promotion", json=payload, timeout_seconds=30.0)

    def deepseek_annotation_workspace_export_bundle(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/annotation-workspace/export-bundle", json=payload, timeout_seconds=30.0)

    def deepseek_table_review_build_source_pack(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/table-review/build-source-pack", json=payload, timeout_seconds=20.0)

    def deepseek_table_review_run_mock(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/table-review/run-mock", json=payload, timeout_seconds=30.0)

    def deepseek_table_review_run_deepseek(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/table-review/run-deepseek", json=payload, timeout_seconds=60.0)

    def deepseek_table_review_validate(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/table-review/validate", json=payload, timeout_seconds=30.0)

    def deepseek_table_review_clean_validate(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/table-review/clean-validate", json=payload, timeout_seconds=30.0)

    def deepseek_table_review_export_review_table(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/table-review/export-review-table", json=payload, timeout_seconds=30.0)

    def deepseek_table_review_preview_ingestion(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/table-review/preview-ingestion", json=payload, timeout_seconds=30.0)

    def deepseek_table_review_visualization(self, run_id: str, *, output_dir: str | None = None) -> dict:
        return self._get("/deepseek-web-search/table-review/visualization/{run_id}".format(run_id=run_id), params={"output_dir": output_dir})

    def deepseek_table_review_ingest_approved(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/table-review/ingest-approved", json=payload, timeout_seconds=30.0)

    def deepseek_table_review_goal_mode(self, payload: dict) -> dict:
        return self._post("/deepseek-web-search/table-review/goal-mode", json=payload, timeout_seconds=180.0)

    def who_data_acquisition_dry_run(self, payload: dict) -> dict:
        return self._post("/who-data-acquisition/dry-run", json=payload, timeout_seconds=30.0)

    def who_data_acquisition_run(self, payload: dict) -> dict:
        return self._post("/who-data-acquisition/run", json=payload, timeout_seconds=90.0)

    def who_data_acquisition_report(self, run_id: str, *, output_dir: str | None = None) -> dict:
        return self._get(f"/who-data-acquisition/reports/{run_id}", params={"output_dir": output_dir})

    def who_data_acquisition_ingest_approved(self, payload: dict) -> dict:
        return self._post("/who-data-acquisition/ingest-approved", json=payload, timeout_seconds=30.0)

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict:
        return self._request("GET", path, params=params)

    def _post(self, path: str, *, json: dict[str, Any], timeout_seconds: float | None = None) -> dict:
        return self._request("POST", path, json=json, timeout_seconds=timeout_seconds)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        safe_params = {key: value for key, value in (params or {}).items() if value is not None}
        try:
            with httpx.Client(
                base_url=self.base_url,
                timeout=float(timeout_seconds or self.timeout_seconds),
                transport=self.transport,
            ) as client:
                response = client.request(method, path, params=safe_params or None, json=json)
            if response.status_code >= 400:
                raise DashboardApiError(
                    f"API request failed: {method} {path} status={response.status_code} "
                    f"body={_safe_error_body(response)}"
                )
            payload = response.json()
            if not isinstance(payload, dict):
                raise DashboardApiError(f"API request failed: {method} {path} returned a non-object payload")
            return sanitize_dict(payload)
        except DashboardApiError:
            raise
        except Exception as exc:
            raise DashboardApiError(f"API request failed: {method} {path}: {type(exc).__name__}") from exc


def _safe_error_body(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        payload = {"detail": "unavailable"}
    return truncate_text(str(sanitize_dict(payload if isinstance(payload, dict) else {"detail": payload})), 240) or ""
