from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from deepseek_web_search_agent.query_request import build_disease_time_query_request
from deepseek_web_search_agent.source_pack_builder import build_source_pack, source_pack_from_report_dir
from deepseek_web_search_agent.structured_table_types import DiseaseTimeQueryRequest, SourceEvidenceItem


@dataclass(frozen=True)
class TableQueryOrchestrationResult:
    query_request: DiseaseTimeQueryRequest
    sources: list[SourceEvidenceItem]
    source_search_summary: dict[str, Any]
    network_accessed: bool
    provider_request_count_total: int
    candidate_acquisition_request_count: int
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_request": self.query_request.to_dict(),
            "sources": [source.to_dict() for source in self.sources],
            "source_search_summary": self.source_search_summary,
            "network_accessed": self.network_accessed,
            "provider_request_count_total": self.provider_request_count_total,
            "candidate_acquisition_request_count": self.candidate_acquisition_request_count,
            "warnings": list(self.warnings),
        }


def build_table_query_source_pack(
    *,
    disease: str,
    country_or_region: str,
    date_start: str,
    date_end: str,
    max_sources: int = 3,
    source_pack_path: str | None = None,
    source_report_dir: str | None = None,
    allow_live_source_search: bool = False,
    allow_network: bool = False,
    terms_reviewed: bool = False,
    manual_reviewed: bool = False,
    providers: str = "auto",
    max_total_provider_requests: int = 3,
    metadata_check_top_n: int = 3,
    max_metadata_check_requests: int = 3,
    output_dir: str = "reports/live_source_search",
) -> TableQueryOrchestrationResult:
    request = build_disease_time_query_request(
        disease=disease,
        country_or_region=country_or_region,
        date_start=date_start,
        date_end=date_end,
        max_sources=max_sources,
        allow_web_search=bool(allow_live_source_search),
    )
    warnings: list[str] = []
    search_summary: dict[str, Any] = {
        "status": "mock_source_pack",
        "live_source_search_executed": False,
        "metadata_check_executed": False,
    }
    provider_requests = 0
    network_accessed = False
    report_dir = source_report_dir
    if allow_live_source_search:
        if not (allow_network and terms_reviewed and manual_reviewed):
            warnings.append("live_source_search_flags_required")
        else:
            from live_source_search_orchestrator import LiveSourceSearchRequest, run_live_source_search
            from live_source_search_orchestrator.orchestrator import report_summary

            live_request = LiveSourceSearchRequest(
                country=country_or_region,
                disease=disease,
                providers=providers,
                max_total_requests=max(1, min(3, int(max_total_provider_requests))),
                dry_run=False,
                allow_network=True,
                terms_reviewed=True,
                manual_reviewed=True,
                metadata_check=True,
                metadata_check_top_n=max(0, min(3, int(metadata_check_top_n))),
                max_metadata_check_requests=max(0, min(3, int(max_metadata_check_requests))),
                output_dir=output_dir,
            )
            live_report = run_live_source_search(live_request)
            search_summary = report_summary(live_report)
            provider_requests = int(search_summary.get("request_count_total") or 0)
            network_accessed = provider_requests > 0
            report_dir = live_report.output_dir
            warnings.extend(search_summary.get("warnings") or [])
    sources = build_source_pack(request, source_pack_path=source_pack_path, report_dir=report_dir)
    if not sources and source_report_dir:
        sources = source_pack_from_report_dir(source_report_dir)
    if not sources:
        sources = build_source_pack(request)
        warnings.append("source_pack_fallback_to_mock")
    return TableQueryOrchestrationResult(
        query_request=request,
        sources=sources[: request.max_sources],
        source_search_summary=search_summary,
        network_accessed=network_accessed,
        provider_request_count_total=provider_requests,
        candidate_acquisition_request_count=0,
        warnings=sorted(set(warnings)),
    )


__all__ = ["TableQueryOrchestrationResult", "build_table_query_source_pack"]
