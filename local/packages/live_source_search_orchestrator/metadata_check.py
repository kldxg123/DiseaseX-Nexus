from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx

from crawler_agent.country_terms import get_country_query_terms, normalize_country
from live_source_search_orchestrator.disease_alias_expander import expand_disease_aliases

HTML_READ_LIMIT = 65536
FILE_CONTENT_TYPES = (
    "application/pdf",
    "application/zip",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument",
    "text/csv",
    "application/csv",
)
DATA_TERMS = {
    "surveillance",
    "data",
    "dataset",
    "cases",
    "outbreak",
    "weekly",
    "dashboard",
    "table",
    "api",
    "csv",
    "download",
    "notifiable",
    "report",
}


@dataclass(frozen=True)
class MetadataCheckRequest:
    url: str
    title: str | None = None
    country: str | None = None
    disease: str | None = None


@dataclass(frozen=True)
class MetadataCheckResult:
    url: str
    domain: str
    status: str
    http_status_code: int | None
    content_type: str | None
    title_summary: str | None
    meta_description_summary: str | None
    data_source_likelihood: float
    detected_source_type: str
    parser_strategy_hint: str
    disease_relevance_confirmed: bool
    country_relevance_confirmed: bool
    has_table_hint: bool
    has_download_hint: bool
    has_dashboard_hint: bool
    has_api_hint: bool
    date_like_tokens: list[str] = field(default_factory=list)
    week_like_tokens: list[str] = field(default_factory=list)
    numeric_tokens_near_case_terms: int = 0
    numeric_tokens_near_death_terms: int = 0
    numeric_tokens_near_contact_terms: int = 0
    paragraphs_with_date_and_number: int = 0
    dated_numeric_signal_count: int = 0
    rows_with_date_and_numeric_hint: int = 0
    report_date_hint: bool = False
    situation_report_hint: bool = False
    document_download_hint: bool = False
    data_table_hint: bool = False
    extraction_ready_hint: bool = False
    extraction_ready_blocker: str | None = None
    no_follow_links: bool = True
    full_body_saved: bool = False
    files_downloaded: bool = False
    recommended_action: str = "manual_review"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetadataCheckSummary:
    status: str
    request_count: int
    checked: int
    confirmed_data_sources: int
    rejected_non_data_sources: int
    no_follow_links: bool
    files_downloaded: bool
    full_body_saved: bool
    results: list[MetadataCheckResult]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["results"] = [result.to_dict() for result in self.results]
        return payload


def run_metadata_check(
    candidates: list,
    *,
    max_requests: int = 3,
    allow_network: bool = False,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    timeout_seconds: float = 15.0,
    transport=None,
) -> MetadataCheckSummary:
    limit = max(0, min(int(max_requests), 3))
    if not candidates or limit <= 0:
        return MetadataCheckSummary(
            status="skipped",
            request_count=0,
            checked=0,
            confirmed_data_sources=0,
            rejected_non_data_sources=0,
            no_follow_links=True,
            files_downloaded=False,
            full_body_saved=False,
            results=[],
            warnings=["No metadata-check candidates were provided."],
        )
    if not (allow_network and terms_reviewed and manual_reviewed):
        return MetadataCheckSummary(
            status="denied",
            request_count=0,
            checked=0,
            confirmed_data_sources=0,
            rejected_non_data_sources=0,
            no_follow_links=True,
            files_downloaded=False,
            full_body_saved=False,
            results=[],
            warnings=["Metadata check requires allow_network, terms_reviewed, and manual_reviewed."],
        )
    results: list[MetadataCheckResult] = []
    warnings: list[str] = []
    request_count = 0
    seen_urls: set[str] = set()
    with httpx.Client(
        follow_redirects=False,
        timeout=float(timeout_seconds),
        transport=transport,
        headers={
            "User-Agent": "infectious-intel-local-metadata-check/0.1",
            "Range": f"bytes=0-{HTML_READ_LIMIT - 1}",
        },
    ) as client:
        for candidate in candidates:
            if request_count >= limit:
                break
            request = _candidate_to_request(candidate)
            if request.url in seen_urls:
                continue
            seen_urls.add(request.url)
            request_count += 1
            result = _check_one(client, request)
            warnings.extend(result.warnings)
            results.append(result)
    confirmed = sum(1 for result in results if result.data_source_likelihood >= 0.62)
    rejected = sum(1 for result in results if result.data_source_likelihood <= 0.25 or result.status == "non_data_source")
    return MetadataCheckSummary(
        status="ok",
        request_count=request_count,
        checked=len(results),
        confirmed_data_sources=confirmed,
        rejected_non_data_sources=rejected,
        no_follow_links=True,
        files_downloaded=False,
        full_body_saved=False,
        results=results,
        warnings=sorted(set(warnings)),
    )


