from __future__ import annotations

from urllib.parse import urlparse

from web_search_providers.provider_policy import clamp_request
from web_search_providers.types import (
    WebSearchProviderCapabilities,
    WebSearchProviderResult,
    WebSearchRequest,
    WebSearchResult,
)


class MockWebSearchProvider:
    name = "mock"
    capabilities = WebSearchProviderCapabilities(
        provider_name="mock",
        supports_search=True,
        requires_api_key=False,
        supports_domain_filter=True,
        supports_time_range=True,
        supports_country_filter=True,
        max_safe_results=8,
        implemented_live=True,
    )

    def search(self, request: WebSearchRequest) -> WebSearchProviderResult:
        req = clamp_request(request)
        disease = req.disease or _infer_disease(req.query)
        country = req.country or _infer_country(req.query)
        slug = "-".join(str(disease).lower().split()) or "disease"
        rows = [
            (
                f"{country} {disease} weekly surveillance CSV dataset",
                f"https://public-health.example.org/{slug}/weekly-surveillance.csv",
                f"Weekly {disease} surveillance data for {country}; CSV table with cases by week.",
                "csv_dataset",
            ),
            (
                f"{country} {disease} official HTML table",
                f"https://health.example.gov/{slug}/weekly-table",
                f"Official public health HTML table for {disease} with week, cases, deaths.",
                "html_table",
            ),
            (
                f"{country} {disease} dashboard API data",
                f"https://data.example.gov/{slug}/api",
                f"Dashboard and API metadata for {disease} surveillance data.",
                "json_api",
            ),
        ]
        results = [
            WebSearchResult(
                provider_name=self.name,
                query=req.query,
                rank=index + 1,
                title=title,
                url=url,
                domain=urlparse(url).netloc.lower(),
                snippet=snippet,
                source_type_hint=hint,
                safe_metadata={"mock": True},
            )
            for index, (title, url, snippet, hint) in enumerate(rows[: req.max_results])
        ]
        return WebSearchProviderResult(
            provider_name=self.name,
            query=req.query,
            results=results,
            request_count=0,
            network_accessed=False,
        )


def _infer_disease(query: str) -> str:
    words = [word for word in str(query or "").split() if word.isalpha()]
    return words[0] if words else "disease"


def _infer_country(query: str) -> str:
    return "United States" if "United States" in str(query or "") else "country"
