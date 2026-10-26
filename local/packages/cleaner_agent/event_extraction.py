from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from cleaner_agent.date_normalization import iso_week_from_date, parse_event_date
from cleaner_agent.disease_detection import best_disease_match, detect_disease_mentions
from cleaner_agent.geo_detection import best_country_match, detect_country_mentions, detect_setting
from cleaner_agent.language import detect_language
from cleaner_agent.quality import clamp_quality, compute_event_quality_score
from cleaner_agent.text_cleaning import clean_text
from crawler_agent.country_terms import normalize_country


EXTRACTOR_VERSION = "cleaner_rule_based_v1"
STRUCTURED_EXTRACTOR_VERSION = "cleaner_structured_event_v1"

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass
class ExtractedEvent:
    country: str | None
    admin1: str | None
    admin2: str | None
    disease: str | None
    event_date: date | None
    iso_week: str | None
    cases: int | None
    suspected_cases: int | None
    confirmed_cases: int | None
    deaths: int | None
    hospitalizations: int | None
    tests: int | None
    positivity_rate: float | None
    outbreaks_count: int | None
    setting: str | None
    pathogen_genotype: str | None
    confidence_score: float
    extraction_method: str
    provenance_json: dict


def _first_int(text: str, patterns: list[str]) -> int | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            for group in match.groups():
                if group and group.isdigit():
                    return int(group)
    return None


def _first_float(text: str, patterns: list[str]) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            for group in match.groups():
                if group:
                    try:
                        return float(group)
                    except ValueError:
                        continue
    return None


def extract_numbers_for_epidemic_events(text: str) -> dict:
    text = text or ""
    values = {
        "confirmed_cases": _first_int(
            text,
            [
                r"\b(\d+)\s+confirmed\s+cases?\b",
                r"确诊\s*(\d+)\s*例",
            ],
        ),
        "suspected_cases": _first_int(
            text,
            [
                r"\b(\d+)\s+suspected\s+cases?\b",
                r"疑似\s*(\d+)\s*例",
            ],
        ),
        "deaths": _first_int(
            text,
            [
                r"\b(\d+)\s+deaths?\b",
                r"死亡\s*(\d+)\s*例",
            ],
        ),
        "hospitalizations": _first_int(
            text,
            [
                r"\b(\d+)\s+hospitali[sz]ations?\b",
                r"住院\s*(\d+)\s*例",
            ],
        ),
        "tests": _first_int(
            text,
            [
                r"\b(\d+)\s+tests?\b",
                r"检测\s*(\d+)\s*(?:次|份|人)?",
            ],
        ),
        "outbreaks_count": _first_int(
            text,
            [
                r"\b(\d+)\s+outbreaks?\b",
                r"(\d+)\s*起\s*暴发",
            ],
        ),
        "positivity_rate": _first_float(
            text,
            [
                r"\b(\d+(?:\.\d+)?)%\s+positivity\b",
                r"\bpositivity\s+rate\s+of\s+(\d+(?:\.\d+)?)%",
                r"阳性率\s*(\d+(?:\.\d+)?)%",
            ],
        ),
    }
    values["cases"] = _first_int(
        text,
        [
            r"\b(\d+)\s+cases?\b",
            r"(?:报告|新增|累计|共)?\s*(\d+)\s*例",
        ],
    )
    values["numeric_evidence"] = {
        key: value for key, value in values.items() if key != "numeric_evidence" and value is not None
    }
    return values


def _pathogen_genotype(text: str) -> str | None:
    match = re.search(r"\bG(?:I|II|IV)\.\d+\b", text, flags=re.IGNORECASE)
    return match.group(0).upper() if match else None


def _raw_document_text(raw_document: Any) -> str:
    title = getattr(raw_document, "title", None) or ""
    body = getattr(raw_document, "body_text", None) or ""
    return clean_text(f"{title}\n{body}")


def _metadata(raw_document: Any) -> dict[str, Any]:
    metadata = getattr(raw_document, "metadata_json", None)
    return metadata if isinstance(metadata, dict) else {}


def _date_from_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _reference_date(raw_document: Any) -> date:
    fallback = getattr(raw_document, "published_at", None) or getattr(raw_document, "fetched_at", None)
    parsed = _date_from_value(fallback)
    return parsed or date.today()


