from __future__ import annotations

from dataclasses import replace

from live_source_search_orchestrator.types import DiseaseAliasSet, NormalizedSourceCandidate

OFFICIAL_DOMAINS = ("cdc.gov", "who.int", "ecdc.europa.eu", "catalog.data.gov", "data.gov")
NEWS_TYPES = {"news_or_media", "literature_or_guidance", "low_relevance"}
TYPE_SCORES = {
    "official_dataset": 0.9,
    "csv_dataset": 0.88,
    "json_api": 0.86,
    "html_table": 0.75,
    "dashboard_data_source": 0.72,
    "surveillance_portal": 0.78,
    "official_report_page": 0.58,
    "literature_or_guidance": 0.35,
    "news_or_media": 0.32,
    "low_relevance": 0.12,
    "unknown": 0.25,
}
DATED_TERMS = ("date", "week", "weekly", "epidemiological", "situation report", "bulletin", "update")
NUMERIC_TERMS = ("cases", "confirmed", "deaths", "suspected", "contacts", "monitoring", "fatality", "probable")
STRUCTURED_TERMS = ("table", "csv", "api", "dataset", "dashboard", "data portal", "json")
REPORT_TERMS = ("situation report", "epidemiological update", "weekly bulletin", "outbreak update")


def score_candidates(
    candidates: list[NormalizedSourceCandidate],
    *,
    aliases: DiseaseAliasSet,
) -> list[NormalizedSourceCandidate]:
    return sorted(
        [score_candidate(candidate, aliases=aliases) for candidate in candidates],
        key=lambda item: (item.total_source_score, item.authority_score, item.data_likelihood_score),
        reverse=True,
    )


def score_candidate(candidate: NormalizedSourceCandidate, *, aliases: DiseaseAliasSet) -> NormalizedSourceCandidate:
    authority = _authority_score(candidate.domain)
    disease = 0.15 + min(0.75, 0.25 * len(candidate.disease_mentions))
    if aliases.unknown_disease_alias_expansion and not candidate.disease_mentions:
        disease = 0.2
    country = 0.75 if candidate.country_mentions else 0.45
    data = min(1.0, 0.2 + 0.12 * len(candidate.data_keywords))
    text = " ".join(
        [
            candidate.normalized_title,
            candidate.normalized_url,
            candidate.snippet_summary or "",
            " ".join(candidate.data_keywords),
        ]
    ).lower()
    dated_numeric = _term_score(text, DATED_TERMS, 0.12) * 0.45 + _term_score(text, NUMERIC_TERMS, 0.1) * 0.55
    table_dataset = _term_score(text, STRUCTURED_TERMS, 0.12)
    situation_report = _term_score(text, REPORT_TERMS, 0.22)
    feature_ready = _clamp((dated_numeric + table_dataset + situation_report) / 3)
    type_score = TYPE_SCORES.get(candidate.candidate_source_type, 0.25)
    freshness = 0.65 if any(term in candidate.data_keywords for term in ("weekly", "surveillance", "dashboard")) else 0.45
    compliance_risk = 0.05 if candidate.authority_domain else 0.18
    provider_confidence = _provider_confidence(candidate.provider)
    warnings = list(candidate.warnings)
    total = (
        0.2 * authority
        + 0.22 * disease
        + 0.1 * country
        + 0.12 * data
        + 0.1 * type_score
        + 0.18 * dated_numeric
        + 0.11 * table_dataset
        + 0.09 * situation_report
        + 0.08 * feature_ready
        + 0.06 * freshness
        + 0.08 * provider_confidence
        - 0.18 * compliance_risk
    )
    if any(term in text for term in ("travel advice", "symptoms", "what is", "overview")) and not any(term in text for term in NUMERIC_TERMS):
        total = min(total, 0.42)
        warnings.append("source_is_evidence_only")
    if any(term in text for term in NUMERIC_TERMS) and not any(term in text for term in DATED_TERMS):
        warnings.append("weak_numeric_only")
    if any(term in text for term in DATED_TERMS) and not any(term in text for term in NUMERIC_TERMS):
        warnings.append("weak_temporal_only")
    if candidate.candidate_source_type in NEWS_TYPES:
        total = min(total, 0.49)
    if aliases.unknown_disease_alias_expansion:
        warnings.append("low_alias_confidence")
    if candidate.candidate_source_type in {"dashboard_data_source", "surveillance_portal", "official_report_page"}:
        warnings.append("metadata_check_required")
    return replace(
        candidate,
        authority_score=round(_clamp(authority), 6),
        disease_relevance_score=round(_clamp(disease), 6),
        country_relevance_score=round(_clamp(country), 6),
        data_likelihood_score=round(_clamp(data), 6),
        dated_numeric_likelihood_score=round(_clamp(dated_numeric), 6),
        table_or_dataset_likelihood_score=round(_clamp(table_dataset), 6),
        situation_report_score=round(_clamp(situation_report), 6),
        feature_ready_evidence_score=round(_clamp(feature_ready), 6),
        freshness_score=round(_clamp(freshness), 6),
        compliance_risk_score=round(_clamp(compliance_risk), 6),
        total_source_score=round(_clamp(total), 6),
        refined_score=round(_clamp(total), 6),
        warnings=sorted(set(warnings)),
    )


def _authority_score(domain: str) -> float:
    if any(domain == item or domain.endswith(f".{item}") for item in OFFICIAL_DOMAINS):
        return 0.95
    if domain.endswith(".gov"):
        return 0.82
    if domain.endswith(".edu"):
        return 0.65
    return 0.35


def _provider_confidence(provider: str) -> float:
    if provider == "data_gov":
        return 0.9
    if provider == "official_directory":
        return 0.82
    if provider == "gdelt":
        return 0.62
    return 0.4


def _term_score(text: str, terms: tuple[str, ...], step: float) -> float:
    return _clamp(sum(step for term in terms if term in text))


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


__all__ = ["score_candidate", "score_candidates"]
