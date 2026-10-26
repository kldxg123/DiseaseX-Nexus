from __future__ import annotations

import csv
import io
import json

from crawler_agent.connectors.base import ConnectorDocument, text_from_record
from crawler_agent.url_utils import normalize_url


class EcdcSurveillanceAtlasConnector:
    connector_name = "ecdc_surveillance_atlas"

    def parse(self, payload: str, *, base_url: str) -> list[ConnectorDocument]:
        reader = csv.DictReader(io.StringIO(payload))
        documents: list[ConnectorDocument] = []
        for index, record in enumerate(reader, start=1):
            title = text_from_record(record, ["disease", "country", "year"])
            body = text_from_record(record, ["disease", "country", "indicator", "value", "unit"])
            documents.append(
                ConnectorDocument(
                    connector_name=self.connector_name,
                    source_name="ECDC Surveillance Atlas",
                    url=normalize_url(f"?row={index}", base_url=base_url),
                    title=title or "ECDC surveillance record",
                    body_text=body,
                    content_type="text/csv",
                    metadata={"row_number": index},
                )
            )
        return documents


class EcdcErvissConnector:
    connector_name = "ecdc_erviss"

    def parse(self, payload: str, *, base_url: str) -> list[ConnectorDocument]:
        data = json.loads(payload)
        records = data.get("records", [])
        documents: list[ConnectorDocument] = []
        for index, record in enumerate(records, start=1):
            title = text_from_record(record, ["pathogen", "region", "week"])
            body = text_from_record(record, ["pathogen", "region", "trend", "positivity"])
            documents.append(
                ConnectorDocument(
                    connector_name=self.connector_name,
                    source_name="ECDC ERVISS",
                    url=normalize_url(f"?record={index}", base_url=base_url),
                    title=title or "ECDC ERVISS record",
                    body_text=body,
                    content_type="application/json",
                    metadata={"record_number": index},
                )
            )
        return documents
