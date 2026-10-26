from __future__ import annotations

import hashlib
from urllib.parse import urlsplit, urlunsplit

from crawler_agent.country_terms import get_country_query_terms, normalize_country

from live_source_search_orchestrator.source_type_classifier import DATA_KEYWORDS, classify_source_type
from live_source_search_orchestrator.types import DiseaseAliasSet, NormalizedSourceCandidate, ProviderSearchResult

MAX_SNIPPET_CHARS = 220
AUTHORITY_DOMAINS = ("cdc.gov", "who.int", "ecdc.europa.eu", "data.gov", "catalog.data.gov")


def normalize_provider_results(
    provider_results: list[ProviderSearchResult],
    *,
    country: str,
    aliases: DiseaseAliasSet,
) -> list[NormalizedSourceCandidate]:
    candidates: list[NormalizedSourceCandidate] = []
    for provider_result in provider_results:
        for row in provider_result.search_results:
            candidate = normalize_search_result(row, provider=provider_result.provider, country=country, aliases=aliases)
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def normalize_search_result(
    row: dict,
    *,
    provider: str,
    country: str,
    aliases: DiseaseAliasSet,
) -> NormalizedSourceCandidate | None:
    normalized_url = _normalize_url(str(row.get("url") or ""))
    if not normalized_url:
        return None
    domain = (urlsplit(normalized_url).hostname or "").lower()
    title = _clean_text(row.get("title") or row.get("source_name") or domain)
    snippet = _truncate(_clean_text(row.get("snippet") or ""))
    source_name = _clean_text(row.get("source_name") or title or domain)
    text = f"{title} {snippet or ''} {normalized_url}".lower()
    country_terms = get_country_query_terms(country) or [normalize_country(country) or country]
    country_mentions = _matches(country_terms, text)
    disease_mentions = _matches(aliases.aliases, text)
    data_keywords = sorted(term for term in DATA_KEYWORDS if term in text)
    source_type = classify_source_type(title=title, url=normalized_url, snippet=snippet)
    warnings: list[str] = []
    if aliases.unknown_disease_alias_expansion:
        warnings.append("low_alias_confidence")
    return NormalizedSourceCandidate(
        candidate_id=_candidate_id(normalized_url),
        normalized_title=title[:160],
        normalized_url=normalized_url,
        domain=domain,
        provider=provider,
        source_name=source_name[:120],
        snippet_summary=snippet,
        country_mentions=country_mentions,
        disease_mentions=disease_mentions,
        data_keywords=data_keywords,
        authority_domain=_authority_domain(domain),
        candidate_source_type=source_type.source_type,
        parser_strategy=source_type.parser_strategy,
        raw_rank=_safe_int(row.get("rank")),
        source_type_confidence=source_type.confidence,
        evidence_summary=_evidence_summary(provider, disease_mentions, country_mentions, data_keywords),
        recommended_action=source_type.recommended_next_action,
        merged_from_providers=[provider],
        warnings=warnings,
    )


def _normalize_url(url: str) -> str | None:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", "", ""))


def _clean_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _truncate(value: str | None) -> str | None:
    if not value:
        return None
    return value if len(value) <= MAX_SNIPPET_CHARS else value[:MAX_SNIPPET_CHARS] + "...[truncated]"


def _matches(terms: list[str], text: str) -> list[str]:
    return [term for term in terms if term and term.lower() in text][:10]


def _authority_domain(domain: str) -> bool:
    return domain.endswith(".gov") or any(domain == item or domain.endswith(f".{item}") for item in AUTHORITY_DOMAINS)


def _candidate_id(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _safe_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _evidence_summary(provider: str, disease: list[str], country: list[str], data: list[str]) -> str:
    parts = [f"provider={provider}"]
    if disease:
        parts.append(f"disease_terms={len(disease)}")
    if country:
        parts.append(f"country_terms={len(country)}")
    if data:
        parts.append(f"data_terms={','.join(data[:4])}")
    return "; ".join(parts)


__all__ = ["MAX_SNIPPET_CHARS", "normalize_provider_results", "normalize_search_result"]
