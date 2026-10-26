from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CasePoint(BaseModel):
    date: str
    new_cases: float
    cum_cases: float = 0.0


class PredictTrendRequest(BaseModel):
    country: str
    adm1: str = ""
    date: str = ""
    history_days: int = 60
    horizon_days: int = 14
    case_series: list[CasePoint] = Field(default_factory=list)
    auto_fetch_weather: bool = False
    auto_fetch_mobility: bool = False


class PredictTrendResponse(BaseModel):
    trend: str | None
    confidence: float
    risk_score: float
    evidence: list[str] = Field(default_factory=list)
    baseline_probability: dict[str, float] = Field(default_factory=dict)
    llm_output: dict[str, Any] = Field(default_factory=dict)
    data_quality_flags: list[str] = Field(default_factory=list)
    model_version: str = "p0a_schema_only"
    adapter_version: str = "none"
    blocked_reason: str | None = None


class HealthResponse(BaseModel):
    status: str
    model_loaded: bool
    baseline_report_ready: bool
    online_model_ready: bool
    feature_store_ready: bool
