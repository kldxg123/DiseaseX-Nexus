
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, asdict
from typing import Any, Iterable

from .loader import disease_alias_map, load_graph


def _zh(value: str) -> str:
    return value.encode("ascii", "backslashreplace").decode("unicode_escape")


@dataclass(frozen=True)
class DatasetProfile:
    disease_id: str
    country_or_region: str | None = None
    time_granularity: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    history_weeks_normalized: int | None = None
    period_count: int | None = None
    unique_period_count: int | None = None
    observed_non_null_period_count: int | None = None
    observed_nonzero_period_count: int | None = None
    longest_contiguous_observed_run: int | None = None
    expected_period_count: int | None = None
    calendar_span_periods: int | None = None
    region_count: int | None = None
    coverage_ratio: float | None = None
    missing_ratio: float | None = None
    zero_ratio: float | None = None
    nonzero_period_count: int | None = None
    is_event_series: bool | None = None
    is_continuous_surveillance: bool | None = None
    has_case_lags: bool | None = None
    has_climate: bool | None = None
    has_weather: bool | None = None
    has_pollution: bool | None = None
    has_mobility: bool | None = None
    has_variant_frequency: bool | None = None
    has_ecological_covariates: bool | None = None
    has_contact_tracing: bool | None = None
    forecast_target: str | None = None
    horizon: int | None = None
    metric_fallback_used: bool = False
    data_quality_status: str | None = None
    source_ids: tuple[str, ...] = ()


def dataset_profile_contract(profile: DatasetProfile) -> dict[str, Any]:
    """Cadence-aware profile contract.

    This intentionally separates calendar span, unique observed periods, and
    publication/model requirements. 104 weeks is no longer a global execution
    lock; it is a formal-publication / two-season heuristic where a model or
    publication policy explicitly asks for it.
    """
    observed = profile.observed_non_null_period_count
    if observed is None:
        observed = profile.unique_period_count
    if observed is None:
        observed = profile.period_count
    return {
        "disease_id": profile.disease_id,
        "geography_scope": profile.country_or_region,
        "metric_id": profile.forecast_target,
        "target_semantics": profile.forecast_target,
        "cadence": profile.time_granularity,
        "period_start": profile.start_date,
        "period_end": profile.end_date,
        "calendar_span_periods": profile.calendar_span_periods or profile.history_weeks_normalized,
        "unique_period_count": profile.unique_period_count,
        "observed_non_null_period_count": observed,
        "observed_nonzero_period_count": profile.observed_nonzero_period_count or profile.nonzero_period_count,
        "longest_contiguous_observed_run": profile.longest_contiguous_observed_run,
        "expected_period_count": profile.expected_period_count,
        "coverage_ratio": profile.coverage_ratio,
        "missing_ratio": profile.missing_ratio,
        "zero_ratio": profile.zero_ratio,
        "duplicate_period_count": None,
        "negative_revision_count": None,
        "latest_period_lag": None,
        "region_count": profile.region_count,
        "is_event_series": profile.is_event_series,
        "is_continuous_surveillance": profile.is_continuous_surveillance,
        "covariates_available": {
            "case_lags": profile.has_case_lags,
            "climate": profile.has_climate,
            "weather": profile.has_weather,
            "pollution": profile.has_pollution,
            "mobility": profile.has_mobility,
            "variant_frequency": profile.has_variant_frequency,
            "ecological": profile.has_ecological_covariates,
            "contact_tracing": profile.has_contact_tracing,
        },
        "source_contract_version": None,
        "data_snapshot_id": None,
    }


def resolve_disease(value: str, graph: dict[str, Any] | None = None) -> dict[str, Any]:
    if not value or not value.strip():
        raise ValueError("DISEASE_REQUIRED")
    graph = graph or load_graph()
    disease_id = disease_alias_map(graph).get(value.strip().lower())
    if not disease_id:
        raise ValueError("UNSUPPORTED_DISEASE")
    node = next(n for n in graph["nodes"] if n["id"] == disease_id)
    return node


