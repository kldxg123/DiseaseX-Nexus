from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select

from common.db import get_session
from common.models import EpidemicEvent, ModelFeature, RawDocument
from live_source_search_orchestrator.feature_alignment_gate import (
    NUMERIC_FIELDS,
    build_feature_generation_blocker_summary,
)

FEATURE_READY = "feature_ready"
EVENT_ONLY = "event_only"
DESCRIPTIVE_ONLY = "descriptive_only"
INSUFFICIENT_TEMPORAL_SIGNAL = "insufficient_temporal_signal"
INSUFFICIENT_NUMERIC_SIGNAL = "insufficient_numeric_signal"
INSUFFICIENT_TARGET_SIGNAL = "insufficient_target_signal"
REQUIRES_STRUCTURED_DATASET = "requires_structured_dataset"
REQUIRES_MANUAL_REVIEW = "requires_manual_review"


@dataclass(frozen=True)
class EventQualityAssessment:
    quality_status: str
    event_count: int
    feature_ready_event_count: int
    disease_known_count: int
    country_known_count: int
    event_date_count: int
    iso_week_count: int
    parseable_iso_week_count: int
    distinct_week_count: int
    numeric_event_count: int
    duplicate_event_count: int
    single_page_summary_risk: bool
    descriptive_or_portal_risk: bool
    numeric_field_counts: dict[str, int] = field(default_factory=dict)
    blocker_types: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureEligibilityAssessment:
    eligible: bool
    quality_status: str
    feature_ready_event_count: int
    blocker_types: list[str]
    weekly_alignment: dict[str, Any]
    minimum_time_series: dict[str, Any]
    numeric_target: dict[str, Any]
    horizon_label: dict[str, Any]
    explanation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FeatureGenerationBlocker:
    blocker_type: str
    severity: str
    evidence_summary: str
    recommended_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AcquisitionQualityDiagnosticReport:
    status: str
    run_dir: str
    raw_documents_count: int
    epidemic_events_count: int
    model_features_count: int
    event_quality: EventQualityAssessment
    feature_eligibility: FeatureEligibilityAssessment
    blockers: list[FeatureGenerationBlocker]
    recommended_next_action: str
    report_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event_quality"] = self.event_quality.to_dict()
        payload["feature_eligibility"] = self.feature_eligibility.to_dict()
        payload["blockers"] = [item.to_dict() for item in self.blockers]
        return payload


def load_acquisition_outputs(run_dir: str | Path, database_url: str | None = None) -> dict[str, Any]:
    root = Path(run_dir)
    db_url = database_url or _infer_database_url(root)
    raw_documents: list[RawDocument] = []
    events: list[EpidemicEvent] = []
    features: list[ModelFeature] = []
    if db_url:
        with get_session(db_url) as session:
            raw_documents = list(session.scalars(select(RawDocument).order_by(RawDocument.id)).all())
            events = list(session.scalars(select(EpidemicEvent).order_by(EpidemicEvent.id)).all())
            features = list(session.scalars(select(ModelFeature).order_by(ModelFeature.id)).all())
            for event in events:
                _ = event.raw_document
    return {
        "run_dir": str(root),
        "database_url_provided": bool(database_url),
        "raw_documents": raw_documents,
        "events": events,
        "model_features": features,
        "acquisition_summary": _read_json(root / "candidate_acquisition_summary.json"),
        "event_extraction_summary": _read_json(root / "event_extraction_summary.json"),
        "raw_document_ingest_summary": _read_json(root / "raw_document_ingest_summary.json"),
    }


def assess_epidemic_event_quality(events: list[Any]) -> EventQualityAssessment:
    summary = build_feature_generation_blocker_summary(events)
    numeric = summary["numeric_target"]
    weeks = set(summary["weekly_alignment"].get("weeks") or [])
    duplicate_count = _duplicate_event_count(events)
    feature_ready_count = _feature_ready_event_count(events)
    single_page_risk = bool(summary["single_page_summary_risk"])
    descriptive_risk = single_page_risk or _descriptive_portal_risk(events)
    blocker_types = list(summary["blocker_types"])
    if descriptive_risk and "requires_structured_dataset" not in blocker_types:
        blocker_types.append("requires_structured_dataset")
    quality_status = _quality_status(summary, descriptive_risk=descriptive_risk)
    warnings = _quality_warnings(quality_status, blocker_types)
    invalid_iso_week = int(summary["weekly_alignment"].get("invalid_iso_week_count", 0))
    missing_iso_week = int(summary["weekly_alignment"].get("missing_iso_week_count", 0))
    return EventQualityAssessment(
        quality_status=quality_status,
        event_count=len(events),
        feature_ready_event_count=feature_ready_count if quality_status == FEATURE_READY else 0,
        disease_known_count=sum(1 for event in events if _value(event, "disease")),
        country_known_count=sum(1 for event in events if _value(event, "country")),
        event_date_count=sum(1 for event in events if _value(event, "event_date")),
        iso_week_count=sum(1 for event in events if _value(event, "iso_week")),
        parseable_iso_week_count=max(0, len(events) - invalid_iso_week - missing_iso_week),
        distinct_week_count=len(weeks),
        numeric_event_count=int(numeric["numeric_event_count"]),
        duplicate_event_count=duplicate_count,
        single_page_summary_risk=single_page_risk,
        descriptive_or_portal_risk=descriptive_risk,
        numeric_field_counts={str(k): int(v) for k, v in (numeric.get("numeric_field_counts") or {}).items()},
        blocker_types=sorted(set(blocker_types)),
        warnings=warnings,
    )


