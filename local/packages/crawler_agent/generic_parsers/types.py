from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class ParsedRecord:
    source_name: str
    source_type: str
    parser_strategy: str
    url: str
    title: str | None
    text: str
    country_detected: str | None
    disease_detected: str | None
    language: str | None
    structured_fields: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParserResult:
    parser_strategy: str
    records: list[ParsedRecord]
    warnings: list[str]
    skipped_rows: int = 0


DEFAULT_FIELD_ALIASES = {
    "week": "iso_week",
    "iso week": "iso_week",
    "epiweek": "iso_week",
    "epi week": "iso_week",
    "mmwr week": "iso_week",
    "week label": "week_label",
    "state": "admin1",
    "region": "admin1",
    "jurisdiction": "admin1",
    "area": "admin1",
    "cases": "cases",
    "case count": "cases",
    "illnesses": "cases",
    "ili visits": "cases",
    "visits": "cases",
    "outbreaks": "outbreaks_count",
    "outbreak count": "outbreaks_count",
    "total outbreaks": "outbreaks_count",
    "suspected outbreaks": "suspected_outbreaks",
    "confirmed outbreaks": "confirmed_outbreaks",
    "suspected cases": "suspected_cases",
    "confirmed cases": "confirmed_cases",
    "deaths": "deaths",
    "hospitalizations": "hospitalizations",
    "hospitalisations": "hospitalizations",
    "tests": "tests",
    "total tests": "tests",
    "specimens tested": "tests",
    "positive tests": "positive_tests",
    "positive specimens": "positive_tests",
    "positives": "positive_tests",
    "ili": "positivity_rate",
    "ili percent": "positivity_rate",
    "weighted ili": "positivity_rate",
    "percent ili": "positivity_rate",
    "% ili": "positivity_rate",
    "percent positive": "positivity_rate",
    "percent positivity": "positivity_rate",
    "positivity": "positivity_rate",
    "positivity rate": "positivity_rate",
}

NUMERIC_INT_FIELDS = {
    "cases",
    "outbreaks_count",
    "suspected_outbreaks",
    "confirmed_outbreaks",
    "suspected_cases",
    "confirmed_cases",
    "deaths",
    "hospitalizations",
    "tests",
    "positive_tests",
}
NUMERIC_FLOAT_FIELDS = {"positivity_rate", "surveillance_indicator"}
SAFE_METADATA_KEYS = {
    "row_number",
    "field_names",
    "field_count",
    "records_path",
    "source_type",
    "table_number",
    "paragraph_count",
    "content_kind",
}


def normalize_header(value: Any) -> str:
    text = re.sub(r"[^a-z0-9%]+", " ", str(value or "").strip().lower())
    return " ".join(text.split())


def squash(value: Any) -> str:
    return " ".join(str(value or "").split())


def parse_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    match = re.search(r"-?\d+", str(value).replace(",", ""))
    return int(match.group(0)) if match else None


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def parse_iso_week(value: Any) -> tuple[str | None, str | None]:
    text = squash(value)
    if not text:
        return None, None
    match = re.fullmatch(r"(\d{4})-?W?(\d{1,2})", text, flags=re.IGNORECASE)
    if not match:
        return text, None
    year, week = int(match.group(1)), int(match.group(2))
    try:
        parsed = date.fromisocalendar(year, week, 1)
    except ValueError:
        return text, None
    iso_year, iso_week, _ = parsed.isocalendar()
    return f"{iso_year}-W{iso_week:02d}", parsed.isoformat()


def build_field_mapping(field_mapping: dict[str, str] | None = None) -> dict[str, str]:
    mapping = dict(DEFAULT_FIELD_ALIASES)
    for source, target in (field_mapping or {}).items():
        mapping[normalize_header(source)] = target
    return mapping


def normalize_record_values(record: dict[str, Any]) -> dict[str, str]:
    return {
        normalize_header(key): squash(value)
        for key, value in record.items()
        if key is not None and normalize_header(key)
    }


def standardize_fields(
    record: dict[str, Any],
    *,
    country: str | None,
    disease: str | None,
    source_type: str,
    field_mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    normalized = normalize_record_values(record)
    mapping = build_field_mapping(field_mapping)
    structured: dict[str, Any] = {
        "country": country,
        "disease": disease.lower() if disease else None,
        "source_type": source_type,
    }
    for key, value in normalized.items():
        target = mapping.get(key)
        if not target:
            continue
        if target == "iso_week":
            iso_week, event_date = parse_iso_week(value)
            structured["iso_week"] = iso_week
            structured["week_label"] = value
            if event_date:
                structured["event_date"] = event_date
        elif target == "week_label":
            structured["week_label"] = value
        elif target == "admin1":
            structured["admin1"] = None if value.lower() in {"national", "us", "u s", "united states"} else value
        elif target in NUMERIC_INT_FIELDS:
            parsed_int = parse_int(value)
            structured[target] = parsed_int
            if target == "positive_tests" and structured.get("confirmed_cases") is None:
                structured["confirmed_cases"] = parsed_int
        elif target in NUMERIC_FLOAT_FIELDS:
            structured[target] = parse_float(value)
        else:
            structured[target] = value
    return {key: value for key, value in structured.items() if value is not None}


def has_numeric_signal(structured: dict[str, Any]) -> bool:
    return any(
        structured.get(key) is not None
        for key in (
            "cases",
            "outbreaks_count",
            "suspected_outbreaks",
            "confirmed_outbreaks",
            "suspected_cases",
            "confirmed_cases",
            "deaths",
            "hospitalizations",
            "tests",
            "positive_tests",
            "positivity_rate",
        )
    )


def safe_record_metadata(record: dict[str, Any], *, row_number: int, **extra: Any) -> dict[str, Any]:
    normalized = normalize_record_values(record)
    metadata: dict[str, Any] = {
        "row_number": row_number,
        "field_names": sorted(normalized.keys()),
        "field_count": len(normalized),
    }
    for key, value in extra.items():
        if key in SAFE_METADATA_KEYS and value is not None:
            metadata[key] = value
    return metadata


def build_structured_text(source_name: str, structured: dict[str, Any]) -> str:
    parts = [
        f"{source_name} structured surveillance record.",
        f"Country: {structured.get('country')}." if structured.get("country") else "",
        f"Disease: {structured.get('disease')}." if structured.get("disease") else "",
        f"Region: {structured.get('admin1')}." if structured.get("admin1") else "",
        f"Week: {structured.get('iso_week') or structured.get('week_label')}."
        if structured.get("iso_week") or structured.get("week_label")
        else "",
    ]
    for key, label in (
        ("cases", "Cases"),
        ("confirmed_cases", "Confirmed cases"),
        ("suspected_cases", "Suspected cases"),
        ("outbreaks_count", "Outbreaks"),
        ("tests", "Tests"),
        ("positive_tests", "Positive tests"),
        ("positivity_rate", "Positivity rate"),
        ("hospitalizations", "Hospitalizations"),
        ("deaths", "Deaths"),
    ):
        if structured.get(key) is not None:
            parts.append(f"{label}: {structured[key]}.")
    return squash(" ".join(part for part in parts if part))
