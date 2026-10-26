from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any

from live_source_search_orchestrator.types import NormalizedSourceCandidate

REJECT_TERMS = {
    "career",
    "careers",
    "employment",
    "hiring",
    "job",
    "jobs",
    "press release",
    "social media",
}
DEPRIORITIZE_TERMS = {
    "archive",
    "archived",
    "portal",
    "privacy",
    "about",
    "contact",
    "service",
    "grant",
    "funding",
    "training",
    "guidance",
}
BOOST_TERMS = {
    "surveillance",
    "data",
    "dataset",
    "cases",
    "outbreak",
    "weekly report",
    "dashboard",
    "table",
    "api",
    "csv",
    "download data",
    "public health",
    "notifiable disease",
    "official report",
}
STRONG_DATA_TYPES = {
    "official_dataset",
    "csv_dataset",
    "json_api",
    "html_table",
    "surveillance_portal",
}
METADATA_CHECK_TYPES = {"dashboard_data_source", "official_report_page", "surveillance_portal", "html_table"}


@dataclass(frozen=True)
class CandidateQualityAssessment:
    candidate_id: str
    url: str
    domain: str
    title: str
    quality_gate_status: str
    quality_penalties: list[str]
    quality_boosts: list[str]
    rejection_reason: str | None
    original_score: float
    refined_score: float
    recommended_next_step: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def apply_quality_gate(candidates: list[NormalizedSourceCandidate]) -> tuple[list[NormalizedSourceCandidate], list[CandidateQualityAssessment]]:
    gated: list[NormalizedSourceCandidate] = []
    assessments: list[CandidateQualityAssessment] = []
    for candidate in candidates:
        updated, assessment = assess_candidate_quality(candidate)
        gated.append(updated)
        assessments.append(assessment)
    gated.sort(
        key=lambda item: (
            _status_rank(item.quality_gate_status),
            item.refined_score,
            item.total_source_score,
            item.authority_score,
        ),
        reverse=True,
    )
    return gated, assessments


def assess_candidate_quality(candidate: NormalizedSourceCandidate) -> tuple[NormalizedSourceCandidate, CandidateQualityAssessment]:
    text = _candidate_text(candidate)
    penalties = sorted(term for term in REJECT_TERMS | DEPRIORITIZE_TERMS if term in text)
    boosts = sorted(term for term in BOOST_TERMS if term in text)
    status = "pass"
    rejection_reason: str | None = None
    recommended_next_step = "manual_review"
    penalty_score = 0.0
    boost_score = min(0.18, len(boosts) * 0.035)

    if _is_jobs_or_career(candidate, text):
        status = "rejected"
        rejection_reason = "jobs_or_career_page"
        recommended_next_step = "reject_unless_analyst_overrides"
        penalty_score = 0.6
    elif candidate.candidate_source_type == "news_or_media" and not boosts:
        status = "rejected"
        rejection_reason = "news_without_data_signal"
        recommended_next_step = "reject_or_keep_for_context_only"
        penalty_score = 0.35
    elif candidate.candidate_source_type == "low_relevance":
        status = "rejected" if not boosts else "deprioritized"
        rejection_reason = "low_data_likelihood"
        recommended_next_step = "reject_or_deprioritize"
        penalty_score = 0.34 if status == "rejected" else 0.22
    elif _is_archive_only(candidate, text) and len(boosts) < 2:
        status = "deprioritized"
        rejection_reason = "archive_or_generic_portal_low_data_signal"
        recommended_next_step = "keep_pending_low_priority"
        penalty_score = 0.24
    elif candidate.candidate_source_type in METADATA_CHECK_TYPES:
        status = "needs_metadata_check" if candidate.candidate_source_type == "dashboard_data_source" else "pass_with_warning"
        recommended_next_step = "run_controlled_metadata_check"
        penalty_score = 0.04
    elif candidate.candidate_source_type in STRONG_DATA_TYPES or boosts:
        status = "pass"
        recommended_next_step = "manual_review_for_connector_spec"
    else:
        status = "pass_with_warning"
        recommended_next_step = "manual_review_for_relevance"
        penalty_score = 0.08

    refined = _clamp(float(candidate.total_source_score) + boost_score - penalty_score)
    if status == "rejected":
        refined = min(refined, 0.18)
    elif status == "deprioritized":
        refined = min(refined, 0.32)
    elif status == "needs_metadata_check":
        refined = max(refined, min(float(candidate.total_source_score), 0.42))

    quality_penalties = _quality_penalties(candidate, penalties, rejection_reason)
    quality_boosts = _quality_boosts(candidate, boosts)
    warnings = sorted(
        set(
            list(candidate.warnings)
            + ([f"quality_gate:{status}"] if status != "pass" else [])
            + (["metadata_check_required"] if status == "needs_metadata_check" else [])
        )
    )
    updated = replace(
        candidate,
        refined_score=round(refined, 6),
        quality_gate_status=status,
        quality_penalties=quality_penalties,
        quality_boosts=quality_boosts,
        rejection_reason=rejection_reason,
        recommended_action=recommended_next_step,
        warnings=warnings,
    )
    assessment = CandidateQualityAssessment(
        candidate_id=candidate.candidate_id,
        url=candidate.normalized_url,
        domain=candidate.domain,
        title=candidate.normalized_title,
        quality_gate_status=status,
        quality_penalties=quality_penalties,
        quality_boosts=quality_boosts,
        rejection_reason=rejection_reason,
        original_score=float(candidate.total_source_score),
        refined_score=round(refined, 6),
        recommended_next_step=recommended_next_step,
    )
    return updated, assessment


