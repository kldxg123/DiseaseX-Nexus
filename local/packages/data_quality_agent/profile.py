from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from common.models import (
    EpidemicEvent,
    HumanFeedback,
    ModelExperienceBank,
    ModelFeature,
    Prediction,
    RawDocument,
    SourceRegistry,
)


@dataclass
class TableQualitySummary:
    table_name: str
    row_count: int
    key_metrics: dict[str, Any]
    warnings: list[str]


@dataclass
class DataQualityProfile:
    source_registry: TableQualitySummary
    raw_documents: TableQualitySummary
    epidemic_events: TableQualitySummary
    model_features: TableQualitySummary
    predictions: TableQualitySummary
    human_feedback: TableQualitySummary
    model_experience_bank: TableQualitySummary
    overall_warnings: list[str]


def profile_source_registry(session: Session) -> TableQualitySummary:
    rows = list(session.scalars(select(SourceRegistry)).all())
    status_counts = Counter(str(row.status or "unknown") for row in rows)
    score_values = [_source_score(row) for row in rows]
    score_values = [value for value in score_values if value is not None]
    warnings: list[str] = []
    if status_counts.get("disabled", 0):
        warnings.append("disabled sources are present and should not be used for collection.")
    if status_counts.get("degraded", 0):
        warnings.append("degraded sources need review before relying on their records.")
    if not rows:
        warnings.append("source_registry is empty.")
    return TableQualitySummary(
        table_name="source_registry",
        row_count=len(rows),
        key_metrics={
            "total": len(rows),
            "active": status_counts.get("active", 0),
            "pending_review": status_counts.get("pending_review", 0),
            "degraded": status_counts.get("degraded", 0),
            "disabled": status_counts.get("disabled", 0),
            "avg_source_score": _mean(score_values),
            "sources_with_failures": sum(1 for row in rows if int(row.consecutive_failures or 0) > 0),
        },
        warnings=warnings,
    )


def profile_raw_documents(session: Session) -> TableQualitySummary:
    rows = list(session.scalars(select(RawDocument)).all())
    urls = [str(row.normalized_url or row.original_url or "") for row in rows]
    hashes = [str(row.content_hash or "") for row in rows]
    status_counts = Counter(str(row.extraction_status or "unknown") for row in rows)
    language_counts = Counter(_metadata_value(row.metadata_json, "language") or "unknown" for row in rows)
    empty_text_count = sum(1 for row in rows if not str(row.body_text or "").strip())
    short_text_count = sum(1 for row in rows if 0 < len(str(row.body_text or "").strip()) < 80)
    duplicate_url_count = _duplicate_count(urls)
    duplicate_hash_count = _duplicate_count(hashes)
    warnings: list[str] = []
    if duplicate_url_count:
        warnings.append("duplicate raw document URLs detected.")
    if duplicate_hash_count:
        warnings.append("duplicate raw document content hashes detected.")
    if empty_text_count or short_text_count:
        warnings.append("some raw documents have empty or very short extracted text.")
    return TableQualitySummary(
        table_name="raw_documents",
        row_count=len(rows),
        key_metrics={
            "total": len(rows),
            "unique_urls": len(set(item for item in urls if item)),
            "unique_content_hashes": len(set(item for item in hashes if item)),
            "duplicate_url_count": duplicate_url_count,
            "duplicate_hash_count": duplicate_hash_count,
            "extraction_status_counts": dict(sorted(status_counts.items())),
            "empty_text_count": empty_text_count,
            "short_text_count": short_text_count,
            "language_counts": dict(sorted(language_counts.items())),
        },
        warnings=warnings,
    )


