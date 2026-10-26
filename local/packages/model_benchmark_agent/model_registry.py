from __future__ import annotations

import importlib.util

from model_benchmark_agent.types import ModelSpec


def optional_dependency_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def get_model_registry() -> dict[str, ModelSpec]:
    sklearn_available = optional_dependency_available("sklearn")
    lightgbm_available = optional_dependency_available("lightgbm")
    statsmodels_available = optional_dependency_available("statsmodels")

    specs = {
        "moving_average": ModelSpec(
            model_name="moving_average",
            model_family="baseline",
            benchmark_strategy="moving_average",
            execution_status="executable",
            data_requirement="Very low; works with short weekly time series.",
            interpretability="high",
            compute_risk="low",
            leakage_risk="low",
            recommended_for_m2_m1=True,
        ),
        "seasonal_naive": ModelSpec(
            model_name="seasonal_naive",
            model_family="baseline",
            benchmark_strategy="seasonal_naive",
            execution_status="executable",
            data_requirement="Low to medium; best with enough seasonal history but has fallback.",
            interpretability="high",
            compute_risk="low",
            leakage_risk="low",
            recommended_for_m2_m1=True,
        ),
        "random_forest": _sklearn_spec(
            name="random_forest",
            family="tree_ensemble",
            sklearn_available=sklearn_available,
            data_requirement="Medium; needs enough examples and careful leakage controls.",
            interpretability="medium",
        ),
        "gradient_boosting": _sklearn_spec(
            name="gradient_boosting",
            family="tree_ensemble",
            sklearn_available=sklearn_available,
            data_requirement="Medium; sensitive to leakage and small sample variance.",
            interpretability="medium",
        ),
        "lightgbm": _lightgbm_spec(lightgbm_available, sklearn_available),
        "arima_or_sarima": ModelSpec(
            model_name="arima_or_sarima",
            model_family="statistical_time_series",
            benchmark_strategy="seasonal_naive" if statsmodels_available else "skip",
            execution_status="simulated_proxy" if statsmodels_available else "skipped_optional_dependency",
            data_requirement="Medium; requires stable regular time series.",
            interpretability="medium",
            compute_risk="medium",
            leakage_risk="low",
            recommended_for_m2_m1=statsmodels_available,
            skip_reason=None if statsmodels_available else "statsmodels is not available.",
            warnings=(
                ["M2 M1 uses seasonal_naive proxy; no ARIMA/SARIMA fitting is performed."]
                if statsmodels_available
                else []
            ),
        ),
        "feature_rule_baseline": _m2_m2_interpretable_spec(
            "feature_rule_baseline",
            "Deterministic rules over lag, rolling, positivity, and source-quality signals.",
        ),
        "weighted_signal_baseline": _m2_m2_interpretable_spec(
            "weighted_signal_baseline",
            "Weighted feature-group signal model with transparent group contributions.",
        ),
        "lag_rolling_linear_baseline": _m2_m2_interpretable_spec(
            "lag_rolling_linear_baseline",
            "Small regularized linear baseline over lag, rolling, and positivity features.",
        ),
        "positivity_adjusted_baseline": _m2_m2_interpretable_spec(
            "positivity_adjusted_baseline",
            "Rolling/lag case baseline adjusted by testing and positivity signals.",
        ),
        "lstm_or_temporal_deep_learning": _deferred_high_risk(
            "lstm_or_temporal_deep_learning",
            "deep_learning",
            "Requires larger labeled time series, GPU/compute planning, and strong leakage controls.",
        ),
        "transformer_or_foundation_model": _deferred_high_risk(
            "transformer_or_foundation_model",
            "foundation_model",
            "Not appropriate for first benchmark ranking on small local fixture data.",
        ),
        "graph_or_mobility_model": _deferred_high_risk(
            "graph_or_mobility_model",
            "graph_model",
            "Requires mobility/contact graph data that is not available in current M1 data base.",
        ),
        "llm_agent": _deferred_high_risk(
            "llm_agent",
            "llm_agent",
            "External LLM calls and agentic model selection are out of scope for M2 M1.",
        ),
    }
    return specs


def get_benchmark_model_specs() -> list[ModelSpec]:
    registry = get_model_registry()
    return [
        registry[name]
        for name in (
            "moving_average",
            "seasonal_naive",
            "random_forest",
            "gradient_boosting",
            "lightgbm",
            "arima_or_sarima",
        )
    ]


def get_m2_m2_model_specs() -> list[ModelSpec]:
    registry = get_model_registry()
    return [
        registry[name]
        for name in (
            "feature_rule_baseline",
            "weighted_signal_baseline",
            "lag_rolling_linear_baseline",
            "positivity_adjusted_baseline",
        )
    ]


