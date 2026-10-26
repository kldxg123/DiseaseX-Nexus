from __future__ import annotations

import re

from crawler_agent.connectors.base import ConnectorDocument
from crawler_agent.generic_parsers.types import ParsedRecord, ParserResult
from crawler_agent.url_utils import normalize_url


def parsed_record_to_connector_document(record: ParsedRecord) -> ConnectorDocument:
    metadata = {
        "parser_strategy": record.parser_strategy,
        "parse_mode": record.parser_strategy,
        "generic_parser": True,
        "source_type": record.source_type,
        "country_detected": record.country_detected,
        "disease_detected": record.disease_detected,
        "structured_fields": record.structured_fields,
        "structured_event": record.structured_fields,
        **record.metadata,
    }
    return ConnectorDocument(
        connector_name=_connector_name(record),
        source_name=record.source_name,
        url=normalize_url(record.url),
        title=record.title,
        body_text=record.text,
        content_type=_content_type(record.parser_strategy),
        metadata=metadata,
    )


def parser_result_to_connector_documents(result: ParserResult) -> list[ConnectorDocument]:
    return [parsed_record_to_connector_document(record) for record in result.records]


def _connector_name(record: ParsedRecord) -> str:
    value = str(record.metadata.get("connector_name") or record.source_name or record.parser_strategy)
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug or "generic_parser"


def _content_type(parser_strategy: str) -> str:
    if parser_strategy == "csv_dataset":
        return "text/csv"
    if parser_strategy == "json_api" or parser_strategy == "dashboard_data_source":
        return "application/json"
    return "text/html"