def _iso_week_start(iso_week: str | None) -> date | None:
    if not iso_week:
        return None
    match = re.fullmatch(r"(\d{4})-W(\d{1,2})", str(iso_week).strip(), flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
    except ValueError:
        return None


def _week_label_date(value: Any, reference: date) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    iso_start = _iso_week_start(text)
    if iso_start:
        return iso_start
    match = re.fullmatch(r"(\d{1,2})[-/\s]([A-Za-z]{3,9})", text)
    if not match:
        match = re.fullmatch(r"([A-Za-z]{3,9})[-/\s](\d{1,2})", text)
        if not match:
            return None
        month = MONTHS.get(match.group(1).lower())
        day = int(match.group(2))
    else:
        day = int(match.group(1))
        month = MONTHS.get(match.group(2).lower())
    if not month:
        return None
    year = reference.year
    if reference.month < 8 <= month:
        year -= 1
    elif reference.month >= 8 and month < 8:
        year += 1
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        match = re.search(r"\d+", str(value))
        return int(match.group(0)) if match else None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else None


def _structured_event_metadata(raw_document: Any) -> dict[str, Any] | None:
    metadata = _metadata(raw_document)
    structured = metadata.get("structured_event")
    if isinstance(structured, dict):
        return structured
    if metadata.get("source_key") != "cdc_norostat":
        return None
    row_fields = metadata.get("row_fields")
    if not isinstance(row_fields, dict):
        return None
    suspected = _int_or_none(row_fields.get("suspected outbreaks"))
    confirmed = _int_or_none(row_fields.get("confirmed outbreaks"))
    total = _int_or_none(row_fields.get("total outbreaks"))
    outbreaks_count = total
    if outbreaks_count is None and (suspected is not None or confirmed is not None):
        outbreaks_count = int(suspected or 0) + int(confirmed or 0)
    return {
        "country": metadata.get("country_detected") or "United States",
        "admin1": row_fields.get("state"),
        "disease": metadata.get("disease_detected") or "norovirus",
        "week_label": row_fields.get("illness onset week") or row_fields.get("week"),
        "suspected_outbreaks": suspected,
        "confirmed_outbreaks": confirmed,
        "outbreaks_count": outbreaks_count,
        "source_type": metadata.get("source_type") or "official_dataset",
        "source_key": "cdc_norostat",
    }


def _extract_structured_event(
    raw_document: Any,
    *,
    target_country: str | None,
    target_disease: str | None,
) -> ExtractedEvent | None:
    structured = _structured_event_metadata(raw_document)
    if not structured:
        return None
    disease = str(structured.get("disease") or getattr(raw_document, "disease_detected", None) or "").strip().lower()
    if not disease:
        disease = str(target_disease or "").strip().lower()
    if target_disease and disease and disease != target_disease.lower():
        return None
    country_value = structured.get("country") or getattr(raw_document, "country_detected", None) or target_country
    country = normalize_country(country_value) if country_value else None
    if target_country and country and normalize_country(target_country) != country:
        return None
    reference = _reference_date(raw_document)
    event_date = _date_from_value(structured.get("event_date"))
    if event_date is None:
        event_date = _iso_week_start(structured.get("iso_week"))
    if event_date is None:
        event_date = _week_label_date(structured.get("week_label"), reference)
    suspected_outbreaks = _int_or_none(structured.get("suspected_outbreaks"))
    confirmed_outbreaks = _int_or_none(structured.get("confirmed_outbreaks"))
    outbreaks_count = _int_or_none(structured.get("outbreaks_count"))
    if outbreaks_count is None and (suspected_outbreaks is not None or confirmed_outbreaks is not None):
        outbreaks_count = int(suspected_outbreaks or 0) + int(confirmed_outbreaks or 0)
    cases = _int_or_none(structured.get("cases"))
    suspected_cases = _int_or_none(structured.get("suspected_cases"))
    confirmed_cases = _int_or_none(structured.get("confirmed_cases"))
    deaths = _int_or_none(structured.get("deaths"))
    hospitalizations = _int_or_none(structured.get("hospitalizations"))
    tests = _int_or_none(structured.get("tests"))
    positivity_rate = _float_or_none(structured.get("positivity_rate"))
    has_numeric_signal = any(
        value is not None
        for value in (
            outbreaks_count,
            cases,
            suspected_cases,
            confirmed_cases,
            deaths,
            hospitalizations,
            tests,
            positivity_rate,
        )
    )
    confidence = 0.88 if country and disease and event_date else 0.7
    if not has_numeric_signal:
        confidence *= 0.85
    raw_document_id = getattr(raw_document, "id", None)
    return ExtractedEvent(
        country=country,
        admin1=str(structured.get("admin1")).strip() if structured.get("admin1") else None,
        admin2=None,
        disease=disease or None,
        event_date=event_date,
        iso_week=iso_week_from_date(event_date),
        cases=cases,
        suspected_cases=suspected_cases,
        confirmed_cases=confirmed_cases,
        deaths=deaths,
        hospitalizations=hospitalizations,
        tests=tests,
        positivity_rate=positivity_rate,
        outbreaks_count=outbreaks_count,
        setting=None,
        pathogen_genotype=None,
        confidence_score=clamp_quality(confidence),
        extraction_method="structured_event_v1",
        provenance_json={
            "raw_document_id": raw_document_id,
            "structured_source": True,
            "source_key": structured.get("source_key") or _metadata(raw_document).get("source_key"),
            "source_type": structured.get("source_type") or _metadata(raw_document).get("source_type"),
            "row_number": _metadata(raw_document).get("row_number"),
            "parse_mode": _metadata(raw_document).get("parse_mode"),
            "numeric_evidence": {
                key: value
                for key, value in {
                    "cases": cases,
                    "suspected_cases": suspected_cases,
                    "confirmed_cases": confirmed_cases,
                    "deaths": deaths,
                    "hospitalizations": hospitalizations,
                    "tests": tests,
                    "positivity_rate": positivity_rate,
                    "outbreaks_count": outbreaks_count,
                }.items()
                if value is not None
            },
            "extractor_version": STRUCTURED_EXTRACTOR_VERSION,
        },
    )


def extract_event_from_raw_document(
    raw_document,
    *,
    target_country: str | None = None,
    target_disease: str | None = None,
) -> ExtractedEvent | None:
    structured_event = _extract_structured_event(
        raw_document,
        target_country=target_country,
        target_disease=target_disease,
    )
    if structured_event is not None:
        return structured_event

    text = _raw_document_text(raw_document)
    if not text:
        return None

    disease_match = best_disease_match(text, disease=target_disease)
    disease_fallback = getattr(raw_document, "disease_detected", None) or target_disease
    if disease_match:
        disease = disease_match["disease"]
        disease_confidence = float(disease_match["confidence"])
    elif disease_fallback:
        disease = str(disease_fallback).strip().lower()
        disease_confidence = 0.55
    else:
        return None

    country_match = best_country_match(text, country=target_country)
    country_fallback = getattr(raw_document, "country_detected", None) or target_country
    country = None
    if country_match:
        country = country_match["country"]
    elif country_fallback:
        country = normalize_country(country_fallback)

    fallback_date = getattr(raw_document, "published_at", None) or getattr(raw_document, "fetched_at", None)
    event_date = parse_event_date(text, fallback=fallback_date)
    numeric_values = extract_numbers_for_epidemic_events(text)
    numeric_evidence = numeric_values.pop("numeric_evidence")
    has_numeric_signal = bool(numeric_evidence)
    setting = detect_setting(text)
    source = getattr(raw_document, "source_registry", None)
    source_reliability = getattr(source, "reliability_score", None)
    confidence = compute_event_quality_score(
        has_disease=bool(disease),
        has_country=bool(country),
        has_date=bool(event_date),
        has_numeric_signal=has_numeric_signal,
        source_reliability=source_reliability,
        extraction_confidence=disease_confidence,
    )
    if not has_numeric_signal:
        confidence *= 0.65

    disease_mentions = detect_disease_mentions(text, disease=target_disease)
    country_mentions = detect_country_mentions(text, country=target_country)
    raw_document_id = getattr(raw_document, "id", None)

    return ExtractedEvent(
        country=country,
        admin1=None,
        admin2=None,
        disease=disease,
        event_date=event_date,
        iso_week=iso_week_from_date(event_date),
        cases=numeric_values["cases"],
        suspected_cases=numeric_values["suspected_cases"],
        confirmed_cases=numeric_values["confirmed_cases"],
        deaths=numeric_values["deaths"],
        hospitalizations=numeric_values["hospitalizations"],
        tests=numeric_values["tests"],
        positivity_rate=numeric_values["positivity_rate"],
        outbreaks_count=numeric_values["outbreaks_count"],
        setting=setting,
        pathogen_genotype=_pathogen_genotype(text),
        confidence_score=clamp_quality(confidence),
        extraction_method="rule_based_v1",
        provenance_json={
            "raw_document_id": raw_document_id,
            "matched_disease_terms": [item["matched_term"] for item in disease_mentions[:5]],
            "matched_country_terms": [item["matched_term"] for item in country_mentions[:5]],
            "numeric_evidence": numeric_evidence,
            "language": detect_language(text),
            "extractor_version": EXTRACTOR_VERSION,
        },
    )
