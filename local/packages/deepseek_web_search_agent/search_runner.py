from __future__ import annotations

from deepseek_web_search_agent.types import GeneralWebSearchRequest, GeneralWebSearchResult, QueryPlan
from deepseek_web_search_agent.web_search_providers import GeneralWebSearchProvider


def run_search_plan(
    plan: QueryPlan,
    *,
    provider: GeneralWebSearchProvider,
    allow_network: bool = False,
    max_results_per_query: int = 8,
    max_total_http_requests: int = 20,
) -> tuple[list[GeneralWebSearchResult], dict]:
    results: list[GeneralWebSearchResult] = []
    seen_urls: set[str] = set()
    request_count = 0
    for planned in plan.search_queries:
        if request_count >= max_total_http_requests:
            break
        request_count += 1
        query_results = provider.search(
            GeneralWebSearchRequest(
                query=planned.query_text,
                disease=plan.disease,
                country=plan.country,
                max_results=max_results_per_query,
                allow_network=allow_network,
            )
        )
        for result in query_results:
            key = result.url.strip().lower()
            if key in seen_urls:
                continue
            seen_urls.add(key)
            results.append(result)
    domains: dict[str, int] = {}
    for result in results:
        domains[result.domain] = domains.get(result.domain, 0) + 1
    return results, {
        "search_request_count": request_count,
        "results_seen": len(results),
        "domain_count": len(domains),
        "domains": domains,
        "candidate_urls_accessed": False,
        "follow_links": False,
    }
