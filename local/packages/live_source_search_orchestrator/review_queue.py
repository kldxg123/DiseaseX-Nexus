from __future__ import annotations

from live_source_search_orchestrator.types import NormalizedSourceCandidate, ReviewQueueItem


def build_review_queue(candidates: list[NormalizedSourceCandidate], *, limit: int = 25) -> list[ReviewQueueItem]:
    items: list[ReviewQueueItem] = []
    for candidate in candidates[:limit]:
        items.append(
            ReviewQueueItem(
                candidate_id=candidate.candidate_id,
                title=candidate.normalized_title,
                domain=candidate.domain,
                source_score=candidate.total_source_score,
                source_type=candidate.candidate_source_type,
                parser_strategy=candidate.parser_strategy,
                evidence_summary=candidate.evidence_summary,
                recommended_action=candidate.recommended_action,
                risk_flags=_risk_flags(candidate),
                requires_human_review=True,
                suggested_next_step=_suggested_next_step(candidate),
                registry_status="pending_review",
                quality_gate_status=candidate.quality_gate_status,
                refined_score=candidate.refined_score or candidate.total_source_score,
                metadata_check_status=candidate.metadata_check_status,
                data_source_likelihood=candidate.data_source_likelihood,
                rejection_reason=candidate.rejection_reason,
                url=candidate.normalized_url,
            )
        )
    return items


def review_queue_to_markdown(items: list[ReviewQueueItem]) -> str:
    lines = [
        "# Live Source Search Review Queue",
        "",
        "Candidates require human review before source activation, live acquisition, or connector implementation.",
        "",
        "| id | title | domain | score | quality | metadata | type | parser | action |",
        "|---|---|---|---:|---|---|---|---|---|",
    ]
    for item in items:
        lines.append(
            f"| `{item.candidate_id}` | {item.title[:80]} | {item.domain} | {item.refined_score:.3f} | "
            f"{item.quality_gate_status} | {item.metadata_check_status or ''} | "
            f"{item.source_type} | {item.parser_strategy} | {item.recommended_action} |"
        )
    return "\n".join(lines) + "\n"


def _risk_flags(candidate: NormalizedSourceCandidate) -> list[str]:
    flags = list(candidate.warnings)
    if candidate.compliance_risk_score >= 0.15:
        flags.append("compliance_review_required")
    if candidate.candidate_source_type in {"dashboard_data_source", "official_report_page"}:
        flags.append("metadata_check_required")
    return sorted(set(flags))


def _suggested_next_step(candidate: NormalizedSourceCandidate) -> str:
    if candidate.recommended_action == "controlled_acquisition_candidate":
        return "Review terms and robots before a separate controlled metadata/acquisition step."
    if candidate.quality_gate_status == "rejected":
        return "Reject or keep only if an analyst overrides the quality gate."
    if candidate.quality_gate_status == "deprioritized":
        return "Keep pending at low priority unless metadata review confirms source value."
    if candidate.recommended_action == "metadata_check_required":
        return "Run a separately approved metadata check before connector work."
    if candidate.recommended_action == "run_controlled_metadata_check":
        return "Run controlled metadata check before connector work."
    if candidate.recommended_action == "reject_or_deprioritize":
        return "Keep pending or reject unless an analyst confirms data-source value."
    return "Manual review required."


__all__ = ["build_review_queue", "review_queue_to_markdown"]
