from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from crawler_agent.country_terms import normalize_country_code
from crawler_agent.disease_terms import expand_disease_aliases
from crawler_agent.url_utils import normalize_url

GDELT_DOC_API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


@dataclass(frozen=True)
class GdeltArticle:
    url: str
    title: str | None
    domain: str
    source_name: str
    language: str | None = None
    source_country: str | None = None


def build_gdelt_query(country: str | None, disease: str) -> str:
    disease_query = " OR ".join(f'"{alias}"' for alias in expand_disease_aliases(disease))
    country_code = normalize_country_code(country)
    if country_code:
        return f"({disease_query}) sourcecountry:{country_code}"
    return f"({disease_query})"


def build_gdelt_url(
    *,
    country: str | None,
    disease: str,
    lookback_days: int,
    max_records: int = 250,
) -> str:
    safe_lookback = max(1, min(int(lookback_days), 365))
    safe_max_records = max(1, min(int(max_records), 250))
    params = {
        "query": build_gdelt_query(country, disease),
        "mode": "ArtList",
        "format": "json",
        "sort": "hybridrel",
        "maxrecords": str(safe_max_records),
        "timespan": f"{safe_lookback}d",
    }
    return f"{GDELT_DOC_API_URL}?{urlencode(params)}"


def domain_from_url(url: str) -> str:
    normalized = normalize_url(url)
    hostname = urlsplit(normalized).hostname or ""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname.lower()


def parse_gdelt_articles(payload: str | dict[str, Any]) -> list[GdeltArticle]:
    data = json.loads(payload) if isinstance(payload, str) else payload
    articles = data.get("articles", [])
    if not isinstance(articles, list):
        raise ValueError("GDELT mock payload must contain an articles list.")

    parsed: list[GdeltArticle] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        url = article.get("url") or article.get("sourceurl")
        if not isinstance(url, str) or not url.strip():
            continue
        try:
            domain = str(article.get("domain") or domain_from_url(url)).lower()
            if domain.startswith("www."):
                domain = domain[4:]
            normalized_url = normalize_url(url)
        except ValueError:
            continue
        source_name = str(article.get("sourceCommonName") or domain)
        title = article.get("title")
        parsed.append(
            GdeltArticle(
                url=normalized_url,
                title=str(title) if title else None,
                domain=domain,
                source_name=source_name,
                language=str(article["language"]) if article.get("language") else None,
                source_country=(
                    str(article["sourceCountry"]) if article.get("sourceCountry") else None
                ),
            )
        )
    return parsed
