from __future__ import annotations

import json
from typing import Any


NUMERIC_FIELDS = (
    "confirmed_cases",
    "confirmed_deaths",
    "suspected_cases",
    "contacts_under_monitoring",
)


def parse_deepseek_table_response(response: str | dict[str, Any]) -> dict[str, Any]:
    payload = response if isinstance(response, dict) else _loads_object(str(response or ""))
    rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
    parsed_rows = []
    parse_warnings = list(payload.get("extraction_warnings") or [])
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            parse_warnings.append(f"row_{index}_not_object")
            continue
        normalized = {
            "row_id": str(row.get("row_id") or f"ROW{index}"),
            "date": _str_or_none(row.get("date")),
            "country": _str_or_none(row.get("country")),
            "region": _str_or_none(row.get("region")),
            "source_id": _str_or_none(row.get("source_id")),
            "evidence_summary": _str_or_none(row.get("evidence_summary")) or "",
            "confidence": _float_or_none(row.get("confidence")),
            "uncertainty_flags": _as_list(row.get("uncertainty_flags")),
            "date_source": _str_or_none(row.get("date_source")),
            "date_anchor_id": _str_or_none(row.get("date_anchor_id")),
            "metric_anchor_id": _str_or_none(row.get("metric_anchor_id")),
            "date_confidence": _str_or_none(row.get("date_confidence")),
            "metric_source": _str_or_none(row.get("metric_source")),
        }
        for field in NUMERIC_FIELDS:
            normalized[field] = parse_int_or_none(row.get(field))
            if row.get(field) not in (None, "") and normalized[field] is None:
                parse_warnings.append(f"{normalized['row_id']}:{field}:invalid_numeric")
        parsed_rows.append(normalized)
    return {
        "rows": parsed_rows,
        "source_notes": payload.get("source_notes") if isinstance(payload.get("source_notes"), list) else [],
        "extraction_warnings": parse_warnings,
    }


def parse_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if text.startswith("+"):
            text = text[1:]
        if text.lstrip("-").isdigit():
            return int(text)
    return None


def _loads_object(text: str) -> dict[str, Any]:
    raw = _extract_json_object(text)
    try:
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {"rows": [], "extraction_warnings": ["json_not_object"]}
    except json.JSONDecodeError:
        return {"rows": [], "extraction_warnings": ["json_parse_error"]}


def _extract_json_object(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end >= start:
        return raw[start : end + 1]
    return raw


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value)]