def assess_feature_generation_eligibility(events: list[Any]) -> FeatureEligibilityAssessment:
    summary = build_feature_generation_blocker_summary(events)
    event_quality = assess_epidemic_event_quality(events)
    eligible = bool(summary["eligible"]) and event_quality.quality_status == FEATURE_READY
    blocker_types = sorted(set(event_quality.blocker_types + list(summary["blocker_types"])))
    explanation = (
        "Events satisfy conservative weekly feature requirements."
        if eligible
        else "FeatureBuilder skipped: acquisition events do not yet provide sufficient weekly time-series target labels."
    )
    return FeatureEligibilityAssessment(
        eligible=eligible,
        quality_status=event_quality.quality_status,
        feature_ready_event_count=event_quality.feature_ready_event_count,
        blocker_types=blocker_types,
        weekly_alignment=summary["weekly_alignment"],
        minimum_time_series=summary["minimum_time_series"],
        numeric_target=summary["numeric_target"],
        horizon_label=summary["horizon_label"],
        explanation=explanation,
    )


def diagnose_feature_generation_blockers(events: list[Any], feature_summary: dict[str, Any] | None = None) -> list[FeatureGenerationBlocker]:
    eligibility = assess_feature_generation_eligibility(events)
    blockers: list[FeatureGenerationBlocker] = []
    for blocker_type in eligibility.blocker_types:
        blockers.append(_blocker_for_type(blocker_type, eligibility))
    if feature_summary and int(feature_summary.get("model_features_count") or 0) == 0 and not blockers:
        blockers.append(
            FeatureGenerationBlocker(
                blocker_type="feature_builder_no_rows",
                severity="medium",
                evidence_summary="FeatureBuilder produced zero rows from available events.",
                recommended_action="Inspect target horizon coverage before feature generation.",
            )
        )
    return blockers


def recommend_next_acquisition_action(diagnostics: AcquisitionQualityDiagnosticReport | dict[str, Any]) -> str:
    if isinstance(diagnostics, AcquisitionQualityDiagnosticReport):
        blockers = [item.blocker_type for item in diagnostics.blockers]
        status = diagnostics.event_quality.quality_status
    else:
        blockers = list(diagnostics.get("blocker_types") or [])
        status = str(diagnostics.get("quality_status") or "")
    if "requires_structured_dataset" in blockers or status in {DESCRIPTIVE_ONLY, REQUIRES_STRUCTURED_DATASET}:
        return "Keep the current page as an evidence source and search for measles CSV/table/weekly surveillance/API data."
    if "insufficient_temporal_signal" in blockers or "insufficient_target_signal" in blockers:
        return "Acquire a structured time-series source with at least four ISO weeks and future horizon labels."
    if "insufficient_numeric_signal" in blockers:
        return "Find a source with numeric cases, outbreaks, tests, positivity, or surveillance indicators."
    if status == FEATURE_READY:
        return "Run FeatureBuilder in a local review workflow, then inspect generated features before prediction."
    return "Keep source pending_review and perform manual review before any connector activation."


def write_event_quality_diagnostic_report(
    run_dir: str | Path,
    *,
    database_url: str | None = None,
) -> AcquisitionQualityDiagnosticReport:
    outputs = load_acquisition_outputs(run_dir, database_url=database_url)
    events = outputs["events"]
    raw_documents = outputs["raw_documents"]
    model_features = outputs["model_features"]
    event_quality = assess_epidemic_event_quality(events)
    feature_eligibility = assess_feature_generation_eligibility(events)
    blockers = diagnose_feature_generation_blockers(events, {"model_features_count": len(model_features)})
    report = AcquisitionQualityDiagnosticReport(
        status="ok",
        run_dir=str(run_dir),
        raw_documents_count=len(raw_documents),
        epidemic_events_count=len(events),
        model_features_count=len(model_features),
        event_quality=event_quality,
        feature_eligibility=feature_eligibility,
        blockers=blockers,
        recommended_next_action=recommend_next_acquisition_action(
            {
                "quality_status": event_quality.quality_status,
                "blocker_types": [item.blocker_type for item in blockers],
            }
        ),
    )
    return _write_reports(Path(run_dir), report)


