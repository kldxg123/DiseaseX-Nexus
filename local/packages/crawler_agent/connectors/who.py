from __future__ import annotations

import json

from crawler_agent.connectors.base import ConnectorDocument
from crawler_agent.content_extraction import extract_content
from crawler_agent.url_utils import normalize_url


class WhoDiseaseOutbreakNewsConnector:
    connector_name = "who_disease_outbreak_news"

    def parse(self, payload: str, *, base_url: str) -> list[ConnectorDocument]:
        data = json.loads(payload)
        records = data.get("items", [])
        documents: list[ConnectorDocument] = []
        for record in records:
            html = record.get("html") or record.get("summary") or ""
            title = str(record.get("title", "")).strip()
            body = extract_content(html).body_text if "<" in html else str(html).strip()
            documents.append(
                ConnectorDocument(
                    connector_name=self.connector_name,
                    source_name="WHO Disease Outbreak News",
                    url=normalize_url(str(record.get("url", "")), base_url=base_url),
                    title=title or None,
                    body_text=body,
                    content_type="application/json",
                    metadata={"published_at": record.get("published_at")},
                )
            )
        return documents