def _node_map(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["id"]: n for n in graph["nodes"]}


def _model_item(model_id: str, priority: str, reason: str, conditions_met: list[str] | None = None, conditions_missing: list[str] | None = None, limitations: list[str] | None = None, graph: dict[str, Any] | None = None) -> dict[str, Any]:
    graph = graph or load_graph()
    node = _node_map(graph)[model_id]
    return {
        "model_id": model_id,
        "label_zh": node.get("label_zh"),
        "label_en": node.get("label_en"),
        "priority": priority,
        "reason": reason,
        "conditions_met": conditions_met or [],
        "conditions_missing": conditions_missing or [],
        "limitations": limitations or node.get("limitations", []),
    }


def _when_matches(when: dict[str, Any], profile: DatasetProfile) -> bool:
    values = asdict(profile)
    for key, expected in when.items():
        if key == "history_weeks_min":
            if values.get("history_weeks_normalized") is None or values["history_weeks_normalized"] < int(expected):
                return False
        elif key == "history_weeks_max_exclusive":
            if values.get("history_weeks_normalized") is None or values["history_weeks_normalized"] >= int(expected):
                return False
        elif key == "region_count_min":
            if values.get("region_count") is None or values["region_count"] < int(expected):
                return False
        elif key == "horizon_max":
            if values.get("horizon") is None or values["horizon"] > int(expected):
                return False
        elif key == "quality_gate_passed":
            if (values.get("data_quality_status") == "PASS") is not bool(expected):
                return False
        elif key == "has_climate_or_weather":
            actual = bool(values.get("has_climate")) or bool(values.get("has_weather"))
            if actual is not bool(expected):
                return False
        elif key == "has_environmental_covariates":
            actual = bool(values.get("has_weather")) or bool(values.get("has_pollution")) or bool(values.get("has_climate"))
            if actual is not bool(expected):
                return False
        else:
            if values.get(key) != expected:
                return False
    return True


def eligibility_track(profile: DatasetProfile) -> str:
    if profile.is_event_series or (profile.zero_ratio is not None and profile.zero_ratio >= 0.8):
        return "SPARSE_SCENARIO"
    observed = profile.observed_non_null_period_count
    if observed is None:
        observed = profile.unique_period_count
    if observed is None:
        observed = profile.period_count
    if observed is None:
        return "INSUFFICIENT_OR_UNKNOWN"
    cadence = (profile.time_granularity or "").lower()
    if cadence in {"month", "monthly"}:
        if observed >= 24 and profile.data_quality_status == "PASS":
            return "MONTHLY_DEMO"
        if observed >= 12:
            return "BASELINE_DEMO"
        return "SCENARIO_OR_NOT_ELIGIBLE"
    if observed >= 104 and (profile.region_count or 0) >= 5 and profile.data_quality_status == "PASS":
        return "FORMAL"
    if 52 <= observed < 104:
        return "DEMO"
    if observed >= 8:
        return "BASELINE_DEMO"
    return "SCENARIO_OR_NOT_ELIGIBLE"


def eligibility_decision(profile: DatasetProfile) -> dict[str, Any]:
    track = eligibility_track(profile)
    observed = profile.observed_non_null_period_count or profile.unique_period_count or profile.period_count
    compatible: list[dict[str, Any]] = []
    incompatible: list[dict[str, Any]] = []
    def add(model: str, ok: bool, reason: str) -> None:
        (compatible if ok else incompatible).append({"model": model, "reason": reason})
    add("last_value_baseline", bool(observed and observed >= 1), "requires at least one observed period")
    add("rolling_mean_baseline", bool(observed and observed >= 4), "requires at least four observed periods")
    add("drift_baseline", bool(observed and observed >= 8), "requires at least eight observed periods")
    add("seasonal_naive_52", bool(observed and observed >= 104), "requires two 52-week seasonal cycles")
    add("xgboost_tabular", bool(observed and observed >= 52 and (profile.region_count or 0) >= 5), "requires enough rows and panel/region support")
    return {
        "policy_version": "r3_cadence_model_capability_v1",
        "track": track,
        "global_104_hard_block": False,
        "formal_publication_min_observed_weekly_periods": 104,
        "observed_period_definition": "observed_non_null_period_count / unique_period_count, not calendar span or imputed length",
        "eligible_tracks": [track],
        "compatible_models": compatible,
        "incompatible_models_and_reasons": incompatible,
    }



