from __future__ import annotations

from crawler_agent.country_terms import get_country_query_terms, normalize_country
from crawler_agent.disease_terms import get_disease_aliases
from source_discovery_agent.types import SearchQuery


def build_discovery_queries(country: str, disease: str, *, max_queries: int = 5) -> list[SearchQuery]:
    country_name = normalize_country(country) or (country or "").strip() or "global"
    disease_aliases = _aliases(disease)
    country_terms = get_country_query_terms(country) or [country_name]
    primary_disease = disease_aliases[0] if disease_aliases else (disease or "").strip()
    primary_country = country_terms[0] if country_terms else country_name

    if _is_united_states(country) and _is_norovirus(disease_aliases):
        candidates = [
            f"{primary_country} {primary_disease} outbreak data",
            f"{primary_country} {primary_disease} surveillance data",
            "CDC norovirus outbreak data",
            "norovirus NoroSTAT data",
            "norovirus NORS data",
            f"{primary_country} {primary_disease} dataset",
        ]
    else:
        candidates = [
            f"{primary_country} {primary_disease} outbreak data",
            f"{primary_country} {primary_disease} surveillance data",
            f"{primary_country} {primary_disease} dataset",
            f"{primary_disease} outbreak dashboard {primary_country}",
            f"{primary_disease} public health data {primary_country}",
        ]

    queries: list[SearchQuery] = []
    seen: set[str] = set()
    for query_text in candidates:
        normalized = " ".join(query_text.split())
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        queries.append(
            SearchQuery(
                country=country_name,
                disease=primary_disease or (disease or "").strip(),
                query_text=normalized,
                language="en",
                metadata={
                    "source": "offline_query_builder",
                    "requires_network_for_execution": True,
                    "network_executed": False,
                },
            )
        )
        if len(queries) >= max(1, int(max_queries)):
            break
    return queries


def _aliases(disease: str) -> list[str]:
    aliases = get_disease_aliases(disease)
    if isinstance(aliases, list):
        return [item for item in aliases if str(item).strip()]
    return [(disease or "").strip()]


def _is_united_states(country: str) -> bool:
    normalized = (normalize_country(country) or country or "").strip().lower()
    return normalized == "united states"


def _is_norovirus(aliases: list[str]) -> bool:
    return any("norovirus" in alias.lower() or "norostat" in alias.lower() for alias in aliases)
