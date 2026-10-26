from __future__ import annotations

from deepseek_web_search_agent.types import GeneralWebSearchResult, QueryPlan, RankedSearchResult


OFFICIAL_TERMS = (
    "cdc",
    ".gov",
    "who.int",
    "who afro",
    "afro.who.int",
    "africacdc",
    "africa cdc",
    "ecdc",
    "ministry of health",
    "health department",
    "public health",
    "surveillance",
)
DATA_TERMS = (
    "csv",
    "api",
    "json",
    "table",
    "dataset",
    "download",
    "weekly",
    "dashboard",
    "surveillance",
    "line list",
    "situation report",
    "cases",
    "deaths",
    "suspected",
    "contacts",
    "monitoring",
)
DATE_NUMERIC_TERMS = (
    "date",
    "week",
    "weekly",
    "mmwr",
    "epidemiological",
    "situation report",
    "cases",
    "confirmed",
    "deaths",
    "suspected",
    "contacts",
    "monitoring",
)
SITUATION_REPORT_TERMS = (
    "situation report",
    "epidemiological update",
    "weekly bulletin",
    "outbreak update",
    "who afro",
    "africa cdc",
)
DOWNRANK_TERMS = (
    "jobs",
    "career",
    "employment",
    "advertisement",
    "facebook",
    "login",
    "paywall",
    "subscribe",
    "shopping",
)


def rank_results(
    results: list[GeneralWebSearchResult],
    plan: QueryPlan,
    *,
    prefer_official_and_structured_sources: bool = True,
    prefer_dated_numeric_sources: bool = False,
) -> list[RankedSearchResult]:
    ranked = [score_result(result, plan) for result in results if not result.error_type]
    if prefer_official_and_structured_sources:
        ranked.sort(
            key=lambda item: (
                _dated_numeric_sort_bonus(item) if prefer_dated_numeric_sources else 0.0,
                item.structured_time_series_likelihood,
                item.authority,
                item.data_likelihood,
                item.total_score,
            ),
            reverse=True,
        )
    else:
        ranked.sort(key=lambda item: item.total_score, reverse=True)
    return [
        RankedSearchResult(
            result=item.result,
            disease_relevance=item.disease_relevance,
            country_relevance=item.country_relevance,
            authority=item.authority,
            data_likelihood=item.data_likelihood,
            structured_time_series_likelihood=item.structured_time_series_likelihood,
            recency=item.recency,
            evidence_quality=item.evidence_quality,
            safety_risk=item.safety_risk,
            total_score=item.total_score,
            source_type=item.source_type,
            selected_for_page_read=index < 8,
        )
        for index, item in enumerate(ranked)
    ]


def score_result(result: GeneralWebSearchResult, plan: QueryPlan) -> RankedSearchResult:
    text = " ".join([result.title, result.snippet, result.url, result.domain]).lower()
    disease_terms = [plan.disease.lower(), *[alias.lower() for alias in plan.disease_aliases]]
    country_terms = [plan.country.lower(), *[alias.lower() for alias in plan.country_aliases]]
    disease_relevance = 1.0 if any(term and term in text for term in disease_terms) else 0.25
    country_relevance = 1.0 if any(term and term in text for term in country_terms) else 0.35
    authority = 0.85 if any(term in text for term in OFFICIAL_TERMS) else 0.45
    data_likelihood = min(1.0, 0.2 + 0.16 * sum(1 for term in DATA_TERMS if term in text))
    dated_numeric_hits = sum(1 for term in DATE_NUMERIC_TERMS if term in text)
    situation_hits = sum(1 for term in SITUATION_REPORT_TERMS if term in text)
    structured = 0.9 if any(term in text for term in ("csv", "api", "table", "weekly")) else data_likelihood * 0.7
    if plan.query_intent == "structured_numeric_evidence":
        structured = min(1.0, structured + 0.05 * dated_numeric_hits + 0.08 * situation_hits)
    recency = 0.55
    evidence_quality = (authority + data_likelihood + structured) / 3
    safety_risk = 0.35 if any(term in text for term in DOWNRANK_TERMS) else 0.05
    source_type = infer_source_type(result)
    total = (
        0.24 * disease_relevance
        + 0.18 * country_relevance
        + 0.18 * authority
        + 0.18 * data_likelihood
        + 0.14 * structured
        + 0.08 * evidence_quality
        - 0.18 * safety_risk
    )
    return RankedSearchResult(
        result=result,
        disease_relevance=round(disease_relevance, 4),
        country_relevance=round(country_relevance, 4),
        authority=round(authority, 4),
        data_likelihood=round(data_likelihood, 4),
        structured_time_series_likelihood=round(structured, 4),
        recency=round(recency, 4),
        evidence_quality=round(evidence_quality, 4),
        safety_risk=round(safety_risk, 4),
        total_score=round(max(0.0, min(1.0, total)), 4),
        source_type=source_type,
    )


def _dated_numeric_sort_bonus(item: RankedSearchResult) -> float:
    text = " ".join(
        [
            item.result.title,
            item.result.snippet,
            item.result.url,
            item.result.domain,
            item.source_type,
        ]
    ).lower()
    date_hit = any(term in text for term in ("date", "week", "weekly", "epidemiological", "situation report"))
    numeric_hit = any(term in text for term in ("cases", "confirmed", "deaths", "suspected", "contacts", "monitoring"))
    structured_hit = any(term in text for term in ("table", "csv", "api", "dataset", "dashboard"))
    return float(date_hit) + float(numeric_hit) + 0.5 * float(structured_hit)


def infer_source_type(result: GeneralWebSearchResult) -> str:
    text = " ".join([result.title, result.snippet, result.url, result.source_type_hint or ""]).lower()
    if ".csv" in text or " csv" in text:
        return "csv_dataset"
    if "api" in text or "json" in text:
        return "json_api"
    if "table" in text:
        return "html_table"
    if "dashboard" in text:
        return "dashboard_candidate"
    if "report" in text or "weekly" in text:
        return "official_report_evidence"
    if "dataset" in text or "surveillance" in text:
        return "structured_dataset_candidate"
    return "unknown"
