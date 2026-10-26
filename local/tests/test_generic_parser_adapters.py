from __future__ import annotations

import json

from crawler_agent.connectors import ConnectorDocument
from crawler_agent.generic_parsers import parse_by_strategy, parser_result_to_connector_documents


def test_parser_result_to_connector_documents_preserves_safe_structured_metadata() -> None:
    result = parse_by_strategy(
        "csv_dataset",
        "week,region,cases,total tests\n2025-W01,US,10,100\n",
        source_name="Generic CSV Fixture",
        url="https://local.invalid/generic-csv",
        country="United States",
        disease="influenza",
    )
    documents = parser_result_to_connector_documents(result)

    assert len(documents) == 1
    document = documents[0]
    assert isinstance(document, ConnectorDocument)
    assert document.metadata["generic_parser"] is True
    assert document.metadata["parser_strategy"] == "csv_dataset"
    assert document.metadata["structured_event"]["cases"] == 10
    assert document.metadata["structured_fields"]["tests"] == 100
    rendered_metadata = json.dumps(document.metadata, ensure_ascii=False).lower()
    assert "raw_text" not in rendered_metadata
    assert "<html" not in rendered_metadata