def _check_one(client: httpx.Client, request: MetadataCheckRequest) -> MetadataCheckResult:
    domain = urlsplit(request.url).hostname or ""
    try:
        with client.stream("GET", request.url) as response:
            content_type = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            status_code = int(response.status_code)
            if 300 <= status_code < 400:
                return _result(
                    request,
                    domain=domain,
                    status="redirect_not_followed",
                    http_status_code=status_code,
                    content_type=content_type,
                    warnings=["Redirect was not followed during metadata check."],
                )
            if status_code >= 400:
                return _result(
                    request,
                    domain=domain,
                    status="failed",
                    http_status_code=status_code,
                    content_type=content_type,
                    warnings=[f"Metadata check returned HTTP {status_code}."],
                )
            if _is_file_like(content_type, request.url):
                return _file_like_result(request, domain=domain, http_status_code=status_code, content_type=content_type)
            sample = _read_limited_text(response)
            return _html_result(
                request,
                domain=domain,
                http_status_code=status_code,
                content_type=content_type,
                html_sample=sample,
            )
    except httpx.TimeoutException as exc:
        return _result(request, domain=domain, status="failed", http_status_code=None, content_type=None, warnings=[f"Timeout: {type(exc).__name__}."])
    except httpx.HTTPError as exc:
        return _result(request, domain=domain, status="failed", http_status_code=None, content_type=None, warnings=[f"HTTP error: {type(exc).__name__}."])


