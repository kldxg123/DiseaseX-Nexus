from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from crawler_agent.country_terms import get_country_query_terms
from crawler_agent.disease_terms import expand_disease_aliases
from crawler_optimizer_agent.types import CrawlerFailureCase, clamp_confidence

ALLOWED_CONTENT_TYPE_MARKERS = ("html", "json", "csv", "text")


def classify_source_failure(source) -> list[CrawlerFailureCase]:
    cases: list[CrawlerFailureCase] = []
    metadata = _metadata(source)
    failure_reason = str(metadata.get("last_failure_reason") or "").lower()
    failures = int(getattr(source, "consecutive_failures", 0) or 0)
    created_at = getattr(source, "last_failed_at", None) or _now()

    if getattr(source, "robots_allowed", None) is False:
        cases.append(
            _source_case(
                source,
                failure_type="robots_denied",
                severity="high",
                confidence=0.95,
                evidence={"robots_allowed": False},
                created_at=created_at,
            )
        )
    if failures >= 15:
        cases.append(
            _source_case(
                source,
                failure_type="broken_link" if _looks_broken(failure_reason) else "stale_source",
                severity="critical",
                confidence=0.9,
                evidence={"consecutive_failures": failures, "status": getattr(source, "status", None)},
                created_at=created_at,
            )
        )
    elif failures >= 5:
        cases.append(
            _source_case(
                source,
                failure_type="http_error" if "http" in failure_reason else "stale_source",
                severity="high",
                confidence=0.78,
                evidence={"consecutive_failures": failures, "status": getattr(source, "status", None)},
                created_at=created_at,
            )
        )
    if "timeout" in failure_reason:
        cases.append(
            _source_case(
                source,
                failure_type="fetch_timeout",
                severity="medium",
                confidence=0.8,
                evidence={"failure_reason_category": "timeout"},
                created_at=created_at,
            )
        )
    if _looks_broken(failure_reason):
        cases.append(
            _source_case(
                source,
                failure_type="broken_link",
                severity="high",
                confidence=0.82,
                evidence={"failure_reason_category": "not_found"},
                created_at=created_at,
            )
        )
    if "429" in failure_reason or "rate" in failure_reason:
        cases.append(
            _source_case(
                source,
                failure_type="rate_limited",
                severity="medium",
                confidence=0.86,
                evidence={"failure_reason_category": "rate_limited"},
                created_at=created_at,
            )
        )
    return cases


def classify_raw_document_failure(
    raw_document,
    *,
    target_country: str | None = None,
    target_disease: str | None = None,
) -> list[CrawlerFailureCase]:
    cases: list[CrawlerFailureCase] = []
    metadata = _metadata(raw_document)
    text = getattr(raw_document, "body_text", "") or ""
    text_length = len(text.strip())
    title = getattr(raw_document, "title", None)
    status_code = _status_code(raw_document)
    content_type = str(getattr(raw_document, "content_type", None) or metadata.get("content_type") or "")
    created_at = getattr(raw_document, "fetched_at", None) or _now()
    structured_official_dataset = _is_structured_official_dataset(raw_document, metadata)

    if status_code >= 400:
        cases.append(
            _document_case(
                raw_document,
                "broken_link" if status_code == 404 else "http_error",
                "high" if status_code == 404 else "medium",
                0.9,
                {"status_code": status_code},
                created_at,
            )
        )
    if content_type and not any(marker in content_type.lower() for marker in ALLOWED_CONTENT_TYPE_MARKERS):
        cases.append(
            _document_case(
                raw_document,
                "unsupported_content_type",
                "medium",
                0.82,
                {"content_type_family": content_type.split(";")[0][:80]},
                created_at,
            )
        )
    if text_length < 30 and not structured_official_dataset:
        cases.append(
            _document_case(
                raw_document,
                "empty_content",
                "high",
                0.88,
                {"text_length": text_length},
                created_at,
            )
        )
    extraction_status = str(getattr(raw_document, "extraction_status", "") or "").lower()
    if extraction_status in {"failed", "error"}:
        cases.append(
            _document_case(
                raw_document,
                "parser_failed",
                "high",
                0.9,
                {"extraction_status": extraction_status, "text_length": text_length},
                created_at,
            )
        )
    if not title and text_length < 120:
        cases.append(
            _document_case(
                raw_document,
                "parser_failed",
                "medium",
                0.72,
                {"title_present": False, "text_length": text_length},
                created_at,
            )
        )
    if target_disease:
        aliases = [alias.lower() for alias in expand_disease_aliases(target_disease)]
        matches = sum(1 for alias in aliases if alias and alias in text.lower())
        if matches == 0 and not _declares_target(raw_document, metadata, target_disease, kind="disease"):
            cases.append(
                _document_case(
                    raw_document,
                    "low_relevance",
                    "medium",
                    0.74,
                    {"target": "disease", "matched_terms_count": 0},
                    created_at,
                )
            )
    if target_country:
        terms = [term.lower() for term in get_country_query_terms(target_country)]
        if terms:
            matches = sum(1 for term in terms if term and term in text.lower())
            if matches == 0 and not _declares_target(raw_document, metadata, target_country, kind="country"):
                cases.append(
                    _document_case(
                        raw_document,
                        "low_relevance",
                        "low",
                        0.55,
                        {"target": "country", "matched_terms_count": 0},
                        created_at,
                    )
                )
    return cases


