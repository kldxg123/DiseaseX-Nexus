from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from deepseek_web_search_agent.downloadable_data_checker import (
    DownloadableMetadata,
    check_downloadable_metadata,
    classify_document_kind,
    is_allowed_who_domain,
)
from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.structured_table_types import DiseaseTimeQueryRequest, SourceEvidenceItem
from deepseek_web_search_agent.who_data_parser_bridge import (
    rows_from_csv_preview,
    rows_from_html_table,
    rows_from_json_payload,
    source_evidence_from_candidate,
    write_who_review_bundle,
)
from web_search_providers.registry import get_web_search_provider
from web_search_providers.types import WebSearchRequest, WebSearchResult

WHO_FIRST_SOURCE_PREFERENCE = ["WHO", "WHO AFRO", "WHO data"]
WHO_FIRST_DATA_PREFERENCE = [
    "csv_dataset",
    "html_table",
    "json_api",
    "official_report_page",
    "pdf_situation_report_preview",
]


@dataclass(frozen=True)
class WHODataCandidate:
    source_id: str
    title_summary: str
    url_safe_summary: str
    domain: str
    source_type: str
    disease_relevance_score: float
    structured_data_likelihood: float
    recommended_parser_strategy: str
    requires_human_review: bool = True
    registry_status: str = "pending_review"
    evidence_summary: str = ""
    date_hint: str | None = None
    metadata_status: str | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WHOAcquisitionPlan:
    disease: str
    country_or_region: str
    date_start: str
    date_end: str
    provider: str
    queries: list[str]
    preferred_sources: list[str]
    preferred_data_types: list[str]
    max_provider_requests: int
    max_metadata_checks: int
    max_document_bytes: int
    max_csv_preview_rows: int
    max_pdf_pages: int
    live_enabled: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WHOAcquisitionResult:
    status: str
    run_id: str
    run_dir: str
    disease: str
    country_or_region: str
    date_start: str
    date_end: str
    provider: str
    network_accessed: bool
    provider_request_count: int
    metadata_check_request_count: int
    preview_request_count: int
    candidates_found: int
    review_rows_generated: int
    pending_review_rows: int
    approved_rows_ingested: int
    feature_generation_allowed: bool
    paths: dict[str, str]
    candidates: list[dict[str, Any]]
    metadata_checks: list[dict[str, Any]]
    field_mapping_reports: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    limitations: str = (
        "WHO-first acquisition test only. Sources and rows stay pending_review by default; "
        "no production prediction or automatic connector activation is performed."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_who_query_plan(
    *,
    disease: str = "Ebola",
    country_or_region: str = "Democratic Republic of the Congo",
    date_start: str = "2025-01-01",
    date_end: str = "2025-12-31",
    provider: str = "tavily",
    max_provider_requests: int = 3,
    max_metadata_checks: int = 3,
    live_enabled: bool = False,
) -> WHOAcquisitionPlan:
    base = f"{disease} {country_or_region} {date_start[:4]} cases deaths outbreak"
    queries = [
        f'site:who.int {base} "Disease Outbreak News"',
        f"site:afro.who.int {base} situation report confirmed deaths",
        f"site:who.int {base} CSV data table confirmed deaths",
        f"site:who.int {base} dashboard table weekly cases deaths",
        f"site:afro.who.int {base} PDF situation report health zone",
    ]
    return WHOAcquisitionPlan(
        disease=disease,
        country_or_region=country_or_region,
        date_start=date_start,
        date_end=date_end,
        provider=provider,
        queries=queries,
        preferred_sources=list(WHO_FIRST_SOURCE_PREFERENCE),
        preferred_data_types=list(WHO_FIRST_DATA_PREFERENCE),
        max_provider_requests=max(0, min(5, int(max_provider_requests))),
        max_metadata_checks=max(0, min(5, int(max_metadata_checks))),
        max_document_bytes=2 * 1024 * 1024,
        max_csv_preview_rows=50,
        max_pdf_pages=3,
        live_enabled=bool(live_enabled),
    )


def run_who_data_acquisition(
    *,
    disease: str = "Ebola",
    country_or_region: str = "Democratic Republic of the Congo",
    date_start: str = "2025-01-01",
    date_end: str = "2025-12-31",
    provider: str = "tavily",
    output_dir: str = "reports/who_data_acquisition",
    dry_run: bool = True,
    allow_live_who: bool = False,
    allow_network: bool = False,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    max_provider_requests: int = 3,
    max_metadata_checks: int = 3,
    transport: httpx.BaseTransport | None = None,
) -> WHOAcquisitionResult:
    live_enabled = bool(not dry_run and allow_live_who and allow_network and terms_reviewed and manual_reviewed)
    plan = build_who_query_plan(
        disease=disease,
        country_or_region=country_or_region,
        date_start=date_start,
        date_end=date_end,
        provider=provider,
        max_provider_requests=max_provider_requests,
        max_metadata_checks=max_metadata_checks,
        live_enabled=live_enabled,
    )
    run_id = _run_id(disease, date_start)
    run_dir = Path(output_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    query_request = build_disease_time_query_request(
        disease=disease,
        country_or_region=country_or_region,
        date_start=date_start,
        date_end=date_end,
        max_sources=10,
    )
    warnings: list[str] = []
    provider_request_count = 0
    network_accessed = False
    if live_enabled:
        candidates, provider_request_count, network_accessed, provider_warnings = _live_candidates(plan, transport=transport)
        warnings.extend(provider_warnings)
    else:
        candidates = _mock_candidates(query_request)
        if not dry_run:
            warnings.append("live_controls_missing_or_disabled")
    metadata_checks: list[DownloadableMetadata] = []
    if live_enabled:
        for candidate in candidates[: plan.max_metadata_checks]:
            metadata_checks.append(
                check_downloadable_metadata(
                    candidate.url_safe_summary,
                    allow_network=True,
                    terms_reviewed=True,
                    manual_reviewed=True,
                    max_document_bytes=plan.max_document_bytes,
                    transport=transport,
                )
            )
    else:
        for candidate in candidates[: plan.max_metadata_checks]:
            metadata_checks.append(check_downloadable_metadata(candidate.url_safe_summary))
    candidates = _apply_metadata(candidates, metadata_checks)
    sources, extracted_rows, field_reports = _build_review_material(query_request, candidates, dry_run=not live_enabled)
    bundle = write_who_review_bundle(
        output_dir=run_dir,
        query_request=query_request,
        sources=sources,
        extracted_rows=extracted_rows,
        warnings=warnings,
    )
    extra_paths = _write_who_reports(
        run_dir=run_dir,
        plan=plan,
        candidates=candidates,
        metadata_checks=metadata_checks,
        field_reports=field_reports,
        bundle_paths=bundle.report_paths,
        warnings=warnings,
    )
    paths = {**bundle.report_paths, **extra_paths}
    summary = WHOAcquisitionResult(
        status="ok" if candidates else "no_candidates",
        run_id=run_id,
        run_dir=str(run_dir),
        disease=disease,
        country_or_region=country_or_region,
        date_start=date_start,
        date_end=date_end,
        provider=provider,
        network_accessed=network_accessed,
        provider_request_count=provider_request_count,
        metadata_check_request_count=sum(item.request_count for item in metadata_checks),
        preview_request_count=0,
        candidates_found=len(candidates),
        review_rows_generated=len(bundle.cleaned_rows),
        pending_review_rows=bundle.approval_status_summary.get("pending_review", 0),
        approved_rows_ingested=0,
        feature_generation_allowed=False,
        paths=paths,
        candidates=[candidate.to_dict() for candidate in candidates],
        metadata_checks=[item.to_dict() for item in metadata_checks],
        field_mapping_reports=[item.to_dict() for item in field_reports],
        warnings=warnings,
    )
    _write_json(run_dir / "who_data_acquisition_summary.json", safe_summary(summary.to_dict()))
    _write_text(run_dir / "who_data_acquisition_summary.md", _summary_markdown(summary))
    return summary


def safe_summary(payload: dict[str, Any]) -> dict[str, Any]:
    candidates = payload.get("candidates") if isinstance(payload.get("candidates"), list) else []
    paths = payload.get("paths") if isinstance(payload.get("paths"), dict) else {}
    return {
        "status": payload.get("status"),
        "run_id": payload.get("run_id"),
        "run_dir": payload.get("run_dir"),
        "disease": payload.get("disease"),
        "country_or_region": payload.get("country_or_region"),
        "date_start": payload.get("date_start"),
        "date_end": payload.get("date_end"),
        "provider": payload.get("provider"),
        "network_accessed": payload.get("network_accessed"),
        "provider_request_count": payload.get("provider_request_count"),
        "metadata_check_request_count": payload.get("metadata_check_request_count"),
        "preview_request_count": payload.get("preview_request_count"),
        "candidates_found": payload.get("candidates_found"),
        "review_rows_generated": payload.get("review_rows_generated"),
        "pending_review_rows": payload.get("pending_review_rows"),
        "approved_rows_ingested": payload.get("approved_rows_ingested"),
        "feature_generation_allowed": payload.get("feature_generation_allowed"),
        "top_candidate_domains": _top_domains(candidates),
        "review_json": paths.get("review_json"),
        "review_csv": paths.get("review_csv"),
        "source_map": paths.get("source_map"),
        "field_mapping_report": paths.get("field_mapping_report"),
        "metadata_checks": paths.get("download_metadata_checks"),
        "safety_audit": paths.get("safety_audit"),
        "rollback_commands": paths.get("rollback_commands"),
        "warnings": list(payload.get("warnings") or [])[:8],
        "limitations": payload.get("limitations"),
    }


def _live_candidates(
    plan: WHOAcquisitionPlan,
    *,
    transport: httpx.BaseTransport | None,
) -> tuple[list[WHODataCandidate], int, bool, list[str]]:
    provider = get_web_search_provider(plan.provider, transport=transport)
    candidates: list[WHODataCandidate] = []
    seen: set[str] = set()
    request_count = 0
    network_accessed = False
    warnings: list[str] = []
    for query in plan.queries[: plan.max_provider_requests]:
        result = provider.search(
            WebSearchRequest(
                query=query,
                provider_name=plan.provider,
                max_results=5,
                search_depth="basic",
                allow_network=True,
                country=plan.country_or_region,
                disease=plan.disease,
                domain_filter=["who.int", "afro.who.int"],
            )
        )
        request_count += int(result.request_count or 0)
        network_accessed = network_accessed or bool(result.network_accessed)
        if result.failure_type:
            warnings.append(f"{result.provider_name}:{result.failure_type}")
        for item in result.results:
            candidate = _candidate_from_result(item, index=len(candidates) + 1)
            if not candidate:
                continue
            key = _dedupe_key(candidate.url_safe_summary)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
    candidates.sort(key=lambda item: (item.disease_relevance_score, item.structured_data_likelihood), reverse=True)
    return candidates[:10], request_count, network_accessed, sorted(set(warnings))


def _candidate_from_result(result: WebSearchResult, *, index: int) -> WHODataCandidate | None:
    url = str(result.url or "").strip()
    if not is_allowed_who_domain(url):
        return None
    text = " ".join([result.title, result.snippet, url]).lower()
    source_type = _infer_source_type(url, text)
    relevance = 0.55
    if "ebola" in text:
        relevance += 0.18
    if "congo" in text or "drc" in text or "african region" in text:
        relevance += 0.12
    if "who" in result.domain:
        relevance += 0.1
    structured = _structured_likelihood(source_type, text)
    return WHODataCandidate(
        source_id=f"WHO{index}",
        title_summary=result.title[:220],
        url_safe_summary=url[:260],
        domain=result.domain,
        source_type=source_type,
        disease_relevance_score=round(min(1.0, relevance), 4),
        structured_data_likelihood=round(structured, 4),
        recommended_parser_strategy=source_type,
        evidence_summary=result.snippet[:500],
        date_hint=result.published_date,
    )


def _mock_candidates(query_request: DiseaseTimeQueryRequest) -> list[WHODataCandidate]:
    base = [
        (
            "WHO Ebola DRC 2025 weekly CSV dataset",
            "https://www.who.int/data/ebola-drc-2025-weekly-surveillance.csv",
            "csv_dataset",
            "Synthetic WHO-style CSV candidate with weekly Ebola cases and deaths for DRC in 2025.",
            0.92,
            0.95,
        ),
        (
            "WHO AFRO Ebola situation report table",
            "https://www.afro.who.int/publications/ebola-drc-2025-situation-report",
            "html_table",
            "Synthetic WHO AFRO-style situation report page with an HTML table preview.",
            0.88,
            0.82,
        ),
        (
            "WHO Ebola DRC JSON surveillance preview",
            "https://www.who.int/data/ebola-drc-2025-surveillance.json",
            "json_api",
            "Synthetic WHO-style JSON candidate for parser bridge validation.",
            0.84,
            0.78,
        ),
        (
            "WHO AFRO Ebola PDF situation report preview",
            "https://www.afro.who.int/sites/default/files/2025-03/ebola-drc-sitrep.pdf",
            "pdf_situation_report_preview",
            "Synthetic PDF situation report candidate; preview limited to metadata and first pages in future live runs.",
            0.79,
            0.4,
        ),
    ]
    candidates = []
    for index, (title, url, source_type, summary, relevance, structured) in enumerate(base, start=1):
        candidates.append(
            WHODataCandidate(
                source_id=f"WHO{index}",
                title_summary=title,
                url_safe_summary=url,
                domain=urlsplit(url).hostname or "who.int",
                source_type=source_type,
                disease_relevance_score=relevance,
                structured_data_likelihood=structured,
                recommended_parser_strategy=source_type,
                evidence_summary=summary,
                date_hint=query_request.date_start,
            )
        )
    return candidates


def _apply_metadata(
    candidates: list[WHODataCandidate],
    metadata_checks: list[DownloadableMetadata],
) -> list[WHODataCandidate]:
    by_url = {item.url: item for item in metadata_checks}
    updated: list[WHODataCandidate] = []
    for candidate in candidates:
        metadata = by_url.get(candidate.url_safe_summary)
        if not metadata:
            updated.append(candidate)
            continue
        kind = metadata.document_kind if metadata.document_kind != "unknown" else candidate.source_type
        updated.append(
            WHODataCandidate(
                **{
                    **candidate.to_dict(),
                    "source_type": _normalize_source_type(kind),
                    "recommended_parser_strategy": _normalize_source_type(kind),
                    "metadata_status": metadata.status,
                    "warnings": sorted(set([*candidate.warnings, *metadata.warnings])),
                }
            )
        )
    return updated


def _build_review_material(
    query_request: DiseaseTimeQueryRequest,
    candidates: list[WHODataCandidate],
    *,
    dry_run: bool,
) -> tuple[list[SourceEvidenceItem], list[dict[str, Any]], list[Any]]:
    sources: list[SourceEvidenceItem] = []
    rows: list[dict[str, Any]] = []
    field_reports: list[Any] = []
    for candidate in candidates[:10]:
        preview_rows: list[dict[str, Any]] = []
        extracted: list[dict[str, Any]] = []
        report = None
        if dry_run:
            if candidate.source_type == "csv_dataset":
                extracted, report = rows_from_csv_preview(
                    _mock_csv(query_request),
                    query_request=query_request,
                    source_id=candidate.source_id,
                )
            elif candidate.source_type == "html_table":
                extracted, report = rows_from_html_table(
                    _mock_html(query_request),
                    query_request=query_request,
                    source_id=candidate.source_id,
                )
            elif candidate.source_type == "json_api":
                extracted, report = rows_from_json_payload(
                    _mock_json(query_request),
                    query_request=query_request,
                    source_id=candidate.source_id,
                    records_path="records",
                )
            preview_rows = _rows_to_preview(extracted)
        source = source_evidence_from_candidate(candidate.to_dict(), source_id=candidate.source_id, table_preview_rows=preview_rows)
        sources.append(source)
        rows.extend(extracted)
        if report is not None:
            field_reports.append(report)
    return sources, rows, field_reports


def _write_who_reports(
    *,
    run_dir: Path,
    plan: WHOAcquisitionPlan,
    candidates: list[WHODataCandidate],
    metadata_checks: list[DownloadableMetadata],
    field_reports: list[Any],
    bundle_paths: dict[str, str],
    warnings: list[str],
) -> dict[str, str]:
    paths = {
        "who_query_plan": str(run_dir / "who_query_plan.json"),
        "who_candidates": str(run_dir / "who_candidates.json"),
        "download_metadata_checks": str(run_dir / "download_metadata_checks.json"),
        "field_mapping_report": str(run_dir / "field_mapping_report.json"),
        "approved_ingestion_plan": str(run_dir / "approved_ingestion_plan.md"),
        "who_safety_audit": str(run_dir / "who_safety_audit.md"),
    }
    _write_json(paths["who_query_plan"], plan.to_dict())
    _write_json(paths["who_candidates"], {"candidates": [item.to_dict() for item in candidates]})
    _write_json(paths["download_metadata_checks"], {"metadata_checks": [item.to_dict() for item in metadata_checks]})
    _write_json(paths["field_mapping_report"], {"field_mappings": [item.to_dict() for item in field_reports]})
    _write_text(paths["approved_ingestion_plan"], _approved_ingestion_plan_markdown(bundle_paths))
    _write_text(paths["who_safety_audit"], _who_safety_audit_markdown(plan, warnings))
    return paths


def _mock_csv(query_request: DiseaseTimeQueryRequest) -> str:
    return "\n".join(
        [
            "date,country,province,confirmed_cases,confirmed_deaths,suspected_cases,contacts_under_monitoring",
            f"{query_request.date_start[:4]}-02-03,{query_request.country_or_region},Equateur,7,2,3,44",
            f"{query_request.date_start[:4]}-02-10,{query_request.country_or_region},Equateur,11,3,4,61",
            f"{query_request.date_start[:4]}-02-17,{query_request.country_or_region},Equateur,15,4,5,80",
        ]
    )


def _mock_html(query_request: DiseaseTimeQueryRequest) -> str:
    return (
        "<table><tr><th>Report date</th><th>Health zone</th><th>Confirmed cases</th>"
        "<th>Deaths</th><th>Contacts</th></tr>"
        f"<tr><td>{query_request.date_start[:4]}-03-03</td><td>Mbandaka</td><td>4</td><td>1</td><td>20</td></tr>"
        f"<tr><td>{query_request.date_start[:4]}-03-10</td><td>Mbandaka</td><td>6</td><td>2</td><td>26</td></tr>"
        "</table>"
    )


def _mock_json(query_request: DiseaseTimeQueryRequest) -> dict[str, Any]:
    return {
        "records": [
            {
                "date": f"{query_request.date_start[:4]}-04-07",
                "country": query_request.country_or_region,
                "region": "North Kivu",
                "confirmed": 3,
                "deaths": 1,
                "suspected": 2,
            }
        ]
    }


def _rows_to_preview(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preview = []
    for row in rows[:10]:
        preview.append(
            {
                "date": row.get("date"),
                "country": row.get("country"),
                "region": row.get("region"),
                "confirmed_cases": row.get("confirmed_cases"),
                "confirmed_deaths": row.get("confirmed_deaths"),
                "suspected_cases": row.get("suspected_cases"),
                "contacts_under_monitoring": row.get("contacts_under_monitoring"),
            }
        )
    return preview


def _infer_source_type(url: str, text: str) -> str:
    kind = classify_document_kind(url)
    if kind != "official_report_page":
        return _normalize_source_type(kind)
    if "csv" in text:
        return "csv_dataset"
    if "json" in text or "api" in text:
        return "json_api"
    if "pdf" in text or "situation report" in text:
        return "pdf_situation_report_preview"
    if "table" in text or "dashboard" in text:
        return "html_table"
    return "official_report_page"


def _normalize_source_type(kind: str) -> str:
    if kind == "html_table_or_report_page":
        return "html_table"
    if kind == "spreadsheet_dataset":
        return "csv_dataset"
    return kind if kind in WHO_FIRST_DATA_PREFERENCE else "official_report_page"


def _structured_likelihood(source_type: str, text: str) -> float:
    base = {
        "csv_dataset": 0.95,
        "html_table": 0.78,
        "json_api": 0.82,
        "official_report_page": 0.52,
        "pdf_situation_report_preview": 0.42,
    }.get(source_type, 0.35)
    if any(term in text for term in ("cases", "deaths", "confirmed", "table", "data")):
        base += 0.08
    return min(1.0, base)


def _dedupe_key(url: str) -> str:
    parsed = urlsplit(str(url or ""))
    return f"{parsed.hostname or ''}{parsed.path}".lower().rstrip("/")


def _top_domains(candidates: list[dict[str, Any]]) -> list[str]:
    domains: list[str] = []
    for item in candidates:
        domain = str(item.get("domain") or "").lower()
        if domain and domain not in domains:
            domains.append(domain)
    return domains[:5]


def _run_id(disease: str, date_start: str) -> str:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    disease_slug = "".join(ch if ch.isalnum() else "_" for ch in str(disease).lower()).strip("_") or "disease"
    return f"who_{disease_slug}_{date_start[:4]}_{timestamp}"


def _write_json(path: str | Path, payload: Any) -> None:
    Path(path).write_text(json.dumps(_safe(payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_text(path: str | Path, text: str) -> None:
    Path(path).write_text(text, encoding="utf-8")


def _safe(value: Any) -> Any:
    forbidden = {"raw_text", "html", "response_body", "prompt", "completion", "metadata_json", "feature_json", "lesson_json"}
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items() if str(key).lower() not in forbidden}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        return value if len(value) <= 500 else value[:497] + "..."
    return value


def _summary_markdown(summary: WHOAcquisitionResult) -> str:
    return "\n".join(
        [
            "# WHO-first Downloadable / Tabular Acquisition Test",
            "",
            f"- Status: {summary.status}",
            f"- Disease: {summary.disease}",
            f"- Country/region: {summary.country_or_region}",
            f"- Network accessed: {summary.network_accessed}",
            f"- Provider requests: {summary.provider_request_count}",
            f"- Metadata HEAD checks: {summary.metadata_check_request_count}",
            f"- Candidates found: {summary.candidates_found}",
            f"- Pending review rows: {summary.pending_review_rows}",
            "- Limitation: rows require human review; no production prediction is triggered.",
            "",
        ]
    )


def _approved_ingestion_plan_markdown(bundle_paths: dict[str, str]) -> str:
    review_json = bundle_paths.get("review_json", "")
    return (
        "# Approved Ingestion Plan\n\n"
        "- Default state is staging review only.\n"
        "- Only rows explicitly changed to `approval_status=approved` may be ingested.\n"
        "- Ingestion requires `allow_persistence` and a local pilot SQLite URL.\n"
        f"- Review table: `{review_json}`\n"
    )


def _who_safety_audit_markdown(plan: WHOAcquisitionPlan, warnings: list[str]) -> str:
    return (
        "# WHO Acquisition Safety Audit\n\n"
        f"- Live enabled: {plan.live_enabled}.\n"
        f"- Provider request cap: {plan.max_provider_requests}.\n"
        f"- Metadata check cap: {plan.max_metadata_checks}.\n"
        "- Allowed domains: WHO and WHO AFRO only.\n"
        "- No full HTML, raw body, prompt, completion, or secret is saved.\n"
        "- No SSH, remote deployment, Docker startup, or production prediction is performed.\n"
        f"- Warnings: {', '.join(warnings[:6]) if warnings else 'none'}.\n"
    )


__all__ = [
    "WHOAcquisitionPlan",
    "WHOAcquisitionResult",
    "WHODataCandidate",
    "build_who_query_plan",
    "run_who_data_acquisition",
    "safe_summary",
]