def _html_result(
    request: MetadataCheckRequest,
    *,
    domain: str,
    http_status_code: int,
    content_type: str,
    html_sample: str,
) -> MetadataCheckResult:
    parser = _MetadataHTMLParser()
    try:
        parser.feed(html_sample)
    except Exception:
        return _result(
            request,
            domain=domain,
            status="failed",
            http_status_code=http_status_code,
            content_type=content_type,
            warnings=["HTML metadata parser failed safely."],
        )
    text = " ".join(
        [
            parser.title,
            parser.meta_description,
            " ".join(parser.headings),
            " ".join(parser.link_texts[:30]),
            request.url,
        ]
    ).lower()
    disease_terms = expand_disease_aliases(request.disease or "").aliases if request.disease else []
    country_terms = get_country_query_terms(request.country or "") or [normalize_country(request.country or "") or str(request.country or "")]
    disease_confirmed = any(term and term.lower() in text for term in disease_terms)
    country_confirmed = any(term and term.lower() in text for term in country_terms)
    data_hits = sorted(term for term in DATA_TERMS if term in text)
    signal_profile = _dated_numeric_profile(
        " ".join([parser.title, parser.meta_description, " ".join(parser.headings), " ".join(parser.text_chunks[:80])])
    )
    has_table = parser.table_count > 0 or "table" in data_hits
    has_download = any(term in text for term in ("download", ".csv", ".xlsx", ".xls", ".zip"))
    has_dashboard = any(term in text for term in ("dashboard", "interactive", "chart"))
    has_api = any(term in text for term in ("api", "json"))
    likelihood = _likelihood(
        data_hits=data_hits,
        disease_confirmed=disease_confirmed,
        country_confirmed=country_confirmed,
        has_table=has_table,
        has_download=has_download,
        has_dashboard=has_dashboard,
        has_api=has_api,
        file_like=False,
    )
    detected_type, parser_hint = _source_type_from_hints(
        has_table=has_table,
        has_download=has_download,
        has_dashboard=has_dashboard,
        has_api=has_api,
        content_type=content_type,
        data_hits=data_hits,
    )
    status = "data_source_candidate" if likelihood >= 0.62 else "non_data_source" if likelihood <= 0.25 else "uncertain"
    return MetadataCheckResult(
        url=request.url,
        domain=domain,
        status=status,
        http_status_code=http_status_code,
        content_type=content_type,
        title_summary=_clip(parser.title or request.title, 120),
        meta_description_summary=_clip(parser.meta_description, 180),
        data_source_likelihood=round(likelihood, 6),
        detected_source_type=detected_type,
        parser_strategy_hint=parser_hint,
        disease_relevance_confirmed=disease_confirmed,
        country_relevance_confirmed=country_confirmed,
        has_table_hint=has_table,
        has_download_hint=has_download,
        has_dashboard_hint=has_dashboard,
        has_api_hint=has_api,
        date_like_tokens=signal_profile["date_like_tokens"],
        week_like_tokens=signal_profile["week_like_tokens"],
        numeric_tokens_near_case_terms=signal_profile["numeric_tokens_near_case_terms"],
        numeric_tokens_near_death_terms=signal_profile["numeric_tokens_near_death_terms"],
        numeric_tokens_near_contact_terms=signal_profile["numeric_tokens_near_contact_terms"],
        paragraphs_with_date_and_number=signal_profile["paragraphs_with_date_and_number"],
        dated_numeric_signal_count=signal_profile["dated_numeric_signal_count"],
        rows_with_date_and_numeric_hint=signal_profile["rows_with_date_and_numeric_hint"],
        report_date_hint=bool(signal_profile["date_like_tokens"]),
        situation_report_hint=bool("situation report" in text or "epidemiological update" in text),
        document_download_hint=has_download,
        data_table_hint=has_table,
        extraction_ready_hint=bool(signal_profile["dated_numeric_signal_count"] > 0 or (has_table and signal_profile["date_like_tokens"] and (signal_profile["numeric_tokens_near_case_terms"] or signal_profile["numeric_tokens_near_death_terms"]))),
        extraction_ready_blocker=None
        if signal_profile["dated_numeric_signal_count"] > 0
        else _metadata_blocker(signal_profile, has_table=has_table, has_api=has_api, has_download=has_download),
        recommended_action=_recommended_action(likelihood, parser_hint),
        warnings=[],
    )


def _file_like_result(request: MetadataCheckRequest, *, domain: str, http_status_code: int, content_type: str) -> MetadataCheckResult:
    detected_type, parser_hint = _source_type_from_hints(
        has_table=False,
        has_download=True,
        has_dashboard=False,
        has_api="json" in content_type,
        content_type=content_type,
        data_hits=["download", "data"],
    )
    return MetadataCheckResult(
        url=request.url,
        domain=domain,
        status="data_source_candidate",
        http_status_code=http_status_code,
        content_type=content_type,
        title_summary=_clip(request.title, 120),
        meta_description_summary=None,
        data_source_likelihood=0.74,
        detected_source_type=detected_type,
        parser_strategy_hint=parser_hint,
        disease_relevance_confirmed=False,
        country_relevance_confirmed=False,
        has_table_hint=False,
        has_download_hint=True,
        has_dashboard_hint=False,
        has_api_hint="json" in content_type,
        recommended_action="manual_review_file_like_metadata_only",
        warnings=["File-like content type detected; body was not downloaded."],
    )


