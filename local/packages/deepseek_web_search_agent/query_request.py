from __future__ import annotations

from datetime import date
from typing import Iterable

from deepseek_web_search_agent.structured_table_types import (
    DEFAULT_REQUESTED_FIELDS,
    DiseaseTimeQueryRequest,
)


def build_disease_time_query_request(
    *,
    disease: str,
    country_or_region: str,
    date_start: str,
    date_end: str,
    requested_fields: Iterable[str] | None = None,
    max_sources: int = 3,
    allow_web_search: bool = False,
    allow_page_read: bool = False,
    allow_deepseek: bool = False,
    require_human_approval: bool = True,
) -> DiseaseTimeQueryRequest:
    disease_text = str(disease or "").strip()
    region_text = str(country_or_region or "").strip()
    if not disease_text:
        raise ValueError("disease is required.")
    if not region_text:
        raise ValueError("country_or_region is required.")
    start = date.fromisoformat(str(date_start))
    end = date.fromisoformat(str(date_end))
    if start > end:
        raise ValueError("date_start must be before or equal to date_end.")
    fields = [str(item).strip() for item in (requested_fields or DEFAULT_REQUESTED_FIELDS) if str(item).strip()]
    return DiseaseTimeQueryRequest(
        disease=disease_text,
        country_or_region=region_text,
        date_start=start.isoformat(),
        date_end=end.isoformat(),
        requested_fields=fields,
        max_sources=max(1, min(10, int(max_sources))),
        allow_web_search=bool(allow_web_search),
        allow_page_read=bool(allow_page_read),
        allow_deepseek=bool(allow_deepseek),
        require_human_approval=bool(require_human_approval),
    )
