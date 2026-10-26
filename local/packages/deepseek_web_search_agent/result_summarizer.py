from __future__ import annotations

from deepseek_web_search_agent.types import PageReadSummary, RankedSearchResult


def summarize_ranked_result(
    ranked: RankedSearchResult,
    page_summary: PageReadSummary | None = None,
) -> dict:
    result = ranked.result
    text = page_summary.text_summary if page_summary and page_summary.text_summary else result.snippet
    return {
        "candidate_source_summary": _truncate(text, 320),
        "what_data_may_exist": _data_hint(ranked),
        "disease_country_relevance": {
            "disease_relevance": ranked.disease_relevance,
            "country_relevance": ranked.country_relevance,
        },
        "likely_source_type": ranked.source_type,
        "limitations": "Safe summary only; raw page body and full snippets are not stored.",
        "recommended_next_action": _recommended_action(ranked),
    }


def _data_hint(ranked: RankedSearchResult) -> str:
    if ranked.source_type in {"csv_dataset", "json_api", "html_table"}:
        return "Likely structured data source; human review and controlled acquisition are required."
    if ranked.source_type == "dashboard_candidate":
        return "Likely dashboard source; may require connector design and human review."
    if ranked.source_type == "official_report_evidence":
        return "Likely report evidence; may be useful for context but not automatically model-ready."
    return "Unknown data shape; review before acquisition."


def _recommended_action(ranked: RankedSearchResult) -> str:
    if ranked.total_score >= 0.7 and ranked.source_type in {"csv_dataset", "json_api", "html_table"}:
        return "review_for_controlled_acquisition"
    if ranked.total_score >= 0.55:
        return "review_source_metadata"
    return "deprioritize_until_more_evidence"


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
