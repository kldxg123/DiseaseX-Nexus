from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from crawler_agent.country_terms import get_country_query_terms, normalize_country
from crawler_agent.disease_terms import get_disease_aliases
from crawler_agent.source_scoring import calculate_source_score, clamp_score
from source_discovery_agent.types import SearchResult, SourceCandidate

STRUCTURED_TERMS = ("data", "dataset", "table", "surveillance", "dashboard", "csv", "api")
NEWS_HINTS = ("news", "times", "post", "journal", "tribune", "media", "press")
OFFICIAL_DOMAINS = ("cdc.gov", "data.cdc.gov", "who.int", "ecdc.europa.eu")


def infer_source_type(result: SearchResult) -> str:
    domain = _domain(result.url)
    text = _haystack(result)
    if domain in OFFICIAL_DOMAINS or domain.endswith(".gov"):
        if _structured_matches(text):
            return "official_dataset"
        return "official_page"
    if "who.int" in domain or "ecdc.europa.eu" in domain:
        return "official_page"
    if any(hint in domain for hint in NEWS_HINTS):
        return "news"
    if _structured_matches(text):
        return "candidate_dataset"
    return "candidate_page"


def evaluate_search_result(
    result: SearchResult,
    *,
    country: str,
    disease: str,
) -> SourceCandidate | None:
    domain = _domain(result.url)
    if not domain:
        return None
    authority = _authority_score(domain)
    relevance, matched_terms = _relevance_score(result, country=country, disease=disease)
    structured = _structured_score(result)
    freshness = _freshness_score(result)
    legal_penalty = _legal_or_robots_penalty(domain)
    country_name = normalize_country(country) or country
    source_type = infer_source_type(result)
    language_country_match = 0.9 if _country_match(result, country) else 0.55
    source_score = calculate_source_score(
        authority_score=authority,
        relevance_score=relevance,
        freshness_score=freshness,
        structured_data_score=structured,
        historical_success_score=0.45,
        language_country_match=language_country_match,
        legal_or_robots_penalty=legal_penalty,
    )
    return SourceCandidate(
        name=_candidate_name(result, domain),
        base_url=_safe_base_url(result.url),
        domain=domain,
        country_scope=country_name,
        disease_scope=disease,
        source_type=source_type,
        authority_score=authority,
        relevance_score=relevance,
        structured_data_score=structured,
        freshness_score=freshness,
        legal_or_robots_penalty=legal_penalty,
        source_score=source_score,
        evidence={
            "domain": domain,
            "rank": result.rank,
            "matched_terms": matched_terms[:12],
            "snippet_length": len(result.snippet or ""),
            "structured_terms": _matched_structured_terms(result),
            "requires_human_review": True,
        },
        requires_human_review=True,
    )


def rank_source_candidates(candidates: list[SourceCandidate]) -> list[SourceCandidate]:
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.source_score,
            candidate.authority_score,
            candidate.structured_data_score,
            -int(candidate.evidence.get("rank") or 9999),
        ),
        reverse=True,
    )


def _candidate_name(result: SearchResult, domain: str) -> str:
    if result.source_name:
        return result.source_name.strip()
    if result.title:
        return result.title.strip()[:120]
    return domain


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _safe_base_url(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", "", ""))


def _haystack(result: SearchResult) -> str:
    return " ".join(
        item
        for item in (result.title or "", result.snippet or "", result.url or "", result.source_name or "")
        if item
    ).lower()


def _authority_score(domain: str) -> float:
    if domain in {"cdc.gov", "www.cdc.gov", "data.cdc.gov"} or domain.endswith(".cdc.gov"):
        return 0.97
    if domain in {"who.int", "www.who.int"} or domain.endswith(".who.int"):
        return 0.93
    if domain == "ecdc.europa.eu" or domain.endswith(".ecdc.europa.eu"):
        return 0.92
    if domain.endswith(".gov"):
        return 0.85
    if domain.endswith(".edu"):
        return 0.7
    if any(hint in domain for hint in NEWS_HINTS):
        return 0.45
    return 0.25


def _relevance_score(result: SearchResult, *, country: str, disease: str) -> tuple[float, list[str]]:
    text = _haystack(result)
    disease_aliases = _aliases(disease)
    country_terms = get_country_query_terms(country) or [normalize_country(country) or country]
    matched_disease = [term for term in disease_aliases if term and term.lower() in text]
    matched_country = [term for term in country_terms if term and term.lower() in text]
    score = 0.0
    if matched_disease:
        score += 0.65
    if matched_country:
        score += 0.25
    if "outbreak" in text or "surveillance" in text:
        score += 0.10
    return clamp_score(score), matched_disease[:8] + matched_country[:4]


def _structured_score(result: SearchResult) -> float:
    matches = _matched_structured_terms(result)
    if not matches:
        return 0.15
    return clamp_score(0.2 + 0.15 * len(matches))


def _structured_matches(text: str) -> bool:
    return any(term in text for term in STRUCTURED_TERMS)


def _matched_structured_terms(result: SearchResult) -> list[str]:
    text = _haystack(result)
    return [term for term in STRUCTURED_TERMS if term in text]


def _freshness_score(result: SearchResult) -> float:
    if result.published_at:
        return 0.75
    text = _haystack(result)
    if "weekly" in text or "updated" in text:
        return 0.65
    if "surveillance" in text or "dashboard" in text:
        return 0.55
    return 0.4


def _legal_or_robots_penalty(domain: str) -> float:
    if any(domain == official or domain.endswith(f".{official}") for official in OFFICIAL_DOMAINS):
        return 0.05
    if domain.endswith(".gov"):
        return 0.08
    if any(hint in domain for hint in NEWS_HINTS):
        return 0.22
    return 0.18


def _country_match(result: SearchResult, country: str) -> bool:
    text = _haystack(result)
    terms = get_country_query_terms(country) or [normalize_country(country) or country]
    return any(term and term.lower() in text for term in terms)


def _aliases(disease: str) -> list[str]:
    aliases = get_disease_aliases(disease)
    if isinstance(aliases, list):
        return [str(item).strip() for item in aliases if str(item).strip()]
    return [disease]
