from __future__ import annotations

import hashlib
import json
from typing import Any

from deepseek_web_search_agent.structured_table_types import DiseaseTimeQueryRequest, SourceEvidenceItem


def build_structured_event_table_prompt(
    query_request: DiseaseTimeQueryRequest,
    source_pack: list[SourceEvidenceItem],
    contextual_evidence_pack: dict[str, Any] | None = None,
) -> str:
    payload = {
        "task": "Extract candidate infectious disease event rows as strict JSON.",
        "query": query_request.to_dict(),
        "sources": [source.to_dict() for source in source_pack],
        "output_schema": {
            "rows": [
                {
                    "date": "YYYY-MM-DD or null",
                    "date_source": "explicit_text|iso_week|report_date_fallback|heading_date|section_date|table_caption_date|null",
                    "country": "string or null",
                    "region": "string or null",
                    "confirmed_cases": "integer or null",
                    "confirmed_deaths": "integer or null",
                    "suspected_cases": "integer or null",
                    "contacts_under_monitoring": "integer or null",
                    "source_id": "SRC1",
                    "date_anchor_id": "source-local anchor id or null",
                    "metric_anchor_id": "source-local anchor id or null",
                    "evidence_summary": "short source-grounded summary",
                    "confidence": 0.0,
                    "uncertainty_flags": [],
                    "requires_human_review": True,
                }
            ],
            "source_notes": [],
            "extraction_warnings": [],
        },
        "rules": [
            "Use only evidence contained in source_pack.",
            "Each source includes material_strength and extraction_readiness. Treat search_result_only as usually insufficient for dated numeric rows.",
            "Prefer table_preview, csv_preview, json_preview, or connector_document material over page summaries.",
            "Do not guess dates, countries, regions, counts, deaths, suspected cases, or contacts.",
            "Do not infer counts from page topic, title, URL, or search snippet alone.",
            "Do not infer dates from page topic, title, URL, or search snippet alone.",
            "Use null for unknown fields; do not fabricate missing values.",
            "You may link a date_anchor and a metric_anchor from the same source if they are in the same section or nearby context window.",
            "Do not link evidence across different sources.",
            "Do not create a row without a clear date or a declared date_source.",
            "If using report publication date as a fallback, set date_source=report_date_fallback, confidence<=0.5, include uncertainty_flags, and requires_human_review=true.",
            "Do not fill a numeric field unless the evidence explicitly supports it.",
            "If evidence explicitly says no cases reported, zero is allowed and must be grounded.",
            "Every row must include a source_id from source_pack.",
            "Every date must be within date_start and date_end.",
            "If no source provides both a date/week and an explicit numeric metric, return rows=[] and include extraction_warnings explaining insufficient_source_material or no_dated_numeric_signal.",
            "Return one valid JSON object only; no Markdown and no explanatory text outside JSON.",
        ],
    }
    if contextual_evidence_pack:
        payload["contextual_evidence_pack"] = contextual_evidence_pack
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()


def prompt_summary(
    query_request: DiseaseTimeQueryRequest,
    source_pack: list[SourceEvidenceItem],
    contextual_evidence_pack: dict[str, Any] | None = None,
) -> dict[str, Any]:
    counts = contextual_evidence_pack.get("counts", {}) if isinstance(contextual_evidence_pack, dict) else {}
    return {
        "task": "structured_event_table_extraction",
        "disease": query_request.disease,
        "country_or_region": query_request.country_or_region,
        "date_start": query_request.date_start,
        "date_end": query_request.date_end,
        "source_count": len(source_pack),
        "material_strength_counts": _counts(source.material_strength for source in source_pack),
        "extraction_readiness_counts": _counts(source.extraction_readiness for source in source_pack),
        "date_anchor_count": counts.get("date_anchor_count", 0),
        "metric_anchor_count": counts.get("metric_anchor_count", 0),
        "context_linked_candidate_count": counts.get("context_linked_candidate_count", 0),
        "output": "strict_json_rows_for_human_review",
    }


def _counts(values) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        key = str(value or "unknown")
        result[key] = result.get(key, 0) + 1
    return result
