from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.api.schemas import HealthResponse, PredictTrendRequest, PredictTrendResponse
from src.utils.config import load_config, resolve_path

try:
    from fastapi import FastAPI  # type: ignore
except Exception:
    FastAPI = None  # type: ignore


class DummyApp:
    def get(self, _path: str, **_kwargs):
        def decorator(fn):
            return fn

        return decorator

    def post(self, _path: str, **_kwargs):
        def decorator(fn):
            return fn

        return decorator


app = FastAPI(title="Ebola P0a Baseline Schema API") if FastAPI else DummyApp()


def _baseline_report_path() -> Path:
    return resolve_path("data/outputs/reports/baseline_results.json")


def _online_model_path() -> Path:
    return resolve_path("models/baseline/p0a_online_model.joblib")


def _feature_store_path() -> Path:
    return resolve_path("data/processed/ebola_quant_panel.parquet")


def _baseline_report_ready() -> bool:
    path = _baseline_report_path()
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    return payload.get("status") == "ok"


def health() -> dict[str, Any]:
    feature_store = _feature_store_path()
    response = HealthResponse(
        status="ok",
        model_loaded=_online_model_path().exists(),
        baseline_report_ready=_baseline_report_ready(),
        online_model_ready=_online_model_path().exists(),
        feature_store_ready=feature_store.exists() or feature_store.with_suffix(feature_store.suffix + ".jsonl").exists(),
    )
    return response.model_dump()


def _blocked_response(payload: PredictTrendRequest, reason: str, flags: list[str]) -> dict[str, Any]:
    response = PredictTrendResponse(
        trend=None,
        confidence=0.0,
        risk_score=0.0,
        evidence=[
            "P0a validates schema only for online prediction. Long-running serving and model export are outside P0a.",
            f"country={payload.country}, adm1={payload.adm1}, horizon_days={payload.horizon_days}",
        ],
        baseline_probability={},
        llm_output={},
        data_quality_flags=flags,
        model_version="p0a_baseline_not_deployed",
        adapter_version="none",
        blocked_reason=reason,
    )
    return response.model_dump()


def predict_trend(payload: dict[str, Any] | PredictTrendRequest) -> dict[str, Any]:
    request = payload if isinstance(payload, PredictTrendRequest) else PredictTrendRequest.model_validate(payload)
    flags: list[str] = []
    if not request.case_series:
        flags.append("missing_case_series")
    if request.auto_fetch_weather or request.auto_fetch_mobility:
        flags.append("external_fetch_disabled_in_p0a")
    if not _online_model_path().exists():
        flags.append("online_baseline_artifact_missing")
        return _blocked_response(request, "online_baseline_artifact_missing", flags)
    flags.append("online_prediction_not_enabled_in_p0a")
    return _blocked_response(request, "online_prediction_not_enabled_in_p0a", flags)


@app.get("/health", response_model=HealthResponse if FastAPI else None)
def health_endpoint() -> dict[str, Any]:
    return health()


@app.post("/predict_trend", response_model=PredictTrendResponse if FastAPI else None)
def predict_trend_endpoint(payload: PredictTrendRequest) -> dict[str, Any]:
    return predict_trend(payload)
