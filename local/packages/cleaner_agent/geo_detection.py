from __future__ import annotations

import re
from typing import Iterable

from crawler_agent.country_terms import (
    COUNTRY_CODES,
    COUNTRY_NAMES,
    get_country_query_terms,
    normalize_country,
    normalize_country_code,
)


SETTING_TERMS: dict[str, list[str]] = {
    "school": ["school", "university", "campus", "学校", "大学", "校园"],
    "hospital": ["hospital", "clinic", "医院", "诊所"],
    "care_home": ["care home", "nursing home", "long-term care", "养老院", "护理院"],
    "cruise": ["cruise ship", "cruise", "邮轮"],
    "restaurant": ["restaurant", "food service", "餐厅", "饭店"],
    "community": ["community", "社区"],
}


def _candidate_countries(country: str | None) -> Iterable[tuple[str, list[str]]]:
    if country:
        code = normalize_country_code(country)
        canonical = normalize_country(country)
        yield canonical or country, get_country_query_terms(country)
        if code in {"UK", "GB"}:
            yield "United Kingdom", get_country_query_terms("GB")
        return
    seen_codes: set[str] = set()
    for alias, code in COUNTRY_CODES.items():
        if code is None or code in seen_codes:
            continue
        seen_codes.add(code)
        canonical = COUNTRY_NAMES.get(code, alias.title())
        yield canonical, get_country_query_terms(code)


def _term_pattern(term: str) -> re.Pattern:
    escaped = re.escape(term)
    if re.fullmatch(r"[A-Za-z0-9 ._-]+", term):
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def detect_country_mentions(text: str, country: str | None = None) -> list[dict]:
    if not text:
        return []
    matches: list[dict] = []
    for canonical, terms in _candidate_countries(country):
        for term in terms:
            for match in _term_pattern(term).finditer(text):
                matches.append(
                    {
                        "country": canonical,
                        "matched_term": term,
                        "confidence": 0.9 if len(term) > 2 else 0.76,
                        "start": match.start(),
                        "end": match.end(),
                        "evidence": text[match.start() : match.end()],
                    }
                )
    matches.sort(key=lambda item: (-item["confidence"], item["start"], item["matched_term"]))
    return matches


def best_country_match(text: str, country: str | None = None) -> dict | None:
    matches = detect_country_mentions(text, country=country)
    return matches[0] if matches else None


def detect_setting(text: str) -> str | None:
    if not text:
        return None
    for setting, terms in SETTING_TERMS.items():
        for term in terms:
            if _term_pattern(term).search(text):
                return setting
    return None