def _write_reports(root: Path, report: AcquisitionQualityDiagnosticReport) -> AcquisitionQualityDiagnosticReport:
    root.mkdir(parents=True, exist_ok=True)
    paths = {
        "event_quality_diagnostics": str(root / "event_quality_diagnostics.json"),
        "event_quality_diagnostics_md": str(root / "event_quality_diagnostics.md"),
        "feature_generation_blockers": str(root / "feature_generation_blockers.json"),
        "feature_generation_blockers_md": str(root / "feature_generation_blockers.md"),
        "feature_alignment_recommendation": str(root / "feature_alignment_recommendation.json"),
        "feature_alignment_recommendation_md": str(root / "feature_alignment_recommendation.md"),
    }
    _write_json(paths["event_quality_diagnostics"], report.to_dict())
    Path(paths["event_quality_diagnostics_md"]).write_text(_diagnostics_markdown(report), encoding="utf-8")
    _write_json(
        paths["feature_generation_blockers"],
        {
            "blocker_types": [item.blocker_type for item in report.blockers],
            "blockers": [item.to_dict() for item in report.blockers],
            "feature_eligibility": report.feature_eligibility.to_dict(),
        },
    )
    Path(paths["feature_generation_blockers_md"]).write_text(_blockers_markdown(report), encoding="utf-8")
    recommendation = {
        "recommended_next_action": report.recommended_next_action,
        "current_page_role": "evidence_source" if report.event_quality.quality_status != FEATURE_READY else "candidate_time_series_source",
        "accept_current_page_as_data_source": report.event_quality.quality_status == FEATURE_READY,
        "requires_structured_dataset_search": report.event_quality.quality_status != FEATURE_READY,
        "limitations": "No model features are fabricated. Feature generation requires structured weekly signals and horizon labels.",
    }
    _write_json(paths["feature_alignment_recommendation"], recommendation)
    Path(paths["feature_alignment_recommendation_md"]).write_text(_recommendation_markdown(recommendation), encoding="utf-8")
    from dataclasses import replace

    return replace(report, report_paths=paths)


def _quality_status(summary: dict[str, Any], *, descriptive_risk: bool) -> str:
    if not summary["weekly_alignment"]["ready"] or not summary["minimum_time_series"]["ready"]:
        return INSUFFICIENT_TEMPORAL_SIGNAL
    if not summary["numeric_target"]["ready"]:
        return INSUFFICIENT_NUMERIC_SIGNAL
    if not summary["horizon_label"]["ready"]:
        return INSUFFICIENT_TARGET_SIGNAL
    if descriptive_risk:
        return REQUIRES_STRUCTURED_DATASET
    if summary["eligible"]:
        return FEATURE_READY
    return EVENT_ONLY


def _feature_ready_event_count(events: list[Any]) -> int:
    summary = build_feature_generation_blocker_summary(events)
    if not summary["eligible"]:
        return 0
    return len(events)


def _duplicate_event_count(events: list[Any]) -> int:
    keys = [
        (
            _value(event, "country"),
            _value(event, "admin1"),
            _value(event, "disease"),
            _value(event, "event_date"),
            _value(event, "iso_week"),
            _value(event, "cases"),
            _value(event, "outbreaks_count"),
        )
        for event in events
    ]
    counts = Counter(keys)
    return sum(count - 1 for count in counts.values() if count > 1)


def _descriptive_portal_risk(events: list[Any]) -> bool:
    if not events:
        return False
    extraction_methods = {str(_value(event, "extraction_method") or "").lower() for event in events}
    return any("generic" in item or "report" in item for item in extraction_methods)


