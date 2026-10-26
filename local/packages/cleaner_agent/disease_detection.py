from __future__ import annotations

import re
from typing import Iterable

from crawler_agent.disease_terms import DISEASE_ALIASES, get_disease_aliases


LOW_CONFIDENCE_TERMS = {
    "acute gastroenteritis",
    "viral gastroenteritis",
    "gastroenteritis outbreak",
    "stomach flu",
}


def _iter_disease_aliases(disease: str | None) -> Iterable[tuple[str, list[str]]]:
    if disease:
        yield disease.strip().lower(), list(get_disease_aliases(disease))
        return
    for canonical in DISEASE_ALIASES:
        yield canonical, list(get_disease_aliases(canonical))


def _term_pattern(term: str) -> re.Pattern:
    escaped = re.escape(term)
    if re.fullmatch(r"[A-Za-z0-9 ._-]+", term):
        return re.compile(rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])", re.IGNORECASE)
    return re.compile(escaped, re.IGNORECASE)


def _confidence_for_term(canonical: str, term: str) -> float:
    normalized = term.lower()
    if normalized == canonical.lower() or term == "诺如病毒":
        return 0.95
    if normalized in LOW_CONFIDENCE_TERMS:
        return 0.65
    if "vomiting" in normalized or "norwalk" in normalized:
        return 0.82
    return 0.78


def detect_disease_mentions(text: str, disease: str | None = None) -> list[dict]:
    if not text:
        return []
    matches: list[dict] = []
    for canonical, aliases in _iter_disease_aliases(disease):
        for alias in aliases:
            if not alias:
                continue
            for match in _term_pattern(alias).finditer(text):
                matches.append(
                    {
                        "disease": canonical,
                        "matched_term": alias,
                        "confidence": _confidence_for_term(canonical, alias),
                        "start": match.start(),
                        "end": match.end(),
                        "evidence": text[match.start() : match.end()],
                    }
                )
    matches.sort(key=lambda item: (-item["confidence"], item["start"], item["matched_term"]))
    return matches


def best_disease_match(text: str, disease: str | None = None) -> dict | None:
    matches = detect_disease_mentions(text, disease=disease)
    return matches[0] if matches else None
