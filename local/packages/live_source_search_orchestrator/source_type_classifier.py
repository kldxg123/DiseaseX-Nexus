from __future__ import annotations

from urllib.parse import urlsplit

from live_source_search_orchestrator.types import SourceTypePrediction

DATA_KEYWORDS = {
    "data",
    "dataset",
    "csv",
    "api",
    "table",
    "dashboard",
    "surveillance",
    "report",
    "situation report",
    "epidemiological update",
    "confirmed cases",
    "deaths",
    "suspected",
    "contacts",
    "weekly",
    "cases",
    "outbreak",
    "download",
    "portal",
}
NEWS_HINTS = ("news", "media", "press", "times", "post", "journal", "tribune")
LOW_QUALITY_HINTS = ("jobs", "career", "careers", "employment", "hiring")


def classify_source_type(*, title: str, url: str, snippet: str | None) -> SourceTypePrediction:
    text = f"{title} {url} {snippet or ''}".lower()
    path = urlsplit(url).path.lower()
    host = (urlsplit(url).hostname or "").lower()
    keywords = sorted(term for term in DATA_KEYWORDS if term in text)
    if host.startswith("jobs.") or any(hint in text for hint in LOW_QUALITY_HINTS):
        return _prediction("low_relevance", "metadata_only", 0.2, keywords, True, "reject_or_deprioritize")
    if path.endswith(".csv") or " csv" in text or "format csv" in text:
        return _prediction("csv_dataset", "csv_dataset", 0.9, keywords, False, "controlled_acquisition_candidate")
    if path.endswith(".json") or " api" in text or "json" in text:
        return _prediction("json_api", "json_api", 0.85, keywords, False, "controlled_acquisition_candidate")
    if "dashboard" in text or "interactive" in text or "portal" in text:
        return _prediction(
            "dashboard_data_source",
            "dashboard_metadata",
            0.72,
            keywords,
            True,
            "metadata_check_required",
        )
    if "table" in text:
        return _prediction("html_table", "html_table", 0.75, keywords, True, "metadata_check_required")
    if "surveillance" in text and ("data" in text or "dataset" in text):
        return _prediction(
            "surveillance_portal",
            "official_dataset",
            0.7,
            keywords,
            True,
            "metadata_check_required",
        )
    if "situation report" in text or "epidemiological update" in text or ("report" in text and any(term in text for term in ("cases", "deaths", "contacts"))):
        return _prediction(
            "official_report_page",
            "generic_html",
            0.78,
            keywords,
            True,
            "metadata_check_required",
        )
    if "report" in text or "weekly" in text:
        return _prediction("official_report_page", "generic_html", 0.65, keywords, True, "metadata_check_required")
    if any(hint in (urlsplit(url).hostname or "").lower() for hint in NEWS_HINTS):
        return _prediction("news_or_media", "news_article", 0.55, keywords, True, "reject_or_deprioritize")
    if "guidance" in text or "literature" in text:
        return _prediction("literature_or_guidance", "metadata_only", 0.5, keywords, True, "reject_or_deprioritize")
    if not keywords:
        return _prediction("low_relevance", "metadata_only", 0.35, [], True, "reject_or_deprioritize")
    return _prediction("unknown", "metadata_only", 0.4, keywords, True, "manual_review")


def _prediction(
    source_type: str,
    parser_strategy: str,
    confidence: float,
    keywords: list[str],
    needs_metadata_check: bool,
    action: str,
) -> SourceTypePrediction:
    return SourceTypePrediction(
        source_type=source_type,
        parser_strategy=parser_strategy,
        confidence=max(0.0, min(1.0, float(confidence))),
        evidence_keywords=keywords[:12],
        needs_metadata_check=needs_metadata_check,
        recommended_next_action=action,
    )


__all__ = ["DATA_KEYWORDS", "classify_source_type"]
