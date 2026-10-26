from __future__ import annotations

from dataclasses import replace

from crawler_agent.generic_parsers.csv_parser import parse_csv_dataset
from crawler_agent.generic_parsers.html_table_parser import parse_html_table
from crawler_agent.generic_parsers.json_api_parser import parse_json_api_response
from crawler_agent.generic_parsers.official_report_parser import parse_official_report
from crawler_agent.generic_parsers.types import ParserResult


def parse_by_strategy(
    parser_strategy: str,
    content: str,
    *,
    source_name: str,
    url: str,
    country: str | None = None,
    disease: str | None = None,
    field_mapping: dict[str, str] | None = None,
    records_path: str | None = None,
) -> ParserResult:
    strategy = (parser_strategy or "").strip().lower()
    if strategy == "csv_dataset":
        return parse_csv_dataset(
            content,
            source_name=source_name,
            url=url,
            country=country,
            disease=disease,
            field_mapping=field_mapping,
        )
    if strategy == "html_table":
        return parse_html_table(
            content,
            source_name=source_name,
            url=url,
            country=country,
            disease=disease,
            field_mapping=field_mapping,
        )
    if strategy == "json_api":
        return parse_json_api_response(
            content,
            source_name=source_name,
            url=url,
            country=country,
            disease=disease,
            records_path=records_path,
            field_mapping=field_mapping,
        )
    if strategy == "official_report_page":
        return parse_official_report(
            content,
            source_name=source_name,
            url=url,
            country=country,
            disease=disease,
        )
    if strategy == "official_dataset":
        detected = _detect_dataset_strategy(content)
        return parse_by_strategy(
            detected,
            content,
            source_name=source_name,
            url=url,
            country=country,
            disease=disease,
            field_mapping=field_mapping,
            records_path=records_path,
        )
    if strategy == "dashboard_data_source":
        json_result = parse_json_api_response(
            content,
            source_name=source_name,
            url=url,
            country=country,
            disease=disease,
            records_path=records_path,
            field_mapping=field_mapping,
        )
        if json_result.records:
            return ParserResult(
                "dashboard_data_source",
                [
                    replace(
                        record,
                        parser_strategy="dashboard_data_source",
                        source_type="dashboard_data_source",
                        metadata={**record.metadata, "content_kind": "dashboard_data_source"},
                    )
                    for record in json_result.records
                ],
                json_result.warnings,
                skipped_rows=json_result.skipped_rows,
            )
        return ParserResult(
            "dashboard_data_source",
            [],
            [*json_result.warnings, "Dashboard data source parser supports JSON fixture records only in this MVP."],
            skipped_rows=json_result.skipped_rows,
        )
    raise ValueError(f"Unknown parser strategy: {parser_strategy}.")


def _detect_dataset_strategy(content: str) -> str:
    stripped = (content or "").lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "json_api"
    if stripped.startswith("<"):
        if "<table" in stripped[:4000].lower():
            return "html_table"
        return "official_report_page"
    return "csv_dataset"