def _result(
    request: MetadataCheckRequest,
    *,
    domain: str,
    status: str,
    http_status_code: int | None,
    content_type: str | None,
    warnings: list[str],
) -> MetadataCheckResult:
    return MetadataCheckResult(
        url=request.url,
        domain=domain,
        status=status,
        http_status_code=http_status_code,
        content_type=content_type,
        title_summary=_clip(request.title, 120),
        meta_description_summary=None,
        data_source_likelihood=0.0,
        detected_source_type="unknown",
        parser_strategy_hint="metadata_only",
        disease_relevance_confirmed=False,
        country_relevance_confirmed=False,
        has_table_hint=False,
        has_download_hint=False,
        has_dashboard_hint=False,
        has_api_hint=False,
        recommended_action="manual_review_or_skip",
        warnings=warnings,
    )


def _read_limited_text(response: httpx.Response) -> str:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        if not chunk:
            continue
        remaining = HTML_READ_LIMIT - total
        if remaining <= 0:
            break
        chunks.append(chunk[:remaining])
        total += len(chunk[:remaining])
        if total >= HTML_READ_LIMIT:
            break
    content = b"".join(chunks)
    digest = hashlib.sha256(content).hexdigest()[:12]
    _ = digest
    return content.decode(response.encoding or "utf-8", errors="ignore")


def _candidate_to_request(candidate: Any) -> MetadataCheckRequest:
    if isinstance(candidate, MetadataCheckRequest):
        return candidate
    if isinstance(candidate, dict):
        return MetadataCheckRequest(
            url=str(candidate.get("normalized_url") or candidate.get("url") or candidate.get("base_url") or ""),
            title=candidate.get("normalized_title") or candidate.get("title") or candidate.get("source_name"),
            country=candidate.get("country"),
            disease=candidate.get("disease"),
        )
    return MetadataCheckRequest(
        url=str(getattr(candidate, "normalized_url", "") or getattr(candidate, "url", "")),
        title=getattr(candidate, "normalized_title", None) or getattr(candidate, "source_name", None),
        country=getattr(candidate, "country", None),
        disease=getattr(candidate, "disease", None),
    )


class _MetadataHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.headings: list[str] = []
        self.link_texts: list[str] = []
        self.text_chunks: list[str] = []
        self.table_count = 0
        self._active_tag: str | None = None
        self._active_link = False

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attrs_dict = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag == "meta" and attrs_dict.get("name", "").lower() == "description":
            self.meta_description = _clip(attrs_dict.get("content"), 180) or ""
        if tag in {"title", "h1", "h2"}:
            self._active_tag = tag
        if tag == "a":
            self._active_link = True
        if tag == "table":
            self.table_count += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == self._active_tag:
            self._active_tag = None
        if tag == "a":
            self._active_link = False

    def handle_data(self, data: str) -> None:
        text = _clip(" ".join(str(data or "").split()), 120)
        if not text:
            return
        if self._active_tag == "title" and not self.title:
            self.title = text
        elif self._active_tag in {"h1", "h2"} and len(self.headings) < 8:
            self.headings.append(text)
        elif self._active_link and len(self.link_texts) < 60:
            self.link_texts.append(text)
        elif len(self.text_chunks) < 160:
            self.text_chunks.append(text)


def _source_type_from_hints(
    *,
    has_table: bool,
    has_download: bool,
    has_dashboard: bool,
    has_api: bool,
    content_type: str,
    data_hits: list[str],
) -> tuple[str, str]:
    if "json" in content_type or has_api:
        return "json_api", "json_api"
    if "csv" in content_type:
        return "csv_dataset", "csv_dataset"
    if has_table:
        return "html_table", "html_table"
    if has_dashboard:
        return "dashboard_data_source", "dashboard_metadata"
    if has_download:
        return "downloadable_dataset", "download_metadata"
    if "report" in data_hits or "weekly" in data_hits:
        return "official_report_page", "generic_html"
    return "unknown", "metadata_only"


