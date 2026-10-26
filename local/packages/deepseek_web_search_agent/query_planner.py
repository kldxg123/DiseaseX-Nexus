from __future__ import annotations

from typing import Any

from deepseek_web_search_agent.deepseek_client import DeepSeekClient
from deepseek_web_search_agent.structured_numeric_intent import build_structured_numeric_evidence_intent
from deepseek_web_search_agent.types import DeepSeekWebSearchRequest, PlannedQuery, QueryPlan


KNOWN_DISEASE_ALIASES = {
    "influenza": ["influenza", "flu", "ILI", "influenza-like illness"],
    "flu": ["influenza", "flu", "ILI", "influenza-like illness"],
    "norovirus": ["norovirus", "NoroSTAT", "acute gastroenteritis"],
    "measles": ["measles", "rubeola", "MMR vaccine preventable disease"],
    "cholera": ["cholera", "Vibrio cholerae", "acute watery diarrhea"],
    "ebola": ["Ebola", "Ebola virus disease", "EVD", "viral hemorrhagic fever"],
}


def build_query_plan(
    request: DeepSeekWebSearchRequest,
    *,
    client: DeepSeekClient | None = None,
) -> QueryPlan:
    req = request.validated()
    fallback = deterministic_query_plan(req)
    if not req.allow_deepseek_api:
        return fallback
    client = client or DeepSeekClient()
    result = client.call_chat_json(
        system_prompt=(
            "You are a public-health data-source search planner. Return compact JSON with "
            "disease_aliases, country_aliases, search_queries, exclusion_terms, source_priorities, "
            "information_need, expected_metrics, extraction_schema, and quality_criteria."
        ),
        user_payload={
            "disease": req.disease,
            "country": req.country,
            "query_intent": req.query_intent,
            "max_search_queries": req.max_search_queries,
        },
        allow_deepseek_api=req.allow_deepseek_api,
        fallback_json=fallback.to_dict(),
    )
    if not result.called or not result.parsed_json:
        return fallback
    return _plan_from_json(result.parsed_json, req, fallback=fallback, generated_by="deepseek")


def deterministic_query_plan(request: DeepSeekWebSearchRequest) -> QueryPlan:
    disease = request.disease.strip()
    country = request.country.strip()
    aliases = KNOWN_DISEASE_ALIASES.get(disease.lower(), [disease])
    country_aliases = [country]
    if request.query_intent == "structured_numeric_evidence":
        numeric_intent = build_structured_numeric_evidence_intent(
            disease=disease,
            country_or_region=country,
        )
        base_queries = numeric_intent.query_plan.query_terms[: request.max_search_queries]
        source_priorities = numeric_intent.query_plan.preferred_source_types
        expected_metrics = list(numeric_intent.target_fields)
        criteria = [
            "contains date or ISO week",
            "contains numeric case/death/contact metrics",
            "official situation report, dashboard, table, CSV, API, or data portal",
            "human review required before activation",
        ]
    else:
        base_queries = _structured_queries(disease, country)[: request.max_search_queries]
        source_priorities = [
            "official dataset",
            "CSV",
            "API",
            "HTML table",
            "surveillance dashboard",
            "weekly public health report",
        ]
        expected_metrics = ["cases", "outbreaks", "tests", "positivity_rate", "hospitalizations", "deaths"]
        criteria = [
            "official or recognized public-health source",
            "clear disease and country relevance",
            "structured time-series likelihood",
            "human review required before activation",
        ]
    return QueryPlan(
        disease=disease,
        country=country,
        query_intent=request.query_intent,
        disease_aliases=aliases,
        country_aliases=country_aliases,
        search_queries=[
            PlannedQuery(query_text=query, query_rank=index + 1, query_intent=request.query_intent)
            for index, query in enumerate(base_queries)
        ],
        exclusion_terms=["jobs", "career", "advertisement", "social media", "login required"],
        source_priorities=source_priorities,
        information_need=(
            "Find disease-country surveillance sources with dates/weeks, numeric metrics, provenance, "
            "and safe access paths."
        ),
        expected_metrics=expected_metrics,
        extraction_schema={
            "required": ["disease", "country", "source_url", "information_type"],
            "optional": ["event_date", "iso_week", "metric_name", "metric_value", "confidence"],
        },
        quality_criteria=criteria,
        generated_by="fallback",
        warnings=[] if disease.lower() in KNOWN_DISEASE_ALIASES else ["unknown_disease_alias_low_confidence"],
    )


def _plan_from_json(
    payload: dict[str, Any],
    request: DeepSeekWebSearchRequest,
    *,
    fallback: QueryPlan,
    generated_by: str,
) -> QueryPlan:
    raw_queries = payload.get("search_queries")
    queries: list[str] = []
    if isinstance(raw_queries, list):
        for item in raw_queries:
            if isinstance(item, str) and item.strip():
                queries.append(item.strip())
            elif isinstance(item, dict) and str(item.get("query_text") or "").strip():
                queries.append(str(item["query_text"]).strip())
    if not queries:
        return fallback
    queries = queries[: request.max_search_queries]
    return QueryPlan(
        disease=request.disease,
        country=request.country,
        query_intent=request.query_intent,
        disease_aliases=_string_list(payload.get("disease_aliases"), fallback.disease_aliases),
        country_aliases=_string_list(payload.get("country_aliases"), fallback.country_aliases),
        search_queries=[
            PlannedQuery(query_text=query, query_rank=index + 1, query_intent=request.query_intent, generated_by=generated_by)
            for index, query in enumerate(queries)
        ],
        exclusion_terms=_string_list(payload.get("exclusion_terms"), fallback.exclusion_terms),
        source_priorities=_string_list(payload.get("source_priorities"), fallback.source_priorities),
        information_need=str(payload.get("information_need") or fallback.information_need),
        expected_metrics=_string_list(payload.get("expected_metrics"), fallback.expected_metrics),
        extraction_schema=payload.get("extraction_schema") if isinstance(payload.get("extraction_schema"), dict) else fallback.extraction_schema,
        quality_criteria=_string_list(payload.get("quality_criteria"), fallback.quality_criteria),
        generated_by=generated_by,
        warnings=[],
    )


def _string_list(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    rows = [str(item).strip() for item in value if str(item or "").strip()]
    return rows or list(fallback)


def _structured_queries(disease: str, country: str) -> list[str]:
    disease_lower = disease.lower()
    country_lower = country.lower()
    queries = [
        f"{disease} {country} cases by week",
        f"{disease} {country} surveillance data",
        f"{disease} {country} outbreak dataset",
        f"{disease} {country} public health report table",
        f"{disease} {country} CSV",
        f"{disease} {country} API",
        f"{disease} {country} dashboard data",
        f"{disease} {country} weekly surveillance report",
    ]
    if "ebola" in disease_lower or "african" in country_lower:
        ebola_queries = [
            "Ebola Africa outbreak data",
            "Ebola situation report cases deaths",
            "Ebola WHO AFRO situation report",
            "Ebola Africa confirmed cases deaths contacts monitoring",
            "Ebola outbreak line list table",
            "Ebola Africa surveillance CSV API",
        ]
        queries = ebola_queries + queries
    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        key = query.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(query)
    return deduped
