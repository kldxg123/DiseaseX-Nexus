from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from typing import Any

from src.eval.external_validation_common import HORIZONS, load_json, qwen_prediction_rows, write_report_pair
from src.eval.metrics import LABEL_STABLE, LABEL_UP
from src.utils.config import resolve_path


def _stable_bias(rows: list[dict[str, Any]]) -> dict[str, Any]:
    up_rows = [row for row in rows if row.get("true_label") == LABEL_UP]
    up_to_stable = [row for row in up_rows if row.get("predicted_label") == LABEL_STABLE]
    by_horizon = Counter(str(row.get("horizon_days")) for row in up_to_stable)
    by_date = Counter(str(row.get("date"))[:7] for row in up_to_stable)
    flags = Counter(flag for row in up_to_stable for flag in (row.get("data_quality_flags") or []))
    return {
        "up_support": len(up_rows),
        "up_to_stable_count": len(up_to_stable),
        "up_to_stable_rate": len(up_to_stable) / len(up_rows) if up_rows else 0.0,
        "by_horizon": dict(by_horizon),
        "top_months": dict(by_date.most_common(8)),
        "data_quality_flag_counts": dict(flags.most_common(10)),
    }


def build_external_error_analysis() -> dict[str, Any]:
    v1_rows = qwen_prediction_rows("v1")
    v2_rows = qwen_prediction_rows("v2")
    comparison = load_json("data/outputs/reports/external_model_comparison.json")
    guard = load_json("data/outputs/reports/external_guard_eval.json")
    analyses = {
        "qwen_v1": _stable_bias(v1_rows) if v1_rows else {"status": "not_available", "reason": "external_qwen_v1_predictions_missing"},
        "qwen_v2": _stable_bias(v2_rows) if v2_rows else {"status": "not_available", "reason": "external_qwen_v2_predictions_missing"},
    }
    horizon_error_counts: dict[str, dict[str, int]] = {}
    for version, rows in [("v1", v1_rows), ("v2", v2_rows)]:
        counts: dict[str, int] = {}
        for horizon in HORIZONS:
            subset = [row for row in rows if int(row.get("horizon_days") or 0) == horizon]
            counts[f"{horizon}d"] = sum(1 for row in subset if row.get("true_label") != row.get("predicted_label"))
        horizon_error_counts[version] = counts
    best = comparison.get("best_external_strategy") if isinstance(comparison.get("best_external_strategy"), dict) else {}
    result = {
        "status": "ok",
        "blocked_reason": "none",
        "stable_bias_present": any(
            isinstance(item, dict) and item.get("up_to_stable_count", 0) > 0 for item in analyses.values()
        ),
        "analyses": analyses,
        "horizon_error_counts": horizon_error_counts,
        "guinea_2021_date_concentration": {
            "v1_top_months": analyses.get("qwen_v1", {}).get("top_months"),
            "v2_top_months": analyses.get("qwen_v2", {}).get("top_months"),
        },
        "admin_match_quality_effect": "limited_review_needed; P0d admin coverage was high but some Guinea rows had unspecified ADM1.",
        "weather_feature_usefulness": "not_causal_from_validation_only; NASA/Open-Meteo features are aligned metadata and require ablation in a later non-training validation pass.",
        "distribution_shift": "external validation is Guinea 2021 / harmonized HDX context and is not directly comparable to P0a chronological holdout.",
        "supports_v3_training_now": False,
        "supports_more_external_outbreak_data": True,
        "recommended_next_action": "collect_more_external_outbreaks_and_expert_review_before_v3_training",
        "best_external_strategy": best,
        "guard_status": guard.get("status"),
        "promote_allowed": False,
    }
    write_figure(result)
    write_report(result)
    return result


def write_figure(result: dict[str, Any]) -> None:
    path = resolve_path("data/outputs/figures/external_error_breakdown.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        labels = ["v1 up->stable", "v2 up->stable"]
        values = [
            result["analyses"].get("qwen_v1", {}).get("up_to_stable_count", 0),
            result["analyses"].get("qwen_v2", {}).get("up_to_stable_count", 0),
        ]
        fig, ax = plt.subplots(figsize=(6, 4))
        ax.bar(labels, values, color=["#4C78A8", "#F58518"])
        ax.set_ylabel("count")
        ax.set_title("External up-to-stable errors")
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
    except Exception:
        path.with_suffix(".txt").write_text(json.dumps(result["analyses"], ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(result: dict[str, Any]) -> None:
    lines = [
        "# External Error Analysis",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        f"stable_bias_present: {str(result['stable_bias_present']).lower()}",
        "promote_allowed: false",
        f"supports_v3_training_now: {str(result['supports_v3_training_now']).lower()}",
        f"supports_more_external_outbreak_data: {str(result['supports_more_external_outbreak_data']).lower()}",
        f"recommended_next_action: {result['recommended_next_action']}",
        "",
        "## Up -> Stable Bias",
        "```json",
        json.dumps(result["analyses"], ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Interpretation",
        "- External validation still focuses attention on up-class miss risk, especially when Qwen predicts stable for true up windows.",
        "- The evidence is not sufficient to justify promotion or immediate v3 training.",
        "- More external outbreak data and expert-reviewed hard positives are needed before another LoRA iteration.",
        "",
        "## JSON",
        "```json",
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
    ]
    write_report_pair("external_error_analysis", result, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze P0e external validation errors.")
    parser.parse_args()
    result = build_external_error_analysis()
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "stable_bias_present": result["stable_bias_present"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
