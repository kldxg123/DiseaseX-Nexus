from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_benchmark_agent.stability import analyze_stability


def write_m2_m2_ablation_reports(payload: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    ablation_json = out / "M2_M2_FEATURE_ABLATION_RESULTS.json"
    ablation_md = out / "M2_M2_FEATURE_ABLATION_RESULTS.md"
    baseline_json = out / "M2_M2_INTERPRETABLE_BASELINE_RESULTS.json"
    baseline_md = out / "M2_M2_INTERPRETABLE_BASELINE_RESULTS.md"
    stability_json = out / "M2_M2_STABILITY_ANALYSIS.json"
    stability_md = out / "M2_M2_STABILITY_ANALYSIS.md"
    write_json(ablation_json, payload)
    ablation_md.write_text(_ablation_markdown(payload), encoding="utf-8")
    baseline_payload = payload.get("interpretable_baseline_results", {})
    write_json(baseline_json, baseline_payload)
    baseline_md.write_text(_baseline_markdown(baseline_payload), encoding="utf-8")
    stability_payload = analyze_stability(payload)
    write_json(stability_json, stability_payload)
    stability_md.write_text(_stability_markdown(stability_payload), encoding="utf-8")
    return {
        "ablation_json": str(ablation_json),
        "ablation_markdown": str(ablation_md),
        "baseline_json": str(baseline_json),
        "baseline_markdown": str(baseline_md),
        "stability_json": str(stability_json),
        "stability_markdown": str(stability_md),
    }


def write_m2_m2_selection_reports(payload: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "M2_M2_FEATURE_SELECTION_RANKING.json"
    md_path = out / "M2_M2_FEATURE_SELECTION_RANKING.md"
    write_json(json_path, payload)
    md_path.write_text(_selection_markdown(payload), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def write_m2_m2_minimum_reports(payload: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "M2_M2_MINIMUM_FEATURE_RECOMMENDATION.json"
    md_path = out / "M2_M2_MINIMUM_FEATURE_RECOMMENDATION.md"
    gap_path = out / "M2_M2_GAP_ANALYSIS_UPDATE.md"
    next_path = out / "M2_M2_NEXT_ACTION_PLAN.md"
    write_json(json_path, payload)
    md_path.write_text(_minimum_markdown(payload), encoding="utf-8")
    gap_path.write_text(_gap_markdown(payload), encoding="utf-8")
    next_path.write_text(_next_action_markdown(payload), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "gap": str(gap_path),
        "next": str(next_path),
    }


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _ablation_markdown(payload: dict[str, Any]) -> str:
    rows = payload.get("ablation_results", [])
    executed = [row for row in rows if row.get("status") == "executed"]
    lines = [
        "# M2 M2 Feature Ablation Results",
        "",
        "Offline interpretable baseline ablation. No production model is changed.",
        "",
        f"- Total runs: {len(rows)}",
        f"- Executed runs: {len(executed)}",
        "- Review required: yes",
        "",
        "| disease | horizon | model | feature_set | status | MAE | sMAPE | stability |",
        "|---|---:|---|---|---|---:|---:|---:|",
    ]
    for row in rows[:80]:
        regression = row.get("metrics", {}).get("regression", {})
        lines.append(
            f"| {row.get('disease')} | {row.get('horizon_days')} | {row.get('model_name')} | "
            f"{row.get('feature_set_name')} | {row.get('status')} | {_fmt(regression.get('mae'))} | "
            f"{_fmt(regression.get('smape'))} | {_fmt(row.get('stability_score'))} |"
        )
    if len(rows) > 80:
        lines.append(f"| ... | ... | ... | ... | ... | ... | ... | {len(rows) - 80} rows omitted |")
    lines.extend(["", "## Limitations", "", "- Results are local decision-support evidence, not guaranteed accuracy."])
    return "\n".join(lines) + "\n"


def _baseline_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# M2 M2 Interpretable Baseline Results",
        "",
        "| model | executed_runs | average_score | average_stability |",
        "|---|---:|---:|---:|",
    ]
    for row in payload.get("models", []):
        lines.append(
            f"| {row.get('model_name')} | {row.get('executed_runs')} | "
            f"{_fmt(row.get('average_score'))} | {_fmt(row.get('average_stability_score'))} |"
        )
    return "\n".join(lines) + "\n"


def _stability_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# M2 M2 Stability Analysis",
        "",
        f"- Needs more data: {payload.get('needs_more_data')}",
        f"- Fold variance: {_fmt(payload.get('fold_variance'))}",
        f"- Note: {payload.get('feature_contribution_uncertainty')}",
        "",
        "| model | feature_set | runs | average_score | variance |",
        "|---|---|---:|---:|---:|",
    ]
    for row in payload.get("stable_recommendations", [])[:20]:
        lines.append(
            f"| {row.get('model_name')} | {row.get('feature_set_name')} | {row.get('run_count')} | "
            f"{_fmt(row.get('average_score'))} | {_fmt(row.get('score_variance'))} |"
        )
    return "\n".join(lines) + "\n"


def _selection_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# M2 M2 Feature Selection Ranking",
        "",
        "| rank | feature_group | status | score | cost | stability |",
        "|---:|---|---|---:|---:|---:|",
    ]
    for row in payload.get("feature_group_rankings", []):
        lines.append(
            f"| {row.get('rank')} | {row.get('feature_group')} | {row.get('status')} | "
            f"{_fmt(row.get('total_score'))} | {_fmt(row.get('data_cost'))} | {_fmt(row.get('stability_score'))} |"
        )
    lines.extend(["", "Weather and mobility remain not-yet-ready until governed local sources are added."])
    return "\n".join(lines) + "\n"


def _minimum_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# M2 M2 Minimum Feature Recommendation",
        "",
        "| disease | minimal_safe_feature_set | recommended_feature_set | missing_high_value |",
        "|---|---|---|---|",
    ]
    for row in payload.get("recommendations", []):
        lines.append(
            f"| {row.get('disease')} | {', '.join(row.get('minimal_safe_feature_set', []))} | "
            f"{', '.join(row.get('recommended_feature_set', []))} | "
            f"{', '.join(row.get('missing_but_high_value_feature_set', []))} |"
        )
    lines.extend(["", "These sets are advisory and require analyst review."])
    return "\n".join(lines) + "\n"


def _gap_markdown(payload: dict[str, Any]) -> str:
    missing = ", ".join(payload.get("global_not_yet_ready_feature_groups", [])) or "none"
    return "\n".join(
        [
            "# M2 M2 Gap Analysis Update",
            "",
            f"- Not-yet-ready high-value feature groups: {missing}.",
            "- Additional governed local data is required before these groups can enter production backtests.",
            "- No automatic retraining or model replacement is performed.",
        ]
    ) + "\n"


def _next_action_markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# M2 M2 Next Action Plan",
            "",
            "1. Analyst reviews the minimum feature recommendation.",
            "2. Decide whether to add governed local data for not-yet-ready groups.",
            "3. Re-run offline ablation after new local features are available.",
            "4. Keep production predictor unchanged until governance approval.",
        ]
    ) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return ""


__all__ = [
    "load_json",
    "write_json",
    "write_m2_m2_ablation_reports",
    "write_m2_m2_minimum_reports",
    "write_m2_m2_selection_reports",
]
