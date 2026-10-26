from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Any

from model_benchmark_agent.types import FeatureGroupSpec


FEATURE_GROUPS: dict[str, FeatureGroupSpec] = {
    "epidemiological_lag": FeatureGroupSpec(
        name="epidemiological_lag",
        description="Lagged case counts and current case signals.",
        feature_keys=["current_week_cases", "lag_1w_cases", "lag_2w_cases", "lag_4w_cases"],
        m1_status="available",
    ),
    "rolling_statistics": FeatureGroupSpec(
        name="rolling_statistics",
        description="Rolling means, sums, and trend statistics.",
        feature_keys=["rolling_4w_mean_cases", "rolling_4w_sum_cases", "rolling_8w_mean_cases"],
        m1_status="available",
    ),
    "seasonality": FeatureGroupSpec(
        name="seasonality",
        description="Calendar and seasonal encodings.",
        feature_keys=["iso_week_number", "month", "season_sin", "season_cos"],
        m1_status="available",
    ),
    "testing": FeatureGroupSpec(
        name="testing",
        description="Testing volume and testing intensity signals.",
        feature_keys=["tests_performed", "testing_volume", "rolling_4w_tests"],
        m1_status="available",
    ),
    "positivity": FeatureGroupSpec(
        name="positivity",
        description="Positivity rate and rolling positivity statistics.",
        feature_keys=["positivity_rate", "rolling_4w_positivity"],
        m1_status="available",
    ),
    "source_signal": FeatureGroupSpec(
        name="source_signal",
        description="Signals derived from source coverage and reporting volume.",
        feature_keys=["source_count", "official_source_count", "news_source_count"],
        m1_status="available",
    ),
    "data_quality": FeatureGroupSpec(
        name="data_quality",
        description="Data quality, missingness, and source reliability indicators.",
        feature_keys=["quality_score", "source_reliability_score", "missing_value_rate"],
        m1_status="available",
    ),
    "weather": FeatureGroupSpec(
        name="weather",
        description="Weather covariates for future model extensions.",
        feature_keys=["temperature_mean", "humidity_mean", "precipitation_mm"],
        m1_status="planned",
        warnings=["Weather features are not part of the current M1 data foundation."],
    ),
    "mobility": FeatureGroupSpec(
        name="mobility",
        description="Mobility covariates for future model extensions.",
        feature_keys=["mobility_index", "transit_flow_index", "workplace_mobility"],
        m1_status="planned",
        warnings=["Mobility features are not part of the current M1 data foundation."],
    ),
}


FEATURE_GROUP_SETS: dict[str, list[str]] = {
    "baseline_minimal": ["epidemiological_lag"],
    "lag_rolling_seasonality": [
        "epidemiological_lag",
        "rolling_statistics",
        "seasonality",
    ],
    "lag_rolling_testing_positivity": [
        "epidemiological_lag",
        "rolling_statistics",
        "testing",
        "positivity",
    ],
    "all_m1_available": [
        "epidemiological_lag",
        "rolling_statistics",
        "seasonality",
        "testing",
        "positivity",
        "source_signal",
        "data_quality",
    ],
    "source_quality_augmented": [
        "epidemiological_lag",
        "rolling_statistics",
        "seasonality",
        "source_signal",
        "data_quality",
    ],
}


def get_feature_group_registry() -> dict[str, FeatureGroupSpec]:
    return dict(FEATURE_GROUPS)


def get_feature_group_sets() -> dict[str, list[str]]:
    return {name: list(groups) for name, groups in FEATURE_GROUP_SETS.items()}


def get_feature_group_spec(name: str) -> FeatureGroupSpec:
    normalized = normalize_feature_group_name(name)
    try:
        return FEATURE_GROUPS[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown feature group: {name}") from exc


def normalize_feature_group_name(name: str) -> str:
    return (name or "").strip().lower().replace("-", "_").replace(" ", "_")


def feature_keys_for_groups(group_names: Iterable[str]) -> list[str]:
    keys: list[str] = []
    for group_name in group_names:
        spec = get_feature_group_spec(group_name)
        keys.extend(spec.feature_keys)
    return sorted(set(keys))


def filter_feature_json(feature_json: dict[str, Any], group_names: Iterable[str]) -> dict[str, Any]:
    allowed = set(feature_keys_for_groups(group_names))
    return {key: value for key, value in dict(feature_json or {}).items() if key in allowed}


def detect_feature_keys(records: Iterable[Any]) -> set[str]:
    keys: set[str] = set()
    for record in records:
        feature_json = extract_feature_json(record)
        keys.update(feature_json.keys())
    return keys


def extract_feature_json(record: Any) -> dict[str, Any]:
    if isinstance(record, dict):
        if isinstance(record.get("feature_json"), dict):
            return dict(record["feature_json"])
        return {key: value for key, value in record.items() if _looks_like_feature_key(key)}
    feature_json = getattr(record, "feature_json", None)
    return dict(feature_json or {})


def build_feature_availability_report(records: Iterable[Any] | None = None) -> dict[str, Any]:
    available_keys = detect_feature_keys(records or [])
    groups: list[dict[str, Any]] = []
    status_counter: Counter[str] = Counter()
    for spec in FEATURE_GROUPS.values():
        present = sorted(key for key in spec.feature_keys if key in available_keys)
        missing = sorted(key for key in spec.feature_keys if key not in available_keys)
        if spec.m1_status in {"planned", "not_available"}:
            status = spec.m1_status
        elif not available_keys:
            status = "unknown_no_records"
        elif present:
            status = "available" if not missing else "partially_available"
        else:
            status = "missing"
        status_counter[status] += 1
        warnings = list(spec.warnings)
        if status in {"missing", "partially_available", "unknown_no_records"}:
            warnings.append("Feature group is not fully represented in the supplied model_features.")
        groups.append(
            {
                "name": spec.name,
                "description": spec.description,
                "m1_status": spec.m1_status,
                "availability_status": status,
                "present_keys": present,
                "missing_keys": missing,
                "requires_human_review": spec.requires_human_review,
                "warnings": warnings,
            }
        )
    return {
        "available_feature_key_count": len(available_keys),
        "available_feature_keys": sorted(available_keys),
        "groups": groups,
        "status_counts": dict(status_counter),
        "limitations": [
            "Availability is inferred from local model_features or synthetic fixtures only.",
            "Missing fields do not fail the benchmark; they are carried as warnings.",
        ],
    }


def _looks_like_feature_key(key: str) -> bool:
    return key.endswith(("_cases", "_rate", "_score", "_count", "_mean", "_sum")) or key in {
        "month",
        "iso_week_number",
        "season_sin",
        "season_cos",
    }


__all__ = [
    "FEATURE_GROUPS",
    "FEATURE_GROUP_SETS",
    "build_feature_availability_report",
    "detect_feature_keys",
    "extract_feature_json",
    "feature_keys_for_groups",
    "filter_feature_json",
    "get_feature_group_registry",
    "get_feature_group_sets",
    "get_feature_group_spec",
    "normalize_feature_group_name",
]
