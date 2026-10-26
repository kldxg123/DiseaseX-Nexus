from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlunsplit

import httpx
from sqlalchemy.orm import Session

from crawler_agent.gdelt import GdeltArticle, build_gdelt_url, parse_gdelt_articles
from crawler_agent.source_registry import SourceRegistryService
from crawler_agent.source_scoring import calculate_source_score


@dataclass(frozen=True)
class CandidateSource:
    name: str
    base_url: str
    article_count: int
    sample_titles: tuple[str, ...]
    source_score: float
    source_payload: dict[str, Any]


def base_url_from_domain(domain: str) -> str:
    return urlunsplit(("https", domain.lower(), "/", "", ""))


def candidates_from_gdelt_articles(
    *,
    articles: list[GdeltArticle],
    country: str | None,
    disease: str,
    query_url: str | None = None,
) -> list[CandidateSource]:
    grouped: dict[str, list[GdeltArticle]] = defaultdict(list)
    for article in articles:
        grouped[article.domain].append(article)

    candidates: list[CandidateSource] = []
    for domain, domain_articles in sorted(grouped.items()):
        count = len(domain_articles)
        language_counts = Counter(article.language for article in domain_articles if article.language)
        common_language = language_counts.most_common(1)[0][0] if language_counts else None
        relevance_score = min(1.0, 0.35 + count * 0.15)
        freshness_score = min(1.0, 0.45 + count * 0.10)
        language_match = 0.75 if common_language else 0.55
        score = calculate_source_score(
            authority_score=0.35,
            relevance_score=relevance_score,
            freshness_score=freshness_score,
            structured_data_score=0.55,
            historical_success_score=0.35,
            language_country_match=language_match,
            legal_or_robots_penalty=0.0,
        )
        sample_titles = tuple(
            article.title for article in domain_articles[:3] if article.title is not None
        )
        payload = {
            "name": domain_articles[0].source_name or domain,
            "base_url": base_url_from_domain(domain),
            "country_scope": [country.strip()] if country else ["global"],
            "disease_scope": [disease.strip()],
            "source_type": "news_candidate",
            "authority_score": 0.35,
            "relevance_score": relevance_score,
            "freshness_score": freshness_score,
            "reliability_score": 0.45,
            "structured_data_score": 0.55,
            "historical_success_score": 0.35,
            "language_country_match": language_match,
            "legal_or_robots_penalty": 0.0,
            "robots_allowed": None,
            "parser_type": "gdelt_candidate_metadata",
            "metadata_json": {
                "connector": "gdelt_candidate_discovery",
                "allowed_use": "metadata_registry_only",
                "requires_network_for_fetching": True,
                "article_count": count,
                "sample_titles": list(sample_titles),
                "source_score": score,
                "query_url": query_url,
            },
            "status": "pending_review",
        }
        candidates.append(
            CandidateSource(
                name=payload["name"],
                base_url=payload["base_url"],
                article_count=count,
                sample_titles=sample_titles,
                source_score=score,
                source_payload=payload,
            )
        )
    return candidates


class GdeltDiscoveryService:
    def __init__(
        self,
        session: Session,
        *,
        transport: httpx.BaseTransport | None = None,
        allow_network: bool = False,
        timeout_seconds: float = 30.0,
    ):
        self.session = session
        self.transport = transport
        self.allow_network = allow_network
        self.timeout_seconds = timeout_seconds

    def fetch_mockable_articles(
        self,
        *,
        country: str | None,
        disease: str,
        lookback_days: int,
    ) -> list[GdeltArticle]:
        if self.transport is None and not self.allow_network:
            raise RuntimeError("GDELT discovery fetch requires mock transport or allow_network=True.")
        url = build_gdelt_url(country=country, disease=disease, lookback_days=lookback_days)
        with httpx.Client(transport=self.transport, timeout=self.timeout_seconds) as client:
            response = client.get(url)
            response.raise_for_status()
            return parse_gdelt_articles(response.text)

    def discover_candidates_from_articles(
        self,
        *,
        articles: list[GdeltArticle],
        country: str | None,
        disease: str,
        query_url: str | None = None,
    ) -> list[CandidateSource]:
        return candidates_from_gdelt_articles(
            articles=articles,
            country=country,
            disease=disease,
            query_url=query_url,
        )

    def discover_candidates(
        self,
        *,
        country: str | None,
        disease: str,
        lookback_days: int,
    ) -> list[CandidateSource]:
        query_url = build_gdelt_url(country=country, disease=disease, lookback_days=lookback_days)
        articles = self.fetch_mockable_articles(
            country=country,
            disease=disease,
            lookback_days=lookback_days,
        )
        return self.discover_candidates_from_articles(
            articles=articles,
            country=country,
            disease=disease,
            query_url=query_url,
        )

    def discover_and_upsert_candidates(
        self,
        *,
        country: str | None,
        disease: str,
        lookback_days: int,
    ) -> list[CandidateSource]:
        candidates = self.discover_candidates(
            country=country,
            disease=disease,
            lookback_days=lookback_days,
        )
        registry = SourceRegistryService(self.session)
        for candidate in candidates:
            registry.upsert_source(**candidate.source_payload)
        return candidates