def candidates_for_metadata_check(candidates: list[NormalizedSourceCandidate], *, limit: int = 3) -> list[NormalizedSourceCandidate]:
    eligible = [
        candidate
        for candidate in candidates
        if candidate.quality_gate_status in {"needs_metadata_check", "pass_with_warning", "pass"}
        and candidate.quality_gate_status != "rejected"
    ]
    eligible.sort(
        key=lambda item: (
            item.quality_gate_status == "needs_metadata_check",
            item.refined_score,
            item.data_likelihood_score,
            item.authority_score,
        ),
        reverse=True,
    )
    return eligible[: max(0, min(int(limit), 3))]


def apply_metadata_results(
    candidates: list[NormalizedSourceCandidate],
    metadata_results: list[dict[str, Any]],
) -> list[NormalizedSourceCandidate]:
    by_url = {str(result.get("url") or ""): result for result in metadata_results}
    updated: list[NormalizedSourceCandidate] = []
    for candidate in candidates:
        result = by_url.get(candidate.normalized_url)
        if not result:
            updated.append(candidate)
            continue
        likelihood = _safe_float(result.get("data_source_likelihood"))
        status = str(result.get("status") or "unknown")
        action = str(result.get("recommended_action") or candidate.recommended_action)
        refined = float(candidate.refined_score or candidate.total_source_score)
        warnings = list(candidate.warnings)
        if likelihood >= 0.62:
            refined = min(1.0, refined + 0.08)
        elif status in {"non_data_source", "failed"} or likelihood <= 0.25:
            refined = max(0.0, refined - 0.18)
            warnings.append("metadata_check_low_data_likelihood")
        updated.append(
            replace(
                candidate,
                refined_score=round(refined, 6),
                metadata_check_status=status,
                data_source_likelihood=round(likelihood, 6),
                recommended_action=action,
                warnings=sorted(set(warnings)),
            )
        )
    updated.sort(
        key=lambda item: (
            _status_rank(item.quality_gate_status),
            item.data_source_likelihood or 0.0,
            item.refined_score,
        ),
        reverse=True,
    )
    return updated


def quality_gate_summary(assessments: list[CandidateQualityAssessment]) -> dict[str, int]:
    counts = {"pass": 0, "pass_with_warning": 0, "needs_metadata_check": 0, "deprioritized": 0, "rejected": 0}
    for assessment in assessments:
        counts[assessment.quality_gate_status] = counts.get(assessment.quality_gate_status, 0) + 1
    return counts


def _candidate_text(candidate: NormalizedSourceCandidate) -> str:
    return " ".join(
        [
            candidate.normalized_title,
            candidate.normalized_url,
            candidate.domain,
            candidate.snippet_summary or "",
            " ".join(candidate.data_keywords),
        ]
    ).lower()


def _is_jobs_or_career(candidate: NormalizedSourceCandidate, text: str) -> bool:
    if candidate.domain.startswith("jobs.") or candidate.domain in {"jobs.cdc.gov"}:
        return True
    return any(f"/{term}" in text or f" {term} " in f" {text} " for term in ("career", "careers", "employment", "hiring", "jobs"))


def _is_archive_only(candidate: NormalizedSourceCandidate, text: str) -> bool:
    return "archive" in candidate.domain or "/archive" in text or "archived" in text


def _quality_penalties(candidate: NormalizedSourceCandidate, penalties: list[str], rejection_reason: str | None) -> list[str]:
    values = list(penalties)
    if rejection_reason:
        values.append(rejection_reason)
    if candidate.candidate_source_type in {"low_relevance", "news_or_media", "literature_or_guidance"}:
        values.append(f"type:{candidate.candidate_source_type}")
    return sorted(set(values))


def _quality_boosts(candidate: NormalizedSourceCandidate, boosts: list[str]) -> list[str]:
    values = list(boosts)
    if candidate.authority_domain:
        values.append("authority_domain")
    if candidate.candidate_source_type in STRONG_DATA_TYPES:
        values.append(f"type:{candidate.candidate_source_type}")
    return sorted(set(values))


def _status_rank(status: str) -> int:
    return {
        "pass": 5,
        "needs_metadata_check": 4,
        "pass_with_warning": 3,
        "deprioritized": 1,
        "rejected": 0,
    }.get(str(status), 2)


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


__all__ = [
    "CandidateQualityAssessment",
    "apply_metadata_results",
    "apply_quality_gate",
    "assess_candidate_quality",
    "candidates_for_metadata_check",
    "quality_gate_summary",
]