def _blocker_for_type(blocker_type: str, eligibility: FeatureEligibilityAssessment) -> FeatureGenerationBlocker:
    if blocker_type == "insufficient_temporal_signal":
        observed = eligibility.minimum_time_series.get("distinct_week_count", 0)
        required = eligibility.minimum_time_series.get("minimum_required_weeks", 4)
        return FeatureGenerationBlocker(
            blocker_type=blocker_type,
            severity="high",
            evidence_summary=f"Observed {observed} distinct ISO weeks; at least {required} are required for rolling features.",
            recommended_action="Find weekly measles surveillance data, CSV/table, or API records before feature generation.",
        )
    if blocker_type == "insufficient_target_signal":
        return FeatureGenerationBlocker(
            blocker_type=blocker_type,
            severity="high",
            evidence_summary="No horizon has enough future weekly rows to derive target labels.",
            recommended_action="Acquire a longer time series so 7/14/28-day targets can be derived without fabrication.",
        )
    if blocker_type == "insufficient_numeric_signal":
        return FeatureGenerationBlocker(
            blocker_type=blocker_type,
            severity="high",
            evidence_summary="Events do not contain usable numeric surveillance fields.",
            recommended_action="Search for cases, outbreaks, tests, positivity, hospitalization, or surveillance indicators.",
        )
    if blocker_type in {"single_page_summary_signal", "requires_structured_dataset"}:
        return FeatureGenerationBlocker(
            blocker_type=blocker_type,
            severity="medium",
            evidence_summary="The acquisition resembles a single evidence/report page rather than a structured weekly time series.",
            recommended_action="Keep as evidence source; continue discovery for CSV/table/weekly surveillance/API data.",
        )
    return FeatureGenerationBlocker(
        blocker_type=blocker_type,
        severity="medium",
        evidence_summary="Feature generation blocker detected by conservative gate.",
        recommended_action="Manual review required.",
    )


def _quality_warnings(quality_status: str, blockers: list[str]) -> list[str]:
    warnings: list[str] = []
    if quality_status != FEATURE_READY:
        warnings.append("Current acquisition is not feature-ready; no model_features should be fabricated.")
    if "requires_structured_dataset" in blockers:
        warnings.append("Use the current page as evidence unless a structured time-series connector is approved.")
    return warnings


def _diagnostics_markdown(report: AcquisitionQualityDiagnosticReport) -> str:
    return "\n".join(
        [
            "# Acquisition Event Quality Diagnostics",
            "",
            f"- Raw documents: {report.raw_documents_count}",
            f"- Epidemic events: {report.epidemic_events_count}",
            f"- Model features: {report.model_features_count}",
            f"- Event quality status: {report.event_quality.quality_status}",
            f"- Feature-ready events: {report.event_quality.feature_ready_event_count}",
            f"- Distinct ISO weeks: {report.event_quality.distinct_week_count}",
            f"- Numeric events: {report.event_quality.numeric_event_count}",
            f"- Blockers: {', '.join(item.blocker_type for item in report.blockers) or 'none'}",
            "",
            "Raw documents are stored evidence/parsed documents from the controlled acquisition. Epidemic events are extracted observations, but they are not automatically suitable for modeling.",
            "Model features remain zero when weekly time-series coverage or future horizon labels are insufficient.",
            "",
            f"Recommended next action: {report.recommended_next_action}",
        ]
    ) + "\n"


def _blockers_markdown(report: AcquisitionQualityDiagnosticReport) -> str:
    lines = ["# Feature Generation Blockers", ""]
    if not report.blockers:
        lines.append("No blockers detected.")
    for blocker in report.blockers:
        lines.extend(
            [
                f"## {blocker.blocker_type}",
                f"- Severity: {blocker.severity}",
                f"- Evidence: {blocker.evidence_summary}",
                f"- Recommendation: {blocker.recommended_action}",
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _recommendation_markdown(recommendation: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Feature Alignment Recommendation",
            "",
            f"- Current page role: {recommendation['current_page_role']}",
            f"- Accept as data source: {recommendation['accept_current_page_as_data_source']}",
            f"- Requires structured dataset search: {recommendation['requires_structured_dataset_search']}",
            f"- Recommended next action: {recommendation['recommended_next_action']}",
            "",
            recommendation["limitations"],
        ]
    ) + "\n"


def _value(event: Any, field: str) -> Any:
    if isinstance(event, dict):
        return event.get(field)
    return getattr(event, field, None)


def _infer_database_url(run_dir: Path) -> str | None:
    candidate = run_dir.parent / "measles_candidate_acquisition.sqlite"
    if candidate.exists():
        return f"sqlite:///{candidate.as_posix()}"
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(_sanitize(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sanitize(value: Any) -> Any:
    forbidden = {"html", "response_body", "raw_text", "body_text", "metadata_json", "feature_json", "provenance_json", "lesson_json"}
    sensitive = ("password", "token", "secret", "credential", "private", "ssh", "api_key")
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in forbidden or any(part in lowered for part in sensitive):
                continue
            safe[str(key)] = _sanitize(item)
        return safe
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:200]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    lowered = text.lower()
    if any(part in lowered for part in sensitive):
        return "[redacted]"
    return text if len(text) <= 300 else text[:300] + "...[truncated]"


__all__ = [
    "AcquisitionQualityDiagnosticReport",
    "EventQualityAssessment",
    "FeatureEligibilityAssessment",
    "FeatureGenerationBlocker",
    "assess_epidemic_event_quality",
    "assess_feature_generation_eligibility",
    "diagnose_feature_generation_blockers",
    "load_acquisition_outputs",
    "recommend_next_acquisition_action",
    "write_event_quality_diagnostic_report",
]
