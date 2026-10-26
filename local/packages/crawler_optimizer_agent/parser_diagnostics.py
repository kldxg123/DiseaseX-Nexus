from __future__ import annotations

import json
from datetime import UTC, datetime

from crawler_optimizer_agent.types import CrawlerFailureCase, OptimizationAction

PARSER_STRATEGIES = {
    "generic_html",
    "article_page",
    "rss_feed",
    "csv_dataset",
    "json_api",
    "html_table",
    "html_card_list",
    "official_dataset",
    "news_article",
}


def infer_parser_strategy_from_document(raw_document) -> str:
    url = str(getattr(raw_document, "normalized_url", None) or getattr(raw_document, "original_url", "")).lower()
    content_type = str(getattr(raw_document, "content_type", "") or "").lower()
    metadata = _metadata(raw_document)
    text = str(getattr(raw_document, "body_text", "") or "")
    lowered_text = text.lower()
    if url.endswith(".csv") or "csv" in content_type:
        return "csv_dataset"
    if "json" in content_type or _looks_like_json(text):
        return "json_api"
    if "table" in lowered_text or metadata.get("table") or metadata.get("table_count"):
        return "html_table"
    if metadata.get("many_links") or metadata.get("cards") or metadata.get("card_count"):
        return "html_card_list"
    if getattr(raw_document, "title", None) and len(text.strip()) >= 120:
        return "article_page"
    if "rss" in content_type or url.endswith(".xml"):
        return "rss_feed"
    return "generic_html"


def recommend_parser_switch(
    failure_case: CrawlerFailureCase,
    raw_document=None,
    source=None,
) -> OptimizationAction | None:
    if failure_case.failure_type not in {"parser_failed", "empty_content", "html_structure_changed"}:
        return None
    current_parser = getattr(source, "parser_type", None) or _metadata(raw_document).get("parser") or "generic_html"
    recommended_parser = infer_parser_strategy_from_document(raw_document) if raw_document is not None else "article_page"
    action_type = "retry_with_different_parser" if recommended_parser == current_parser else "switch_parser"
    risk_level = "medium"
    requires_review = True
    if current_parser == "generic_html" and recommended_parser == "article_page":
        risk_level = "low"
        requires_review = False
    return OptimizationAction(
        action_type=action_type,
        target_type="source",
        target_id=failure_case.source_id,
        reason=f"Parser issue detected; consider {recommended_parser}.",
        confidence=min(0.9, max(0.55, failure_case.confidence)),
        risk_level=risk_level,
        requires_human_review=requires_review,
        metadata={
            "current_parser": current_parser,
            "recommended_parser": recommended_parser,
            "failure_type": failure_case.failure_type,
        },
    )


def detect_html_structure_issue(raw_document) -> CrawlerFailureCase | None:
    metadata = _metadata(raw_document)
    text_length = len(str(getattr(raw_document, "body_text", "") or "").strip())
    signals = [
        bool(metadata.get("html_structure_changed")),
        bool(metadata.get("selector_miss")),
        bool(metadata.get("many_links") and text_length < 120),
    ]
    if not any(signals):
        return None
    return CrawlerFailureCase(
        source_id=str(getattr(raw_document, "source_registry_id", "")),
        raw_document_id=str(getattr(raw_document, "id", "")) if getattr(raw_document, "id", None) is not None else None,
        source_name=getattr(getattr(raw_document, "source_registry", None), "name", None),
        url=getattr(raw_document, "normalized_url", None) or getattr(raw_document, "original_url", None),
        failure_type="html_structure_changed",
        severity="medium",
        confidence=0.74,
        evidence={
            "text_length": text_length,
            "selector_miss": bool(metadata.get("selector_miss")),
            "many_links": bool(metadata.get("many_links")),
        },
        created_at=getattr(raw_document, "fetched_at", None) or datetime.now(UTC),
    )


def _looks_like_json(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        json.loads(stripped)
    except Exception:
        return False
    return True


def _metadata(entity) -> dict:
    metadata = getattr(entity, "metadata_json", None)
    return metadata if isinstance(metadata, dict) else {}
