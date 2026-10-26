from __future__ import annotations

from crawler_agent.disease_terms import expand_disease_aliases as existing_aliases

from live_source_search_orchestrator.types import DiseaseAliasSet

EXTRA_ALIASES: dict[str, list[str]] = {
    "influenza": ["influenza", "flu", "seasonal influenza", "ILI", "influenza-like illness"],
    "norovirus": ["norovirus", "NoroSTAT", "acute gastroenteritis", "viral gastroenteritis"],
    "ebola": ["ebola", "ebola virus disease", "EVD"],
    "cholera": ["cholera", "vibrio cholerae", "acute watery diarrhea"],
    "measles": ["measles", "rubeola", "MMR", "measles outbreak"],
    "mpox": ["mpox", "monkeypox"],
    "dengue": ["dengue", "dengue fever"],
    "covid": ["covid", "covid-19", "SARS-CoV-2"],
}


def expand_disease_aliases(disease: str) -> DiseaseAliasSet:
    raw = " ".join(str(disease or "").split())
    normalized = raw.lower()
    aliases = _unique(list(existing_aliases(raw)) + EXTRA_ALIASES.get(normalized, []))
    unknown = normalized not in EXTRA_ALIASES and aliases == [raw]
    if unknown:
        aliases = _unique(
            [
                raw,
                f"{raw} surveillance",
                f"{raw} outbreak data",
                f"{raw} dashboard",
                f"{raw} dataset",
            ]
        )
    query_terms = _unique(
        aliases
        + [
            f"{aliases[0]} surveillance data" if aliases else "surveillance data",
            f"{aliases[0]} public health dataset" if aliases else "public health dataset",
        ]
    )
    return DiseaseAliasSet(
        disease=raw,
        aliases=aliases,
        query_terms=query_terms,
        alias_confidence=0.45 if unknown else 0.9,
        unknown_disease_alias_expansion=unknown,
    )


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = " ".join(str(value or "").split())
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            result.append(cleaned)
    return result


__all__ = ["EXTRA_ALIASES", "expand_disease_aliases"]