def _likelihood(
    *,
    data_hits: list[str],
    disease_confirmed: bool,
    country_confirmed: bool,
    has_table: bool,
    has_download: bool,
    has_dashboard: bool,
    has_api: bool,
    file_like: bool,
) -> float:
    value = 0.12 + min(0.36, len(data_hits) * 0.06)
    if disease_confirmed:
        value += 0.12
    if country_confirmed:
        value += 0.08
    if has_table:
        value += 0.12
    if has_download:
        value += 0.12
    if has_dashboard:
        value += 0.1
    if has_api:
        value += 0.12
    if file_like:
        value += 0.18
    return max(0.0, min(1.0, value))


def _recommended_action(likelihood: float, parser_hint: str) -> str:
    if likelihood >= 0.62:
        return f"manual_review_connector_candidate:{parser_hint}"
    if likelihood <= 0.25:
        return "deprioritize_non_data_source"
    return "manual_review_metadata_uncertain"


def _is_file_like(content_type: str, url: str) -> bool:
    lowered = f"{content_type} {url}".lower()
    file_extensions = (".pdf", ".zip", ".xlsx", ".xls", ".csv")
    return any(content_type.startswith(item) for item in FILE_CONTENT_TYPES) or lowered.endswith(file_extensions)


def _clip(value: Any, limit: int) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


_DATE_RE = r"\b20\d{2}-\d{1,2}-\d{1,2}\b|\b\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+20\d{2}\b|\bweek\s+\d{1,2}\b|\b20\d{2}-?w\d{1,2}\b"
_NUMBER_RE = r"\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\b"


def _dated_numeric_profile(text: str) -> dict[str, Any]:
    date_tokens = re.findall(_DATE_RE, text, flags=re.I)[:20]
    week_tokens = re.findall(r"\bweek\s+\d{1,2}\b|\b20\d{2}-?w\d{1,2}\b", text, flags=re.I)[:20]
    chunks = re.split(r"(?<=[.;])\s+|\n+", text)
    paragraphs_with_date_number = 0
    case_near = death_near = contact_near = 0
    for chunk in chunks:
        has_date = bool(re.search(_DATE_RE, chunk, flags=re.I))
        has_number = bool(re.search(_NUMBER_RE, chunk))
        if has_date and has_number:
            paragraphs_with_date_number += 1
        if re.search(r"\bcase[s]?|confirmed|suspected|probable\b", chunk, flags=re.I) and has_number:
            case_near += 1
        if re.search(r"\bdeath[s]?|fatalit", chunk, flags=re.I) and has_number:
            death_near += 1
        if re.search(r"\bcontact[s]?|monitoring\b", chunk, flags=re.I) and has_number:
            contact_near += 1
    return {
        "date_like_tokens": [str(item)[:80] for item in date_tokens],
        "week_like_tokens": [str(item)[:80] for item in week_tokens],
        "numeric_tokens_near_case_terms": case_near,
        "numeric_tokens_near_death_terms": death_near,
        "numeric_tokens_near_contact_terms": contact_near,
        "paragraphs_with_date_and_number": paragraphs_with_date_number,
        "dated_numeric_signal_count": paragraphs_with_date_number,
        "rows_with_date_and_numeric_hint": paragraphs_with_date_number,
    }


def _metadata_blocker(profile: dict[str, Any], *, has_table: bool, has_api: bool, has_download: bool) -> str:
    has_date = bool(profile.get("date_like_tokens"))
    has_numeric = bool(
        profile.get("numeric_tokens_near_case_terms")
        or profile.get("numeric_tokens_near_death_terms")
        or profile.get("numeric_tokens_near_contact_terms")
    )
    if has_api and not has_numeric:
        return "api_hint_without_data"
    if has_download:
        return "document_candidate_not_downloaded"
    if has_numeric and not has_date:
        return "numeric_signal_without_date"
    if has_date and not has_numeric:
        return "date_signal_without_numeric"
    if has_table:
        return "table_found_but_no_dated_numeric_signal"
    return "needs_structured_document_or_dataset"


__all__ = [
    "MetadataCheckRequest",
    "MetadataCheckResult",
    "MetadataCheckSummary",
    "run_metadata_check",
]
