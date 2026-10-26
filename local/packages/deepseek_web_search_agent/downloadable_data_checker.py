from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_DOCUMENT_BYTES = 2 * 1024 * 1024
ALLOWED_WHO_DOMAINS = ("who.int", "www.who.int", "afro.who.int")


@dataclass(frozen=True)
class DownloadableMetadata:
    url: str
    domain: str
    status: str
    status_code: int | None = None
    content_type: str | None = None
    content_length: int | None = None
    document_kind: str = "unknown"
    downloadable_candidate: bool = False
    table_candidate: bool = False
    pdf_candidate: bool = False
    preview_allowed: bool = False
    request_count: int = 0
    network_accessed: bool = False
    files_downloaded: bool = False
    full_body_saved: bool = False
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_allowed_who_domain(url: str) -> bool:
    hostname = (urlsplit(str(url or "")).hostname or "").lower()
    return hostname in ALLOWED_WHO_DOMAINS or hostname.endswith(".who.int")


def classify_document_kind(url: str, content_type: str | None = None) -> str:
    path = Path(urlsplit(str(url or "")).path.lower())
    suffix = path.suffix.lstrip(".")
    ctype = str(content_type or "").lower()
    if suffix in {"csv", "tsv"} or "csv" in ctype or "tab-separated" in ctype:
        return "csv_dataset"
    if suffix in {"json", "geojson"} or "json" in ctype:
        return "json_api"
    if suffix in {"xls", "xlsx"} or "spreadsheet" in ctype or "excel" in ctype:
        return "spreadsheet_dataset"
    if suffix == "pdf" or "pdf" in ctype:
        return "pdf_situation_report_preview"
    if "html" in ctype or suffix in {"", "htm", "html"}:
        return "html_table_or_report_page"
    return "official_report_page"


def check_downloadable_metadata(
    url: str,
    *,
    allow_network: bool = False,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    max_document_bytes: int = MAX_DOCUMENT_BYTES,
    transport: httpx.BaseTransport | None = None,
) -> DownloadableMetadata:
    safe_url = str(url or "").strip()
    domain = (urlsplit(safe_url).hostname or "").lower()
    if not is_allowed_who_domain(safe_url):
        return _metadata(
            safe_url,
            domain,
            "blocked",
            warnings=["non_who_domain_blocked"],
        )
    if not (allow_network and terms_reviewed and manual_reviewed):
        kind = classify_document_kind(safe_url)
        return _metadata(
            safe_url,
            domain,
            "dry_run",
            document_kind=kind,
            downloadable_candidate=kind in {"csv_dataset", "json_api", "spreadsheet_dataset"},
            table_candidate=kind in {"csv_dataset", "json_api", "html_table_or_report_page"},
            pdf_candidate=kind == "pdf_situation_report_preview",
            warnings=["metadata_check_requires_explicit_live_controls"],
        )
    try:
        with httpx.Client(timeout=15.0, follow_redirects=False, transport=transport) as client:
            response = client.head(safe_url)
    except httpx.TimeoutException:
        return _metadata(safe_url, domain, "failed", request_count=1, network_accessed=True, warnings=["head_timeout"])
    except httpx.HTTPError:
        return _metadata(safe_url, domain, "failed", request_count=1, network_accessed=True, warnings=["head_http_error"])
    content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower() or None
    content_length = _content_length(response.headers.get("content-length"))
    kind = classify_document_kind(safe_url, content_type)
    warnings: list[str] = []
    status = "ok"
    if response.status_code >= 400:
        status = "failed"
        warnings.append(f"http_{response.status_code}")
    if content_length is not None and content_length > max_document_bytes:
        status = "too_large"
        warnings.append("document_size_limit")
    preview_allowed = status == "ok" and kind in {
        "csv_dataset",
        "json_api",
        "html_table_or_report_page",
        "official_report_page",
        "pdf_situation_report_preview",
    }
    return DownloadableMetadata(
        url=safe_url,
        domain=domain,
        status=status,
        status_code=response.status_code,
        content_type=content_type,
        content_length=content_length,
        document_kind=kind,
        downloadable_candidate=kind in {"csv_dataset", "json_api", "spreadsheet_dataset"},
        table_candidate=kind in {"csv_dataset", "json_api", "html_table_or_report_page"},
        pdf_candidate=kind == "pdf_situation_report_preview",
        preview_allowed=preview_allowed,
        request_count=1,
        network_accessed=True,
        files_downloaded=False,
        full_body_saved=False,
        warnings=warnings,
    )


def _content_length(value: str | None) -> int | None:
    try:
        return int(value or "")
    except (TypeError, ValueError):
        return None


def _metadata(
    url: str,
    domain: str,
    status: str,
    *,
    status_code: int | None = None,
    content_type: str | None = None,
    content_length: int | None = None,
    document_kind: str = "unknown",
    downloadable_candidate: bool = False,
    table_candidate: bool = False,
    pdf_candidate: bool = False,
    request_count: int = 0,
    network_accessed: bool = False,
    warnings: list[str],
) -> DownloadableMetadata:
    return DownloadableMetadata(
        url=url,
        domain=domain,
        status=status,
        status_code=status_code,
        content_type=content_type,
        content_length=content_length,
        document_kind=document_kind,
        downloadable_candidate=downloadable_candidate,
        table_candidate=table_candidate,
        pdf_candidate=pdf_candidate,
        request_count=request_count,
        network_accessed=network_accessed,
        warnings=warnings,
    )


__all__ = [
    "ALLOWED_WHO_DOMAINS",
    "DownloadableMetadata",
    "MAX_DOCUMENT_BYTES",
    "check_downloadable_metadata",
    "classify_document_kind",
    "is_allowed_who_domain",
]
