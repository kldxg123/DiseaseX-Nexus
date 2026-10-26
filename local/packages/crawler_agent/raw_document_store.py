from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from common.models import RawDocument, SourceRegistry
from crawler_agent.content_extraction import ExtractedContent
from crawler_agent.http_fetcher import FetchedPage


def calculate_content_hash(title: str | None, body_text: str) -> str:
    payload = f"{title or ''}\n{body_text}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class StoredDocumentResult:
    document: RawDocument
    created: bool


class RawDocumentStore:
    def __init__(self, session: Session):
        self.session = session

    def save(
        self,
        *,
        source: SourceRegistry,
        fetched_page: FetchedPage,
        extracted_content: ExtractedContent,
    ) -> StoredDocumentResult:
        content_hash = calculate_content_hash(
            extracted_content.title,
            extracted_content.body_text,
        )
        existing = self.session.scalar(
            select(RawDocument).where(RawDocument.content_hash == content_hash)
        )
        if existing is not None:
            return StoredDocumentResult(document=existing, created=False)

        document = RawDocument(
            source_registry_id=source.id,
            original_url=fetched_page.url,
            normalized_url=fetched_page.final_url,
            title=extracted_content.title,
            body_text=extracted_content.body_text,
            content_hash=content_hash,
            status_code=fetched_page.status_code,
            content_type=fetched_page.content_type,
            fetched_at=datetime.now(UTC),
            metadata_json={"fetcher": "httpx", "parser": "html_parser"},
        )
        self.session.add(document)
        self.session.flush()
        return StoredDocumentResult(document=document, created=True)
