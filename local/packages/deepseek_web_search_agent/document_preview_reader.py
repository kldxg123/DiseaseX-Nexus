from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from deepseek_web_search_agent.contextual_evidence_builder import DATE_RE, METRIC_PATTERNS


MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
MAX_TEXT_PREVIEW_CHARS = 5000
MAX_TABLE_PREVIEW_ROWS = 10


@dataclass(frozen=True)
class DocumentPreviewResult:
    url: str
    domain: str
    status: str
    document_type: str | None
    content_length: int | None
    preview_page_count: int = 0
    date_anchors: list[dict[str, Any]] = field(default_factory=list)
    metric_anchors: list[dict[str, Any]] = field(default_factory=list)
    table_like_text_blocks: list[dict[str, Any]] = field(default_factory=list)
    source_pack_material_strength: str = "document_candidate_summary"
    full_body_saved: bool = False
    files_downloaded: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def preview_document_candidate(
    url: str,
    *,
    allow_document_preview: bool = False,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    max_document_bytes: int = MAX_DOCUMENT_BYTES,
    max_text_preview_chars: int = MAX_TEXT_PREVIEW_CHARS,
    max_table_preview_rows: int = MAX_TABLE_PREVIEW_ROWS,
    transport: httpx.BaseTransport | None = None,
) -> DocumentPreviewResult:
    domain = urlsplit(str(url or "")).hostname or ""
    if not (allow_document_preview and terms_reviewed and manual_reviewed):
        return _result(url, domain, "denied", None, None, warnings=["document_preview_requires_explicit_controls"])
    try:
        with httpx.Client(timeout=15.0, follow_redirects=False, transport=transport) as client:
            response = client.get(str(url), headers={"Range": f"bytes=0-{max_document_bytes - 1}"})
    except httpx.TimeoutException:
        return _result(url, domain, "failed", None, None, warnings=["document_preview_timeout"])
    except httpx.HTTPError:
        return _result(url, domain, "failed", None, None, warnings=["document_preview_http_error"])
    content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    content_length = _content_length(response)
    if response.status_code >= 400:
        return _result(url, domain, "failed", content_type, content_length, warnings=[f"http_{response.status_code}"])
    if content_length is not None and content_length > max_document_bytes:
        return _result(url, domain, "too_large", content_type, content_length, warnings=["document_preview_size_limit"])
    if "pdf" in content_type or str(url).lower().split("?", 1)[0].endswith(".pdf"):
        return _result(
            url,
            domain,
            "skipped_pdf",
            content_type,
            content_length,
            warnings=["pdf_text_extraction_unavailable"],
        )
    sample = response.text[:max_text_preview_chars]
    if "csv" in content_type or str(url).lower().split("?", 1)[0].endswith(".csv"):
        table_rows = _csv_preview(sample, max_rows=max_table_preview_rows)
        return _preview_result(url, domain, "ok", "csv", content_length, sample, table_rows)
    if "json" in content_type or str(url).lower().split("?", 1)[0].endswith(".json"):
        text = _json_preview_text(sample)
        return _preview_result(url, domain, "ok", "json", content_length, text, [])
    return _preview_result(url, domain, "ok", content_type or "html", content_length, sample, [])


def _preview_result(
    url: str,
    domain: str,
    status: str,
    document_type: str,
    content_length: int | None,
    text: str,
    table_rows: list[dict[str, str]],
) -> DocumentPreviewResult:
    dates = [{"anchor_type": "explicit_date_in_text", "value": match.group(0)[:80]} for match in DATE_RE.finditer(text[:MAX_TEXT_PREVIEW_CHARS])]
    metrics: list[dict[str, Any]] = []
    for metric_name, pattern in METRIC_PATTERNS:
        if pattern.search(text) and any(char.isdigit() for char in text):
            metrics.append({"anchor_type": metric_name, "value": "numeric_signal_present"})
    strength = "csv_preview" if table_rows else "paragraph_numeric_preview" if dates and metrics else "document_candidate_summary"
    return DocumentPreviewResult(
        url=url,
        domain=domain,
        status=status,
        document_type=document_type,
        content_length=content_length,
        preview_page_count=1,
        date_anchors=dates[:20],
        metric_anchors=metrics[:20],
        table_like_text_blocks=table_rows[:MAX_TABLE_PREVIEW_ROWS],
        source_pack_material_strength=strength,
        full_body_saved=False,
        files_downloaded=False,
        warnings=[],
    )


def _csv_preview(text: str, *, max_rows: int) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        rows.append({str(key)[:80]: str(value)[:120] for key, value in row.items() if key})
        if len(rows) >= max_rows:
            break
    return rows


def _json_preview_text(text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return text[:MAX_TEXT_PREVIEW_CHARS]
    return json.dumps(payload, ensure_ascii=False, default=str)[:MAX_TEXT_PREVIEW_CHARS]


def _content_length(response: httpx.Response) -> int | None:
    try:
        return int(response.headers.get("content-length") or len(response.content))
    except (TypeError, ValueError):
        return None


def _result(
    url: str,
    domain: str,
    status: str,
    document_type: str | None,
    content_length: int | None,
    *,
    warnings: list[str],
) -> DocumentPreviewResult:
    return DocumentPreviewResult(
        url=str(url),
        domain=domain,
        status=status,
        document_type=document_type,
        content_length=content_length,
        warnings=warnings,
    )


__all__ = ["DocumentPreviewResult", "preview_document_candidate"]