def get_model_spec(name: str) -> ModelSpec:
    normalized = normalize_model_name(name)
    try:
        return get_model_registry()[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown model candidate: {name}") from exc


def normalize_model_name(name: str) -> str:
    normalized = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "moving_average_baseline": "moving_average",
        "hist_gradient_boosting": "gradient_boosting",
        "sarima": "arima_or_sarima",
        "arima": "arima_or_sarima",
    }
    return aliases.get(normalized, normalized)


def _sklearn_spec(
    *,
    name: str,
    family: str,
    sklearn_available: bool,
    data_requirement: str,
    interpretability: str,
) -> ModelSpec:
    if sklearn_available:
        return ModelSpec(
            model_name=name,
            model_family=family,
            benchmark_strategy="feature_rule",
            execution_status="simulated_proxy",
            data_requirement=data_requirement,
            interpretability=interpretability,
            compute_risk="medium",
            leakage_risk="medium",
            recommended_for_m2_m1=True,
            warnings=[
                "M2 M1 does not train sklearn estimators; it runs a deterministic feature_rule proxy."
            ],
        )
    return ModelSpec(
        model_name=name,
        model_family=family,
        benchmark_strategy="skip",
        execution_status="skipped_optional_dependency",
        data_requirement=data_requirement,
        interpretability=interpretability,
        compute_risk="medium",
        leakage_risk="medium",
        recommended_for_m2_m1=False,
        skip_reason="scikit-learn is not available.",
    )


def _lightgbm_spec(lightgbm_available: bool, sklearn_available: bool) -> ModelSpec:
    if lightgbm_available:
        return ModelSpec(
            model_name="lightgbm",
            model_family="gradient_boosting",
            benchmark_strategy="feature_rule",
            execution_status="simulated_proxy",
            data_requirement="Medium; requires enough rows and leakage-safe feature engineering.",
            interpretability="medium",
            compute_risk="medium",
            leakage_risk="medium",
            recommended_for_m2_m1=True,
            warnings=["M2 M1 uses a deterministic proxy; no LightGBM fitting is performed."],
        )
    if sklearn_available:
        return ModelSpec(
            model_name="lightgbm",
            model_family="gradient_boosting",
            benchmark_strategy="feature_rule",
            execution_status="fallback_hist_gradient_boosting_proxy",
            data_requirement="Medium; fallback would use sklearn HistGradientBoosting later.",
            interpretability="medium",
            compute_risk="medium",
            leakage_risk="medium",
            recommended_for_m2_m1=True,
            warnings=[
                "LightGBM is unavailable; M2 M1 records a sklearn fallback path and runs feature_rule proxy."
            ],
        )
    return ModelSpec(
        model_name="lightgbm",
        model_family="gradient_boosting",
        benchmark_strategy="skip",
        execution_status="skipped_optional_dependency",
        data_requirement="Medium; LightGBM or sklearn fallback required.",
        interpretability="medium",
        compute_risk="medium",
        leakage_risk="medium",
        recommended_for_m2_m1=False,
        skip_reason="Neither lightgbm nor scikit-learn is available.",
    )


def _m2_m2_interpretable_spec(name: str, requirement: str) -> ModelSpec:
    return ModelSpec(
        model_name=name,
        model_family="interpretable_baseline",
        benchmark_strategy=name,
        execution_status="executable",
        data_requirement=requirement,
        interpretability="high",
        compute_risk="low",
        leakage_risk="low",
        recommended_for_m2_m1=False,
        recommended_for_m2_m2=True,
        optional_dependency_required=False,
        evidence_source="M2_M2",
        warnings=[
            "Local interpretable baseline only; not a production model promotion.",
            "Predictions require analyst review and uncertainty interpretation.",
        ],
    )


def _deferred_high_risk(name: str, family: str, reason: str) -> ModelSpec:
    return ModelSpec(
        model_name=name,
        model_family=family,
        benchmark_strategy="skip",
        execution_status="deferred_high_risk",
        data_requirement=reason,
        interpretability="low",
        compute_risk="high",
        leakage_risk="high",
        recommended_for_m2_m1=False,
        skip_reason=reason,
        warnings=["Deferred by safety policy; no deep learning or external LLM training in M2 M1."],
    )


__all__ = [
    "get_benchmark_model_specs",
    "get_m2_m2_model_specs",
    "get_model_registry",
    "get_model_spec",
    "normalize_model_name",
    "optional_dependency_available",
]
