from __future__ import annotations

from typing import Any

from live_source_search_orchestrator.types import NormalizedSourceCandidate


def recommend_connector_specs(candidates: list[NormalizedSourceCandidate], *, min_score: float = 0.35) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for candidate in candidates:
        score = float(candidate.refined_score or candidate.total_source_score)
        if candidate.quality_gate_status == "rejected":
            continue
        if score < min_score:
            continue
        action = _action(candidate)
        specs.append(
            {
                "connector_name": _connector_name(candidate.source_name),
                "source_name": candidate.source_name,
                "base_url": candidate.normalized_url,
                "domain": candidate.domain,
                "source_type": candidate.candidate_source_type,
                "parser_strategy": candidate.parser_strategy,
                "quality_gate_status": candidate.quality_gate_status,
                "refined_score": round(score, 6),
                "sample_url_count": 1,
                "requires_human_review": True,
                "auto_enable": False,
                "registry_status": "pending_review",
                "live_acquisition_allowed": False,
                "test_fixture_required": True,
                "recommended_action": action,
            }
        )
    return specs


def attach_connector_specs(
    candidates: list[NormalizedSourceCandidate],
    specs: list[dict[str, Any]],
) -> list[NormalizedSourceCandidate]:
    from dataclasses import replace

    by_url = {spec["base_url"]: spec for spec in specs}
    return [replace(candidate, connector_spec=by_url.get(candidate.normalized_url)) for candidate in candidates]


def _action(candidate: NormalizedSourceCandidate) -> str:
    if candidate.candidate_source_type in {"dashboard_data_source", "surveillance_portal", "official_report_page"}:
        return "metadata_check_required"
    if candidate.candidate_source_type in {"csv_dataset", "json_api", "html_table", "official_dataset"}:
        return "controlled_acquisition_candidate"
    if candidate.candidate_source_type in {"news_or_media", "low_relevance", "literature_or_guidance"}:
        return "reject_or_deprioritize"
    return "manual_review"


def _connector_name(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "candidate"))
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned.strip("_") or "candidate_connector"


__all__ = ["attach_connector_specs", "recommend_connector_specs"]
