from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.utils.config import load_config, resolve_path
from src.utils.io import ensure_dir, write_json


COMPARISONS = [
    ("cases_weather", "weather_lift_vs_cases_only"),
    ("cases_mobility_proxy", "mobility_lift_vs_cases_only"),
    ("cases_weather_mobility_proxy", "weather_mobility_lift_vs_cases_only"),
]


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def run_feature_ablation(config_path: str) -> dict[str, Any]:
    cfg = load_config(config_path)
    reports = resolve_path(cfg["paths"]["outputs"]) / "reports"
    baseline = _load_json(reports / "baseline_results.json")
    if not baseline:
        return {"status": "blocked", "blocked_reason": "missing_baseline_results", "metric_source": "chronological_70_30_holdout"}
    rows: dict[str, Any] = {}
    weather_improved = False
    mobility_improved = False
    for horizon, feature_sets in baseline.get("horizons", {}).items():
        base = feature_sets.get("cases_only", {})
        base_macro = base.get("metrics", {}).get("macro_f1")
        rows[horizon] = {}
        for feature_set, comparison_name in COMPARISONS:
            candidate = feature_sets.get(feature_set, {})
            candidate_macro = candidate.get("metrics", {}).get("macro_f1")
            lift = None if base_macro is None or candidate_macro is None else candidate_macro - base_macro
            rows[horizon][comparison_name] = {
                "feature_set": feature_set,
                "base_macro_f1": base_macro,
                "candidate_macro_f1": candidate_macro,
                "macro_f1_lift": lift,
            }
            if comparison_name.startswith("weather") and lift is not None and lift > 0:
                weather_improved = True
            if comparison_name.startswith("mobility") and lift is not None and lift > 0:
                mobility_improved = True
    return {
        "status": "ok",
        "blocked_reason": "none",
        "metric_source": "chronological_70_30_holdout",
        "weather_improved_any_horizon": weather_improved,
        "mobility_proxy_improved_any_horizon": mobility_improved,
        "comparisons": rows,
    }


def write_feature_ablation_report(result: dict[str, Any], config_path: str) -> None:
    cfg = load_config(config_path)
    reports = ensure_dir(resolve_path(cfg["paths"]["outputs"]) / "reports")
    write_json(reports / "feature_ablation.json", result)
    lines = [
        "# Feature Ablation",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        "metric_source: chronological_70_30_holdout",
        f"weather_improved_any_horizon: {str(result.get('weather_improved_any_horizon', False)).lower()}",
        f"mobility_proxy_improved_any_horizon: {str(result.get('mobility_proxy_improved_any_horizon', False)).lower()}",
        "",
        "| horizon | comparison | base_macro_f1 | candidate_macro_f1 | macro_f1_lift |",
        "|---|---|---:|---:|---:|",
    ]
    for horizon, comparisons in result.get("comparisons", {}).items():
        for name, item in comparisons.items():
            lift = item.get("macro_f1_lift")
            lines.append(
                f"| {horizon} | {name} | {item.get('base_macro_f1')} | "
                f"{item.get('candidate_macro_f1')} | {'' if lift is None else f'{lift:.4f}'} |"
            )
    (reports / "feature_ablation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare P0a feature set ablations using holdout metrics.")
    parser.add_argument("--config", default="configs/config.yaml")
    parser.add_argument("--horizons", nargs="*", type=int, default=[7, 14, 21, 30])
    args = parser.parse_args()
    result = run_feature_ablation(args.config)
    write_feature_ablation_report(result, args.config)
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"]}, ensure_ascii=False))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