def analyze_duplicate_documents(raw_documents: list) -> list[CrawlerFailureCase]:
    by_hash: dict[str, list[Any]] = defaultdict(list)
    for document in raw_documents:
        content_hash = getattr(document, "content_hash", None)
        if content_hash:
            by_hash[str(content_hash)].append(document)
    cases: list[CrawlerFailureCase] = []
    for content_hash, documents in by_hash.items():
        if len(documents) <= 1:
            continue
        for document in documents:
            cases.append(
                _document_case(
                    document,
                    "duplicate_content",
                    "low",
                    0.95,
                    {"duplicate_count": len(documents), "content_hash_prefix": content_hash[:12]},
                    getattr(document, "fetched_at", None) or _now(),
                )
            )
    return cases


def summarize_failure_cases(cases: list[CrawlerFailureCase]) -> dict:
    by_type = Counter(case.failure_type for case in cases)
    by_severity = Counter(case.severity for case in cases)
    return {
        "total": len(cases),
        "by_type": dict(sorted(by_type.items())),
        "by_severity": dict(sorted(by_severity.items())),
        "high_or_critical": sum(1 for case in cases if case.severity in {"high", "critical"}),
    }


def _source_case(source, failure_type, severity, confidence, evidence, created_at) -> CrawlerFailureCase:
    return CrawlerFailureCase(
        source_id=str(getattr(source, "id", "")) if getattr(source, "id", None) is not None else None,
        raw_document_id=None,
        source_name=getattr(source, "name", None),
        url=getattr(source, "base_url", None),
        failure_type=failure_type,
        severity=severity,
        confidence=clamp_confidence(confidence),
        evidence=evidence,
        created_at=created_at,
    )


def _document_case(raw_document, failure_type, severity, confidence, evidence, created_at) -> CrawlerFailureCase:
    source = getattr(raw_document, "source_registry", None)
    return CrawlerFailureCase(
        source_id=str(getattr(raw_document, "source_registry_id", "")),
        raw_document_id=str(getattr(raw_document, "id", "")) if getattr(raw_document, "id", None) is not None else None,
        source_name=getattr(source, "name", None),
        url=getattr(raw_document, "normalized_url", None) or getattr(raw_document, "original_url", None),
        failure_type=failure_type,
        severity=severity,
        confidence=clamp_confidence(confidence),
        evidence=evidence,
        created_at=created_at,
    )


def _status_code(raw_document) -> int:
    metadata = _metadata(raw_document)
    value = getattr(raw_document, "status_code", None)
    if value is None:
        value = metadata.get("status_code")
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _metadata(entity) -> dict:
    metadata = getattr(entity, "metadata_json", None)
    return metadata if isinstance(metadata, dict) else {}


def _is_structured_official_dataset(raw_document, metadata: dict) -> bool:
    source = getattr(raw_document, "source_registry", None)
    source_type = str(
        metadata.get("source_type")
        or getattr(source, "source_type", "")
        or ""
    ).lower()
    return "official_dataset" in source_type and (
        isinstance(metadata.get("structured_event"), dict) or isinstance(metadata.get("row_fields"), dict)
    )


def _declares_target(raw_document, metadata: dict, target: str, *, kind: str) -> bool:
    target = str(target or "").strip().lower()
    if not target:
        return False
    structured = metadata.get("structured_event")
    values: list[Any] = []
    if kind == "disease":
        values.extend(
            [
                getattr(raw_document, "disease_detected", None),
                metadata.get("disease_detected"),
                structured.get("disease") if isinstance(structured, dict) else None,
            ]
        )
        aliases = {alias.lower() for alias in expand_disease_aliases(target)}
        return any(str(value or "").strip().lower() in aliases for value in values)
    values.extend(
        [
            getattr(raw_document, "country_detected", None),
            metadata.get("country_detected"),
            structured.get("country") if isinstance(structured, dict) else None,
        ]
    )
    terms = {term.lower() for term in get_country_query_terms(target)}
    return any(str(value or "").strip().lower() in terms for value in values)


def _looks_broken(reason: str) -> bool:
    return "404" in reason or "not found" in reason or "broken" in reason


def _now() -> datetime:
    return datetime.now(UTC)