def profile_epidemic_events(session: Session) -> TableQualitySummary:
    rows = list(session.scalars(select(EpidemicEvent)).all())
    confidence_values = [float(row.confidence_score or 0.0) for row in rows]
    setting_counts = Counter(str(row.setting or "unknown") for row in rows)
    missing_numeric_signal_count = sum(
        1
        for row in rows
        if all(
            getattr(row, name) is None
            for name in (
                "cases",
                "suspected_cases",
                "confirmed_cases",
                "deaths",
                "hospitalizations",
                "tests",
                "outbreaks_count",
            )
        )
    )
    low_confidence_count = sum(1 for value in confidence_values if value < 0.5)
    warnings: list[str] = []
    if any(row.country is None for row in rows):
        warnings.append("some epidemic_events rows are missing country.")
    if any(row.disease is None for row in rows):
        warnings.append("some epidemic_events rows are missing disease.")
    if any(row.event_date is None or row.iso_week is None for row in rows):
        warnings.append("some epidemic_events rows are missing event_date or iso_week.")
    if low_confidence_count:
        warnings.append("low-confidence event extractions need human review.")
    return TableQualitySummary(
        table_name="epidemic_events",
        row_count=len(rows),
        key_metrics={
            "total": len(rows),
            "countries_count": len({row.country for row in rows if row.country}),
            "diseases_count": len({row.disease for row in rows if row.disease}),
            "missing_country_count": sum(1 for row in rows if not row.country),
            "missing_disease_count": sum(1 for row in rows if not row.disease),
            "missing_event_date_count": sum(1 for row in rows if row.event_date is None),
            "missing_iso_week_count": sum(1 for row in rows if not row.iso_week),
            "missing_numeric_signal_count": missing_numeric_signal_count,
            "avg_confidence_score": _mean(confidence_values),
            "low_confidence_count": low_confidence_count,
            "setting_counts": dict(sorted(setting_counts.items())),
        },
        warnings=warnings,
    )


def profile_model_features(session: Session) -> TableQualitySummary:
    rows = list(session.scalars(select(ModelFeature)).all())
    horizon_counts = Counter(int(row.horizon_days or 0) for row in rows)
    split_counts = Counter(str(row.split_type or "unknown") for row in rows)
    feature_key_counts: Counter[str] = Counter()
    target_dates = [row.target_date for row in rows if row.target_date is not None]
    for row in rows:
        if isinstance(row.feature_json, dict):
            for key in row.feature_json:
                feature_key_counts[str(key)] += 1
    warnings: list[str] = []
    if not rows:
        warnings.append("model_features is empty.")
    if any(row.horizon_days <= 0 for row in rows):
        warnings.append("non-positive horizon_days detected in model_features.")
    return TableQualitySummary(
        table_name="model_features",
        row_count=len(rows),
        key_metrics={
            "total": len(rows),
            "horizon_counts": {str(key): value for key, value in sorted(horizon_counts.items())},
            "split_type_counts": dict(sorted(split_counts.items())),
            "missing_target_value_count": sum(1 for row in rows if row.target_value is None),
            "missing_target_label_count": sum(1 for row in rows if row.target_outbreak_label is None),
            "feature_key_coverage": dict(sorted(feature_key_counts.items())),
            "target_date_min": _date_or_none(min(target_dates) if target_dates else None),
            "target_date_max": _date_or_none(max(target_dates) if target_dates else None),
        },
        warnings=warnings,
    )


def profile_predictions(session: Session) -> TableQualitySummary:
    rows = list(session.scalars(select(Prediction)).all())
    model_versions = Counter(f"{row.model_name}:{row.model_version}" for row in rows)
    risk_level_counts = Counter(_prediction_risk_level(row) for row in rows)
    target_dates = [row.target_date for row in rows if row.target_date is not None]
    warnings: list[str] = []
    if rows and any(row.review_status != "reviewed" for row in rows):
        warnings.append("some predictions are not reviewed by an analyst.")
    return TableQualitySummary(
        table_name="predictions",
        row_count=len(rows),
        key_metrics={
            "total": len(rows),
            "model_versions": dict(sorted(model_versions.items())),
            "risk_level_counts": dict(sorted(risk_level_counts.items())),
            "prediction_date_min": _date_or_none(min(target_dates) if target_dates else None),
            "prediction_date_max": _date_or_none(max(target_dates) if target_dates else None),
        },
        warnings=warnings,
    )


