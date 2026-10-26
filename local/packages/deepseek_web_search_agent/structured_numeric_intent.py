from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_TARGET_FIELDS = [
    "confirmed_cases",
    "confirmed_deaths",
    "suspected_cases",
    "contacts_under_monitoring",
    "probable_cases",
    "total_cases",
    "case_fatality_ratio",
]


@dataclass(frozen=True)
class NumericEvidenceSignalProfile:
    required_signals: list[str]
    preferred_source_types: list[str]
    excluded_source_types: list[str]
    scoring_weights: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NumericEvidenceQueryPlan:
    query_intent: str
    query_terms: list[str]
    required_signals: list[str]
    preferred_source_types: list[str]
    excluded_source_types: list[str]
    provider_targets: list[str]
    scoring_weights: dict[str, float]
    intent_groups: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StructuredNumericEvidenceIntent:
    disease: str
    country_or_region: str
    date_start: str | None
    date_end: str | None
    target_fields: list[str]
    signal_profile: NumericEvidenceSignalProfile
    query_plan: NumericEvidenceQueryPlan

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_structured_numeric_evidence_intent(
    *,
    disease: str,
    country_or_region: str,
    date_start: str | None = None,
    date_end: str | None = None,
    target_fields: list[str] | None = None,
) -> StructuredNumericEvidenceIntent:
    disease_text = " ".join(str(disease or "").split()) or "disease"
    region_text = " ".join(str(country_or_region or "").split()) or "region"
    fields = list(target_fields or DEFAULT_TARGET_FIELDS)
    profile = _signal_profile(disease_text, region_text)
    grouped = _query_groups(disease_text, region_text)
    flat_queries = _dedupe([query for queries in grouped.values() for query in queries])
    query_plan = NumericEvidenceQueryPlan(
        query_intent="structured_numeric_evidence",
        query_terms=flat_queries,
        required_signals=profile.required_signals,
        preferred_source_types=profile.preferred_source_types,
        excluded_source_types=profile.excluded_source_types,
        provider_targets=_provider_targets(disease_text, region_text),
        scoring_weights=profile.scoring_weights,
        intent_groups=grouped,
    )
    return StructuredNumericEvidenceIntent(
        disease=disease_text,
        country_or_region=region_text,
        date_start=date_start,
        date_end=date_end,
        target_fields=fields,
        signal_profile=profile,
        query_plan=query_plan,
    )


def _signal_profile(disease: str, region: str) -> NumericEvidenceSignalProfile:
    lower = f"{disease} {region}".lower()
    required = [
        "date_or_week",
        "country_or_region",
        "confirmed_cases",
        "confirmed_deaths",
        "suspected_cases",
        "contacts_under_monitoring",
        "situation_report",
        "outbreak_update",
        "table_or_dataset",
    ]
    if "ebola" not in lower:
        required = [
            "date_or_week",
            "country_or_region",
            "confirmed_cases",
            "confirmed_deaths",
            "table_or_dataset",
        ]
    return NumericEvidenceSignalProfile(
        required_signals=required,
        preferred_source_types=[
            "situation_report",
            "epidemiological_update",
            "dashboard",
            "data_portal",
            "csv_dataset",
            "json_api",
            "html_table",
            "official_bulletin",
        ],
        excluded_source_types=[
            "news_article",
            "generic_information_page",
            "academic_abstract_without_table",
            "commercial_page",
            "jobs_or_careers",
            "travel_advice_only",
            "general_disease_explainer",
        ],
        scoring_weights={
            "dated_numeric_likelihood": 0.26,
            "table_or_dataset_likelihood": 0.2,
            "situation_report": 0.18,
            "official_authority": 0.16,
            "disease_country_relevance": 0.12,
            "feature_ready_evidence": 0.08,
        },
    )


def _query_groups(disease: str, region: str) -> dict[str, list[str]]:
    lower = f"{disease} {region}".lower()
    if "ebola" in lower and ("africa" in lower or "african" in lower):
        return {
            "official_situation_reports": [
                "Ebola situation report cases deaths contacts Africa",
                "Ebola WHO AFRO weekly bulletin cases deaths",
                "Ebola disease outbreak news confirmed cases deaths Africa",
                "site:who.int Ebola situation report cases deaths",
                "site:afro.who.int Ebola outbreak cases deaths",
            ],
            "cases_deaths_contacts_numeric_table": [
                "Ebola outbreak update confirmed deaths contacts monitoring Africa",
                "Ebola situation report table confirmed suspected contacts",
                "Ebola Africa confirmed cases deaths contacts monitoring",
            ],
            "dashboards_data_portals": [
                "Ebola Africa dashboard cases deaths",
                "Ebola Africa dataset cases deaths contacts",
                "site:data.humdata.org Ebola cases deaths dataset",
                "site:reliefweb.int Ebola situation report cases deaths",
            ],
            "csv_api_dataset": [
                "Ebola Africa surveillance CSV API cases deaths",
                "Ebola outbreak line list table Africa",
            ],
            "weekly_epidemiological_updates": [
                "Ebola weekly epidemiological update Ebola Africa cases deaths",
                "Ebola outbreak response report contacts under monitoring",
                "Ebola Africa CDC outbreak update cases deaths",
                "site:africacdc.org Ebola outbreak update cases deaths",
            ],
        }
    return {
        "official_situation_reports": [
            f"{disease} {region} situation report cases deaths",
            f"{disease} {region} public health report table cases deaths",
        ],
        "cases_deaths_contacts_numeric_table": [
            f"{disease} {region} confirmed cases deaths table",
            f"{disease} {region} cases deaths contacts monitoring",
        ],
        "dashboards_data_portals": [
            f"{disease} {region} dashboard data cases",
            f"{disease} {region} data portal surveillance",
        ],
        "csv_api_dataset": [
            f"{disease} {region} CSV API dataset cases deaths",
        ],
        "weekly_epidemiological_updates": [
            f"{disease} {region} weekly epidemiological update cases deaths",
        ],
    }


def _provider_targets(disease: str, region: str) -> list[str]:
    lower = f"{disease} {region}".lower()
    if "ebola" in lower and ("africa" in lower or "african" in lower):
        return ["tavily", "who_afro", "africa_cdc", "reliefweb", "hdx"]
    return ["tavily", "official_directory", "data_gov", "reliefweb", "hdx"]


def _dedupe(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            output.append(text[:120])
    return output


__all__ = [
    "DEFAULT_TARGET_FIELDS",
    "NumericEvidenceQueryPlan",
    "NumericEvidenceSignalProfile",
    "StructuredNumericEvidenceIntent",
    "build_structured_numeric_evidence_intent",
]
