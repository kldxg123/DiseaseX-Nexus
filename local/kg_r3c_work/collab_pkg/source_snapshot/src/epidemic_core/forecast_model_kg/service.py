
from __future__ import annotations

import datetime as dt
from typing import Any
from sqlalchemy import Engine, text

from .engine import guidance_for_request
from .loader import load_graph, validate_graph, disease_alias_map
from .r3r import augment_guidance_response

COUNTRY_ALIASES = {
    "finland": "FIN", "\u82ac\u5170": "FIN", "fin": "FIN",
    "austria": "AUT", "\u5965\u5730\u5229": "AUT", "aut": "AUT",
    "france": "FRA", "\u6cd5\u56fd": "FRA", "fra": "FRA",
    "brazil": "BRA", "\u5df4\u897f": "BRA", "bra": "BRA",
    "peru": "PER", "\u79d8\u9c81": "PER", "per": "PER",
    "bangladesh": "BGD", "\u5b5f\u52a0\u62c9\u56fd": "BGD", "bgd": "BGD",
    "india": "IND", "\u5370\u5ea6": "IND", "ind": "IND",
    "cod": "COD", "drc": "COD", "congo": "COD", "\u521a\u679c\uff08\u91d1\uff09": "COD",
}


def normalize_country(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    v = str(value).strip()
    return COUNTRY_ALIASES.get(v.lower(), v.upper() if len(v) == 3 else v)


def metadata_response() -> dict[str, Any]:
    graph = load_graph()
    integrity = validate_graph(graph)
    return {"metadata": graph["metadata"], "integrity": integrity}


def full_graph_response() -> dict[str, Any]:
    graph = load_graph()
    validate_graph(graph)
    return graph


def disease_graph_response(disease_id_or_alias: str) -> dict[str, Any]:
    graph = load_graph()
    aliases = disease_alias_map(graph)
    disease_id = aliases.get(disease_id_or_alias.strip().lower())
    if not disease_id:
        raise ValueError("UNSUPPORTED_DISEASE")
    keep = {disease_id}
    for edge in graph["edges"]:
        if edge["source_id"] == disease_id or edge["target_id"] == disease_id:
            keep.add(edge["source_id"]); keep.add(edge["target_id"])
    return {
        "metadata": graph["metadata"],
        "nodes": [n for n in graph["nodes"] if n["id"] in keep],
        "edges": [e for e in graph["edges"] if e["source_id"] in keep and e["target_id"] in keep],
        "rules": [r for r in graph["rules"] if disease_id in r["disease_ids"] or "*" in r["disease_ids"]],
        "evidence_sources": [e for e in graph["evidence_sources"] if disease_id in e.get("disease_ids", [])],
    }


def observed_profile(engine: Engine, disease: str, country: str | None, target: str | None, horizon: int | None) -> dict[str, Any]:
    graph = load_graph()
    disease_id = disease_alias_map(graph).get(disease.strip().lower())
    if not disease_id:
        raise ValueError("UNSUPPORTED_DISEASE")
    country_norm = normalize_country(country)
    disease_slug = {
        "covid_19": "covid-19", "influenza": "influenza", "dengue": "dengue", "ebola": "ebola-disease",
        "hantavirus_puumala": "hantavirus", "legionella": "legionella", "nipah": "nipah",
    }.get(disease_id, disease_id)
    row: dict[str, Any] | None = None
    with engine.connect() as conn:
        base_sql = """
            SELECT disease_slug, country_alpha3 AS country, metric_code, min(period_start) AS start_date, max(period_end) AS end_date,
                   count(*) AS period_count,
                   count(DISTINCT period_start) AS unique_period_count,
                   count(*) FILTER (WHERE value_numeric IS NOT NULL) AS observed_non_null_period_count,
                   count(DISTINCT location_key) AS region_count,
                   count(*) FILTER (WHERE value_numeric IS NOT NULL AND value_numeric <> 0) AS nonzero_period_count,
                   count(DISTINCT source_key) AS source_count,
                   array_agg(DISTINCT source_key) AS source_ids
            FROM epidemic.dashboard_history_series_v2
            WHERE disease_slug=:disease_slug AND (CAST(:country AS text) IS NULL OR country_alpha3=CAST(:country AS text))
              AND (CAST(:target AS text) IS NULL OR metric_code=CAST(:target AS text))
            GROUP BY disease_slug, country_alpha3, metric_code
            ORDER BY count(*) DESC, country_alpha3, metric_code
            LIMIT 1
        """
        rows = conn.execute(text(base_sql), {"disease_slug": disease_slug, "country": country_norm, "target": target}).mappings().all()
        metric_fallback_used = False
        if not rows and target in {None, "cases_new"}:
            fallback_sql = """
                SELECT disease_slug, country_alpha3 AS country, metric_code, min(period_start) AS start_date, max(period_end) AS end_date,
                       count(*) AS period_count,
                       count(DISTINCT period_start) AS unique_period_count,
                       count(*) FILTER (WHERE value_numeric IS NOT NULL) AS observed_non_null_period_count,
                       count(DISTINCT location_key) AS region_count,
                       count(*) FILTER (WHERE value_numeric IS NOT NULL AND value_numeric <> 0) AS nonzero_period_count,
                       count(DISTINCT source_key) AS source_count,
                       array_agg(DISTINCT source_key) AS source_ids
                FROM epidemic.dashboard_history_series_v2
                WHERE disease_slug=:disease_slug AND (CAST(:country AS text) IS NULL OR country_alpha3=CAST(:country AS text))
                GROUP BY disease_slug, country_alpha3, metric_code
                ORDER BY
                  CASE
                    WHEN metric_code IN ('cases_new','influenza_ili_cases','influenza_sari_cases') THEN 0
                    WHEN metric_code LIKE '%positive%' OR metric_code LIKE '%detections%' THEN 1
                    ELSE 2
                  END,
                  count(*) DESC,
                  metric_code
                LIMIT 1
            """
            rows = conn.execute(text(fallback_sql), {"disease_slug": disease_slug, "country": country_norm}).mappings().all()
            metric_fallback_used = bool(rows)
        if rows:
            row = dict(rows[0])
            row["metric_fallback_used"] = metric_fallback_used
    profile: dict[str, Any] = {
        "disease_id": disease_id,
        "country_or_region": country_norm,
        "forecast_target": target or "cases_new",
        "metric_fallback_used": False,
        "horizon": horizon,
        "time_granularity": None,
        "start_date": None,
        "end_date": None,
        "history_weeks_normalized": None,
        "period_count": None,
        "unique_period_count": None,
        "observed_non_null_period_count": None,
        "observed_nonzero_period_count": None,
        "longest_contiguous_observed_run": None,
        "expected_period_count": None,
        "calendar_span_periods": None,
        "region_count": None,
        "coverage_ratio": None,
        "missing_ratio": None,
        "zero_ratio": None,
        "nonzero_period_count": None,
        "is_event_series": disease_id in {"ebola", "nipah"},
        "is_continuous_surveillance": None,
        "has_case_lags": None,
        "has_climate": None,
        "has_weather": None,
        "has_pollution": None,
        "has_mobility": disease_id == "covid_19" and None,
        "has_variant_frequency": None,
        "has_ecological_covariates": False if disease_id == "hantavirus_puumala" else None,
        "has_contact_tracing": True if disease_id in {"ebola", "nipah"} else None,
        "data_quality_status": None,
        "source_ids": (),
    }
    if row:
        start = row["start_date"]
        end = row["end_date"]
        weeks = None
        if start and end:
            weeks = max(1, int(((end - start).days + 6) / 7))
        period_count = int(row["period_count"] or 0)
        unique_period_count = int(row.get("unique_period_count") or period_count)
        observed_non_null = int(row.get("observed_non_null_period_count") or period_count)
        nonzero = int(row["nonzero_period_count"] or 0)
        profile.update({
            "country_or_region": row["country"],
            "time_granularity": "week_or_report_period",
            "start_date": start.isoformat() if hasattr(start, "isoformat") else str(start),
            "end_date": end.isoformat() if hasattr(end, "isoformat") else str(end),
            "history_weeks_normalized": weeks,
            "calendar_span_periods": weeks,
            "period_count": period_count,
            "unique_period_count": unique_period_count,
            "observed_non_null_period_count": observed_non_null,
            "observed_nonzero_period_count": nonzero,
            "expected_period_count": weeks,
            "longest_contiguous_observed_run": unique_period_count,
            "region_count": int(row["region_count"] or 1),
            "forecast_target": str(row.get("metric_code") or target or "cases_new"),
            "metric_fallback_used": bool(row.get("metric_fallback_used")),
            "nonzero_period_count": nonzero,
            "zero_ratio": None if period_count == 0 else round(1 - (nonzero / period_count), 6),
            "is_continuous_surveillance": disease_id not in {"ebola", "nipah"},
            "data_quality_status": "PASS" if period_count > 0 else None,
            "source_ids": tuple(str(x) for x in (row.get("source_ids") or []) if x),
        })
    if disease_id == "dengue":
        profile["has_climate"] = False
        profile["has_weather"] = False
    if disease_id == "legionella":
        profile["has_weather"] = False
        profile["has_pollution"] = False
    return profile


def guidance_response(engine: Engine | None, disease: str, country: str | None, target: str | None, horizon: int | None, explicit_profile: dict[str, Any] | None = None, frequency: str | None = "week") -> dict[str, Any]:
    country_norm = normalize_country(country)
    profile = explicit_profile
    source = "user_supplied_profile" if profile else "explicit_parameters_only"
    if profile is None and engine is not None:
        profile = observed_profile(engine, disease, country_norm, target, horizon)
        source = "observed_profile"
    result = guidance_for_request(disease=disease, country_or_region=country_norm, forecast_target=target, horizon=horizon, profile=profile)
    result["dataset_profile_source"] = source
    return augment_guidance_response(engine, result, disease=disease, country=country_norm, target=target, horizon=horizon, frequency=frequency)
