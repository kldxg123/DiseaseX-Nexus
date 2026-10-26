from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from model_benchmark_agent.benchmark_matrix import matrix_to_markdown
from model_benchmark_agent.feature_groups import build_feature_availability_report
from model_benchmark_agent.ranking import ranking_to_markdown


def write_benchmark_matrix_reports(matrix: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "M2_M1_BENCHMARK_MATRIX.json"
    md_path = out / "M2_M1_BENCHMARK_MATRIX.md"
    write_json(json_path, matrix)
    md_path.write_text(matrix_to_markdown(matrix), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def write_feature_availability_report(records: list[Any], output_dir: str | Path) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    report = build_feature_availability_report(records)
    json_path = out / "M2_M1_FEATURE_AVAILABILITY_REPORT.json"
    md_path = out / "M2_M1_FEATURE_AVAILABILITY_REPORT.md"
    write_json(json_path, report)
    lines = [
        "# M2 M1 Feature Availability Report",
        "",
        f"- Available feature key count: {report['available_feature_key_count']}",
        "",
        "| feature_group | status | present | missing |",
        "|---|---|---:|---:|",
    ]
    for group in report["groups"]:
        lines.append(
            f"| {group['name']} | {group['availability_status']} | "
            f"{len(group['present_keys'])} | {len(group['missing_keys'])} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- Weather and mobility are planned extensions unless corresponding local feature fields exist.",
            "- Missing fields are warnings, not benchmark failures.",
        ]
    )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def write_backtest_reports(results: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "M2_M1_BACKTEST_RESULTS.json"
    md_path = out / "M2_M1_BACKTEST_RESULTS.md"
    write_json(json_path, results)
    rows = results.get("results", [])
    executed = [row for row in rows if row.get("status") in {"executed", "simulated_proxy", "fallback_hist_gradient_boosting_proxy"}]
    skipped = [row for row in rows if row.get("status") in {"skipped", "insufficient_data", "failed"}]
    lines = [
        "# M2 M1 Backtest Results",
        "",
        "Local rolling-origin benchmark results. No production predictor changes are made.",
        "",
        f"- Result count: {len(rows)}",
        f"- Executed/proxy count: {len(executed)}",
        f"- Skipped/insufficient/failed count: {len(skipped)}",
        "",
        "| task_id | model | status | horizon | MAE | RMSE | sMAPE | warnings |",
        "|---|---|---|---:|---:|---:|---:|---|",
    ]
    for row in rows[:80]:
        regression = row.get("metrics", {}).get("regression", {})
        lines.append(
            f"| `{row['task_id']}` | {row['model_name']} | {row['status']} | {row['horizon_days']} | "
            f"{_fmt(regression.get('mae'))} | {_fmt(regression.get('rmse'))} | "
            f"{_fmt(regression.get('smape'))} | {', '.join(row.get('warnings', [])[:3])} |"
        )
    if len(rows) > 80:
        lines.append(f"| ... | ... | ... | ... | ... | ... | ... | {len(rows) - 80} additional rows omitted |")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def write_ranking_reports(ranking: dict[str, Any], output_dir: str | Path) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "M2_M1_MODEL_FEATURE_RANKING.json"
    md_path = out / "M2_M1_MODEL_FEATURE_RANKING.md"
    write_json(json_path, ranking)
    md_path.write_text(ranking_to_markdown(ranking), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def write_gap_and_next_action_reports(
    *,
    ranking: dict[str, Any],
    output_dir: str | Path,
) -> dict[str, str]:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    top = (ranking.get("top_5") or [{}])[0]
    gap_path = out / "M2_M1_GAP_ANALYSIS_UPDATE.md"
    next_path = out / "M2_M1_NEXT_ACTION_PLAN.md"
    gap_lines = [
        "# M2 M1 Gap Analysis Update",
        "",
        "- Optional dependency models may be skipped or proxy-only depending on local environment.",
        "- Weather and mobility feature groups remain planned unless local feature fields are added.",
        "- Real literature discovery is still a separate future step; M2 M1 uses local M2 M0 evidence outputs.",
        "- Deep learning, transformer, graph, and LLM agent candidates are deferred due to data, compute, and governance risk.",
        "",
        f"Current top benchmark candidate: {top.get('model_name', 'n/a')} with {', '.join(top.get('feature_groups', [])) if top else 'n/a'}.",
    ]
    next_lines = [
        "# M2 M1 Next Action Plan",
        "",
        "1. Review benchmark matrix and top ranked model/feature combinations with an analyst.",
        "2. Decide whether to run controlled real literature discovery for model evidence.",
        "3. Decide whether to expand M1 data foundation with weather and mobility sources.",
        "4. Keep production predictor unchanged until governance review approves any promotion.",
    ]
    gap_path.write_text("\n".join(gap_lines) + "\n", encoding="utf-8")
    next_path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
    return {"gap": str(gap_path), "next": str(next_path)}


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return ""


__all__ = [
    "write_backtest_reports",
    "write_benchmark_matrix_reports",
    "write_feature_availability_report",
    "write_gap_and_next_action_reports",
    "write_json",
    "write_ranking_reports",
]
