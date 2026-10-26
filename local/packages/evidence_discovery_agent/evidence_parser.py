from __future__ import annotations

from typing import Any

from evidence_discovery_agent.types import EvidenceDocument

MAX_SUMMARY_CHARS = 700


def parse_evidence_documents(payload: Any) -> list[EvidenceDocument]:
    if isinstance(payload, dict):
        entries = payload.get("documents", [payload])
    elif isinstance(payload, list):
        entries = payload
    else:
        raise ValueError("evidence fixture must be a mapping or list")
    return [parse_evidence_document(entry) for entry in entries if isinstance(entry, dict)]


def parse_evidence_document(entry: dict[str, Any]) -> EvidenceDocument:
    return EvidenceDocument(
        title=_safe_text(entry.get("title") or "Untitled evidence"),
        source_type=_safe_text(entry.get("source_type") or "fixture_summary"),
        disease=_safe_text(entry.get("disease") or "generic").lower(),
        target=_safe_text(entry.get("target") or "outbreak_or_case_trend").lower(),
        features_mentioned=_string_list(entry.get("features_mentioned") or entry.get("features mentioned")),
        models_mentioned=_string_list(entry.get("models_mentioned") or entry.get("models mentioned")),
        metrics_mentioned=_string_list(entry.get("metrics_mentioned") or entry.get("metrics mentioned")),
        limitations=_string_list(entry.get("limitations")),
        summary=_safe_text(entry.get("summary") or entry.get("short abstract-like summary") or "", max_chars=MAX_SUMMARY_CHARS),
        evidence_source=_safe_text(entry.get("evidence_source") or entry.get("source_type") or "local_fixture"),
        country_scope=_string_list(entry.get("country_scope")),
        data_modality=_safe_text(entry.get("data_modality") or "weekly_surveillance"),
        applicability_notes=_string_list(entry.get("applicability_notes")),
        requires_human_review=True,
    )


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        values = [value]
    return [_safe_text(item) for item in values if str(item).strip()]


def _safe_text(value: Any, *, max_chars: int = 180) -> str:
    text = " ".join(str(value or "").split())
    lowered = text.lower()
    if any(marker in lowered for marker in ("password", "token", "secret", "private key", "ssh_key_path")):
        return "[redacted]"
    if len(text) > max_chars:
        return text[:max_chars] + "...[truncated]"
    return text
