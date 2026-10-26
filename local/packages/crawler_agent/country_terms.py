from __future__ import annotations

COUNTRY_CODES: dict[str, str | None] = {
    "united states": "US",
    "usa": "US",
    "us": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "america": "US",
    "美国": "US",
    "china": "CN",
    "cn": "CN",
    "中国": "CN",
    "hong kong": "HK",
    "united kingdom": "UK",
    "great britain": "GB",
    "gb": "GB",
    "uk": "UK",
    "england": "GB",
    "英国": "GB",
    "japan": "JP",
    "jp": "JP",
    "日本": "JP",
    "germany": "DE",
    "de": "DE",
    "德国": "DE",
    "france": "FR",
    "fr": "FR",
    "法国": "FR",
    "global": None,
    "all": None,
}

COUNTRY_NAMES: dict[str, str] = {
    "US": "United States",
    "GB": "United Kingdom",
    "UK": "United Kingdom",
    "CN": "China",
    "HK": "Hong Kong",
    "JP": "Japan",
    "DE": "Germany",
    "FR": "France",
}

COUNTRY_QUERY_TERMS: dict[str, list[str]] = {
    "US": ["United States", "US", "USA", "U.S.", "America", "美国"],
    "GB": ["United Kingdom", "UK", "GB", "Great Britain", "England", "英国"],
    "UK": ["United Kingdom", "UK", "GB", "Great Britain", "England", "英国"],
    "CN": ["China", "CN", "中国"],
    "JP": ["Japan", "JP", "日本"],
    "DE": ["Germany", "DE", "德国"],
    "FR": ["France", "FR", "法国"],
}


def normalize_country_code(country: str | None) -> str | None:
    if country is None:
        return None
    normalized = country.strip().lower()
    if not normalized:
        return None
    if normalized in COUNTRY_CODES:
        return COUNTRY_CODES[normalized]
    compact = normalized.replace(" ", "")
    if len(compact) == 2 and compact.isalpha():
        return compact.upper()
    return country.strip()


def normalize_country(country: str | None) -> str | None:
    code = normalize_country_code(country)
    if code is None:
        return None
    return COUNTRY_NAMES.get(code, country.strip() if country else None)


def get_country_query_terms(country: str | None) -> list[str]:
    code = normalize_country_code(country)
    if code is None:
        return []
    terms = COUNTRY_QUERY_TERMS.get(code, [country.strip()] if country else [])
    seen: set[str] = set()
    result: list[str] = []
    for term in terms:
        cleaned = term.strip()
        if cleaned and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            result.append(cleaned)
    return result