def _merge_unique(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def guidance_for_request(disease: str, country_or_region: str | None = None, forecast_target: str | None = None, horizon: int | None = None, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    graph = load_graph()
    disease_node = resolve_disease(disease, graph)
    supplied = dict(profile or {})
    supplied.setdefault("disease_id", disease_node["id"])
    supplied.setdefault("country_or_region", country_or_region)
    supplied.setdefault("forecast_target", forecast_target or "cases_new")
    supplied.setdefault("horizon", horizon)
    # Disease-safe defaults are semantic, not observed-data fabrication.
    if disease_node["id"] in {"ebola", "nipah"}:
        supplied.setdefault("is_event_series", True)
    if disease_node["id"] == "hantavirus_puumala":
        supplied.setdefault("has_ecological_covariates", False)
    if disease_node["id"] == "legionella":
        supplied.setdefault("has_weather", False)
        supplied.setdefault("has_pollution", False)
    prof = DatasetProfile(**{k: v for k, v in supplied.items() if k in DatasetProfile.__dataclass_fields__})
    track = eligibility_track(prof)
    applicable = [r for r in graph["rules"] if ("*" in r["disease_ids"] or disease_node["id"] in r["disease_ids"]) and _when_matches(r.get("when", {}), prof)]
    applicable.sort(key=lambda r: (-int(r["priority"]), r["rule_id"]))
    if not applicable:
        applicable = [r for r in graph["rules"] if disease_node["id"] in r["disease_ids"]]
        applicable.sort(key=lambda r: (-int(r["priority"]), r["rule_id"]))
    rec_ids = _merge_unique(mid for r in applicable for mid in r.get("recommend", []))
    avoid_ids = _merge_unique(mid for r in applicable for mid in r.get("avoid", []))
    baseline_ids = _merge_unique(mid for r in applicable for mid in r.get("required_baselines", []))
    validation_ids = _merge_unique(mid for r in applicable for mid in r.get("required_validation", []))
    primary_ids = rec_ids[:2] if rec_ids else []
    secondary_ids = rec_ids[2:]
    warnings: list[str] = []
    missing: list[str] = []
    if disease_node["id"] == "hantavirus_puumala" and not prof.has_ecological_covariates:
        warnings.append(_zh("\\u751f\\u6001\\u534f\\u53d8\\u91cf\\u7f3a\\u5931\uff1aFinland Puumala \u53ea\u80fd\u7ed9\u51fa\u6761\u4ef6\u5316\u751f\u6001\u6a21\u578b\u5efa\u8bae\uff0c\u4e0d\u5f97\u6cdb\u5316\u4e3a\u5168\u7403 Hantavirus\u3002"))
        missing.append("has_ecological_covariates")
    if disease_node["id"] == "legionella" and not (prof.has_weather or prof.has_pollution or prof.has_climate):
        warnings.append(_zh("\\u73af\\u5883\\u534f\\u53d8\\u91cf\\u7f3a\\u5931\uff1a\u53ea\u80fd\u7ed9\u51fa\u75c5\u4f8b\u8ba1\u6570\u57fa\u7ebf\u4e0e\u964d\u7ea7\u8bf4\u660e\uff0c\u4e0d\u5f97\u5ba3\u79f0\u5b8c\u6574\u73af\u5883\u6ede\u540e\u6a21\u578b\u6761\u4ef6\u3002"))
        missing.extend(["has_weather", "has_pollution"])
    if disease_node["id"] == "nipah":
        warnings.append(_zh("Nipah \u4e3a\u7a00\u758f\u4e8b\u4ef6/\u4e8b\u5b9e\u6307\u6807\uff1b\u672a\u901a\u62a5\u65f6\u6bb5\u672a\u77e5\uff0c\u4e0d\u5f97\u8865\u96f6\u4e3a\u8fde\u7eed\u5468\u5ea6\u75c5\u4f8b\u3002"))
    if disease_node["id"] == "legionella" and country_or_region and str(country_or_region).lower() == "europe":
        warnings.append(_zh("Legionella \u5fc5\u987b\u9009\u62e9\u5177\u4f53\u56fd\u5bb6\uff1bEurope \u4e0d\u80fd\u4f5c\u4e3a\u4f2a\u56fd\u5bb6\u3002"))
    node_by_id = _node_map(graph)
    evidence_ids = _merge_unique(eid for r in applicable for eid in r.get("evidence_ids", []))
    evidence = [e for e in graph["evidence_sources"] if e["id"] in evidence_ids]
    reasons = [r["explanation_template_zh"] for r in applicable]
    result = {
        "graph_version": graph["metadata"]["content_version"],
        "graph_content_sha256": graph["metadata"]["content_sha256"],
        "request": {"disease": disease, "country_or_region": country_or_region, "forecast_target": forecast_target, "horizon": horizon},
        "resolved_disease": {k: disease_node[k] for k in ("id", "label_zh", "label_en", "description_zh", "description_en", "aliases", "limitations")},
        "dataset_profile": asdict(prof),
        "dataset_profile_contract": dataset_profile_contract(prof),
        "eligibility_track": track,
        "eligibility_decision": eligibility_decision(prof),
        "recommended_model_families": [_model_item(mid, "PRIMARY" if mid in primary_ids else "SECONDARY", _zh("\\u89c4\\u5219\\u547d\\u4e2d\\u63a8\\u8350\\u3002"), graph=graph) for mid in rec_ids],
        "recommended_models": [_model_item(mid, "PRIMARY", _zh("\\u4e3b\\u5019\\u9009\uff1b\u4ecd\u9700\u6eda\u52a8\u56de\u6d4b\u51b3\u5b9a champion\u3002"), graph=graph) for mid in primary_ids],
        "conditional_models": [_model_item(mid, "CONDITIONAL", _zh("\\u6761\\u4ef6\\u5019\\u9009\uff1b\u4f9d\u8d56\u534f\u53d8\u91cf\u3001\u5386\u53f2\u957f\u5ea6\u6216\u5916\u90e8\u9a8c\u8bc1\u3002"), graph=graph) for mid in secondary_ids],
        "models_not_recommended_as_primary": [_model_item(mid, "AVOID_AS_PRIMARY", _zh("\\u5f53\\u524d\\u753b\\u50cf\\u4e0b\\u4e0d\\u5f97\\u4f5c\\u4e3a\\u4e3b\\u6a21\\u578b\\u3002"), graph=graph) for mid in avoid_ids],
        "required_baselines": [_model_item(mid, "BASELINE_ONLY", _zh("\\u6b63\\u5f0f\\u4f7f\\u7528\\u524d\\u5fc5\\u987b\\u6bd4\\u8f83\\u7684\\u57fa\\u7ebf\\u3002"), graph=graph) for mid in baseline_ids],
        "required_validation": [{"id": mid, "label_zh": node_by_id[mid]["label_zh"], "label_en": node_by_id[mid]["label_en"]} for mid in validation_ids],
        "metrics": [{"id": "coverage_check", "label_zh": node_by_id["coverage_check"]["label_zh"], "label_en": node_by_id["coverage_check"]["label_en"]}],
        "reasons": reasons,
        "warnings": _merge_unique(warnings),
        "missing_requirements": _merge_unique(missing),
        "evidence": evidence,
        "decision_trace": [{"rule_id": r["rule_id"], "priority": r["priority"], "when": r.get("when", {})} for r in applicable],
        "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "side_effects": {"db_write": False, "training_started": False, "prediction_started": False, "external_network": False},
    }
    return result
