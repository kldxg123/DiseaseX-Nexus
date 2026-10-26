from __future__ import annotations

from crawler_agent.generic_parsers.adapters import (
    parsed_record_to_connector_document,
    parser_result_to_connector_documents,
)
from crawler_agent.generic_parsers.csv_parser import parse_csv_dataset
from crawler_agent.generic_parsers.dispatcher import parse_by_strategy
from crawler_agent.generic_parsers.html_table_parser import parse_html_table
from crawler_agent.generic_parsers.json_api_parser import parse_json_api_response
from crawler_agent.generic_parsers.official_report_parser import parse_official_report
from crawler_agent.generic_parsers.types import ParsedRecord, ParserResult

__all__ = [
    "ParsedRecord",
    "ParserResult",
    "parse_by_strategy",
    "parse_csv_dataset",
    "parse_html_table",
    "parse_json_api_response",
    "parse_official_report",
    "parsed_record_to_connector_document",
    "parser_result_to_connector_documents",
]
