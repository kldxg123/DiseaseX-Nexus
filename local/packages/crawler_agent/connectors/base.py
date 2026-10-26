from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from crawler_agent.content_extraction import extract_content
from crawler_agent.url_utils import normalize_url


@dataclass(frozen=True)
class ConnectorDocument:
    connector_name: str
    source_name: str
    url: str
    title: str | None
    body_text: str
    content_type: str
    metadata: dict


class OfficialConnector(Protocol):
    connector_name: str

    def parse(self, payload: str, *, base_url: str) -> list[ConnectorDocument]:
        ...


@dataclass(frozen=True)
class ConnectorSpec:
    name: str
    display_name: str
    base_url: str
    source_type: str
    connector: OfficialConnector


def text_from_record(record: dict, fields: list[str]) -> str:
    values = [str(record.get(field, "")).strip() for field in fields]
    return " ".join(value for value in values if value).strip()


def document_from_html(
    *,
    connector_name: str,
    source_name: str,
    url: str,
    html: str,
    metadata: dict | None = None,
) -> ConnectorDocument:
    extracted = extract_content(html)
    return ConnectorDocument(
        connector_name=connector_name,
        source_name=source_name,
        url=normalize_url(url),
        title=extracted.title,
        body_text=extracted.body_text,
        content_type="text/html",
        metadata=metadata or {},
    )
