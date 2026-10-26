from __future__ import annotations

import re

from epidemic_core.forecast_model_kg.r4_shadow import ForecastOrchestrator, ModelRegistry


def test_monthly_seasonal_naive_uses_corresponding_prior_month_for_each_horizon() -> None:
    orch = ForecastOrchestrator.__new__(ForecastOrchestrator)
    spec = ModelRegistry().models["seasonal_naive_12_month"]
    history = [{"value": v} for v in range(1, 25)]
    got = [ForecastOrchestrator.predict_value(orch, history, spec, {"season_lag": 12}, 24 + h, h) for h in range(1, 5)]
    assert got == [13.0, 14.0, 15.0, 16.0]


def test_weekly_seasonal_naive_uses_horizon_specific_prior_week() -> None:
    orch = ForecastOrchestrator.__new__(ForecastOrchestrator)
    spec = ModelRegistry().models["seasonal_naive_52_week"]
    history = [{"value": v} for v in range(1, 110)]
    got = [ForecastOrchestrator.predict_value(orch, history, spec, {"season_lag": 52}, 109 + h, h) for h in range(1, 4)]
    assert got == [58.0, 59.0, 60.0]


def test_registry_contains_executable_baselines() -> None:
    names = {m["name"] for m in ModelRegistry().executable()}
    assert {"last_value_baseline", "rolling_mean_baseline", "seasonal_naive_12_month", "seasonal_naive_52_week"} <= names


def test_known_truncated_doi_patterns_are_detectable() -> None:
    rx = re.compile(r"^work:doi:10\.[0-9]+/([a-z]?|j|ja|s|n|e|bi|s1|s4|s41|978-|j\.e|3)?$", re.I)
    for key in [
        "work:doi:10.1038/s4",
        "work:doi:10.1016/j.e",
        "work:doi:10.1038/3",
        "work:doi:10.1007/s1",
        "work:doi:10.1093/bi",
        "work:doi:10.1038/s41",
    ]:
        assert rx.match(key)


def test_full_doi_is_not_detected_as_truncated_prefix() -> None:
    rx = re.compile(r"^work:doi:10\.[0-9]+/([a-z]?|j|ja|s|n|e|bi|s1|s4|s41|978-|j\.e|3)?$", re.I)
    assert not rx.match("work:doi:10.1038/s41586-023-06617-0")
