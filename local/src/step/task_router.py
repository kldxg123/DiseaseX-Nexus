from __future__ import annotations


def route_stimulus(stimulus: dict) -> list[str]:
    kind = stimulus.get("type")
    if kind == "case_stimulus":
        return ["T_BUILD_FEATURES", "T_BUILD_LABELS", "T_INFER_TREND"]
    if kind == "data_quality_stimulus":
        return ["T_GEO_ALIGN", "T_BUILD_FEATURES"]
    if kind == "model_drift_stimulus":
        return ["T_EVALUATE_BACKTEST", "T_UPDATE_EXPERIENCE"]
    return ["T_INFER_TREND"]
