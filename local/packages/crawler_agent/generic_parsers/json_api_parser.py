from __future__ import annotations

import json
from typing import Any

from crawler_agent.generic_parsers.types import (
    ParsedRecord,
    ParserResult,
    build_structured_text,
    has_numeric_signal,
    safe_record_metadata,
    standardize_fields,
)
from crawler_agent.url_utils import normalize_url


def parse_json_api_response(
    content: str,
    *,
    source_name: str,
    url: str,
    country: str | None = None,
    disease: str | None = None,
    records_path: str | None = None,
    field_mapping: dict[str, str] | None = None,
) -> ParserResult:
    try:
        payload = json.loads(content or "")
    except json.JSONDecodeError as exc:
        return ParserResult("json_api", [], [f"JSON parse failed: {exc.msg}."], skipped_rows=0)

    records_payload, resolved_path = _extract_records(payload, records_path=records_path)
    if records_payload is None:
        return ParserResult("json_api", [], ["No JSON records list found."], skipped_rows=0)

    warnings: list[str] = []
    records: list[ParsedRecord] = []
    skipped_rows = 0
    for index, item in enumerate(records_payload, start=1):
        if not isinstance(item, dict):
            skipped_rows += 1
            warnings.append(f"JSON record {index} is not an object and was skipped.")
            continue
        structured = standardize_fields(
            item,
            country=country,
            disease=disease,
            source_type="official_dataset",
            field_mapping=field_mapping,
        )
        if "iso_week" not in structured and "week_label" not in structured:
            warnings.append(f"JSON record {index} has no recognized week field.")
        if not has_numeric_signal(structured):
            warnings.append(f"JSON record {index} has no recognized numeric surveillance field.")
        records.append(
            ParsedRecord(
                source_name=source_name,
                source_type="official_dataset",
                parser_strategy="json_api",
                url=normalize_url(f"?row={index}&parser=json_api", base_url=url),
                title=_record_title(source_name, structured, index),
                text=build_structured_text(source_name, structured),
                country_detected=country,
                disease_detected=disease,
                language="en",
                structured_fields=structured,
                metadata=safe_record_metadata(
                    item,
                    row_number=index,
                    records_path=resolved_path,
                    content_kind="json_api",
                ),
            )
        )
    return ParserResult("json_api", records, warnings, skipped_rows=skipped_rows)


def _extract_records(payload: Any, *, records_path: str | None) -> tuple[list[Any] | None, str | None]:
    if records_path:
        current = payload
        for part in records_path.split("."):
            if not isinstance(current, dict):
                return None, records_path
            current = current.get(part)
        return current if isinstance(current, list) else None, records_path
    if isinstance(payload, list):
        return payload, "$"
    if not isinstance(payload, dict):
        return None, None
    for key in ("records", "data", "results", "series", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return value, key
        if isinstance(value, dict):
            nested, path = _extract_records(value, records_path=None)
            if nested is not None:
                return nested, f"{key}.{path}"
    return None, None


def _record_title(source_name: str, structured: dict, index: int) -> str:
    week = structured.get("iso_week") or structured.get("week_label") or f"record {index}"
    region = structured.get("admin1") or structured.get("country") or "all regions"
    disease = structured.get("disease") or "surveillance"
    return f"{source_name} {disease} JSON record - {region} - {week}"
