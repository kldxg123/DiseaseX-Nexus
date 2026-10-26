from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from common.models import SourceRegistry
from crawler_agent.content_extraction import extract_content
from crawler_agent.http_fetcher import HttpFetcher
from crawler_agent.raw_document_store import RawDocumentStore
from crawler_agent.robots import RobotsPolicy, StaticRobotsPolicy
from crawler_agent.url_utils import normalize_url


@dataclass(frozen=True)
class CrawlResult:
    raw_document_id: int
    content_hash: str
    created: bool
    title: str | None


class CrawlerService:
    def __init__(
        self,
        session: Session,
        *,
        fetcher: HttpFetcher,
        robots_policy: RobotsPolicy | None = None,
        user_agent: str = "infectious-intel-local/0.1",
    ):
        self.session = session
        self.fetcher = fetcher
        self.robots_policy = robots_policy or StaticRobotsPolicy(is_allowed=True)
        self.user_agent = user_agent

    def crawl_url(self, *, source_id: int, url: str) -> CrawlResult:
        source = self.session.get(SourceRegistry, source_id)
        if source is None:
            raise ValueError("source_id must identify an existing source.")
        if source.status != "active":
            raise ValueError("source must be active before crawling.")
        if source.robots_allowed is False:
            raise ValueError("source robots policy does not allow crawling.")

        normalized_url = normalize_url(url, base_url=source.base_url)
        if not self.robots_policy.allowed(normalized_url, self.user_agent):
            raise ValueError("robots policy does not allow crawling.")

        fetched_page = self.fetcher.fetch(normalized_url)
        extracted_content = extract_content(fetched_page.text)
        if not extracted_content.body_text:
            raise ValueError("fetched page did not contain extractable text.")

        result = RawDocumentStore(self.session).save(
            source=source,
            fetched_page=fetched_page,
            extracted_content=extracted_content,
        )
        return CrawlResult(
            raw_document_id=result.document.id,
            content_hash=result.document.content_hash,
            created=result.created,
            title=result.document.title,
        )
