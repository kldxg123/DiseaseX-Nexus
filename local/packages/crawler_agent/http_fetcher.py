from __future__ import annotations

from dataclasses import dataclass

import httpx

from crawler_agent.url_utils import normalize_url


@dataclass(frozen=True)
class FetchedPage:
    url: str
    final_url: str
    status_code: int
    content_type: str | None
    text: str


class HttpFetcher:
    def __init__(
        self,
        *,
        user_agent: str = "infectious-intel-local/0.1",
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ):
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def fetch(self, url: str) -> FetchedPage:
        normalized = normalize_url(url)
        with httpx.Client(
            follow_redirects=True,
            timeout=self.timeout_seconds,
            transport=self.transport,
            headers={"User-Agent": self.user_agent},
        ) as client:
            response = client.get(normalized)
            response.raise_for_status()
            return FetchedPage(
                url=normalized,
                final_url=normalize_url(str(response.url)),
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                text=response.text,
            )