def profile_feedback_and_experience(
    session: Session,
) -> tuple[TableQualitySummary, TableQualitySummary]:
    feedback_rows = list(session.scalars(select(HumanFeedback)).all())
    experience_rows = list(session.scalars(select(ModelExperienceBank)).all())
    verdict_counts = Counter(str(row.feedback_status or "unknown") for row in feedback_rows)
    failure_type_counts = Counter(
        str(row.failure_type or row.failure_reason or "unknown") for row in experience_rows
    )
    weights = [
        float(row.weight_for_retraining)
        for row in experience_rows
        if row.weight_for_retraining is not None
    ]
    feedback_warnings: list[str] = []
    experience_warnings: list[str] = []
    if experience_rows and len(experience_rows) < len(feedback_rows):
        experience_warnings.append("some feedback rows do not yet have experience-bank records.")
    feedback_summary = TableQualitySummary(
        table_name="human_feedback",
        row_count=len(feedback_rows),
        key_metrics={
            "feedback_total": len(feedback_rows),
            "verdict_counts": dict(sorted(verdict_counts.items())),
            "experience_total": len(experience_rows),
            "failure_type_counts": dict(sorted(failure_type_counts.items())),
            "avg_weight_for_retraining": _mean(weights),
        },
        warnings=feedback_warnings,
    )
    experience_summary = TableQualitySummary(
        table_name="model_experience_bank",
        row_count=len(experience_rows),
        key_metrics={
            "feedback_total": len(feedback_rows),
            "verdict_counts": dict(sorted(verdict_counts.items())),
            "experience_total": len(experience_rows),
            "failure_type_counts": dict(sorted(failure_type_counts.items())),
            "avg_weight_for_retraining": _mean(weights),
        },
        warnings=experience_warnings,
    )
    return feedback_summary, experience_summary


def build_data_quality_profile(session: Session) -> DataQualityProfile:
    feedback, experience = profile_feedback_and_experience(session)
    profile = DataQualityProfile(
        source_registry=profile_source_registry(session),
        raw_documents=profile_raw_documents(session),
        epidemic_events=profile_epidemic_events(session),
        model_features=profile_model_features(session),
        predictions=profile_predictions(session),
        human_feedback=feedback,
        model_experience_bank=experience,
        overall_warnings=[],
    )
    warnings: list[str] = []
    for summary in (
        profile.source_registry,
        profile.raw_documents,
        profile.epidemic_events,
        profile.model_features,
        profile.predictions,
        profile.human_feedback,
        profile.model_experience_bank,
    ):
        warnings.extend(summary.warnings)
    profile.overall_warnings = warnings
    return profile


def _source_score(row: SourceRegistry) -> float | None:
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    if metadata.get("source_score") is not None:
        return _float_or_none(metadata.get("source_score"))
    values = [
        row.authority_score,
        row.relevance_score,
        row.freshness_score,
        row.reliability_score,
        row.structured_data_score,
        row.historical_success_score,
        row.language_country_match,
    ]
    numeric = [_float_or_none(value) for value in values]
    numeric = [value for value in numeric if value is not None]
    return _mean(numeric) if numeric else None


def _prediction_risk_level(row: Prediction) -> str:
    for payload in (row.provenance_json, row.metrics_json, row.prediction_interval_json):
        if isinstance(payload, dict):
            risk = payload.get("risk_level")
            if risk:
                return str(risk)
            probability = payload.get("predicted_outbreak_probability")
            if probability is not None:
                return _risk_from_probability(probability)
    return "unknown"


def _risk_from_probability(value: Any) -> str:
    numeric = _float_or_none(value)
    if numeric is None:
        return "unknown"
    if numeric >= 0.7:
        return "high"
    if numeric >= 0.4:
        return "medium"
    return "low"


def _duplicate_count(values: list[str]) -> int:
    counts = Counter(item for item in values if item)
    return sum(count - 1 for count in counts.values() if count > 1)


def _metadata_value(metadata: Any, key: str) -> str | None:
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(key)
    return None if value is None else str(value)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date_or_none(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
