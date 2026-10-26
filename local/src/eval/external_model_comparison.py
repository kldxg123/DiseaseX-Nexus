from __future__ import annotations

import argparse
import json
from typing import Any

from src.eval.external_guard_eval import load_frozen_thresholds, risk_flag
from src.eval.external_validation_common import (
    HORIZONS,
    extract_metric_summary,
    load_json,
    metric_bundle,
    qwen_prediction_rows,
    read_external_samples,
    sample_true_label,
    write_report_pair,
)
from src.eval.metrics import LABEL_STABLE, LABEL_UP
from src.utils.config import resolve_path


def _qwen_plus_guard(version: str) -> dict[str, Any]:
    thresholds = load_frozen_thresholds()
    rows = qwen_prediction_rows(version)
    if not thresholds:
        return {"status": "not_available", "reason": "missing_guard_thresholds"}
    if not rows:
        return {"status": "not_available", "reason": f"external_qwen_{version}_predictions_missing"}
    samples_by_key: dict[tuple[str, int], dict[str, Any]] = {}
    for horizon in HORIZONS:
        for sample in read_external_samples(horizon):
            samples_by_key[(str(sample.get("sample_id")), horizon)] = sample
    y_true: list[str] = []
    y_pred: list[str] = []
    by_horizon: dict[str, Any] = {}
    for row in rows:
        horizon = int(row.get("horizon_days") or 0)
        sample = samples_by_key.get((str(row.get("sample_id")), horizon), {})
        pred = str(row.get("predicted_label"))
        if pred == LABEL_STABLE and sample and risk_flag(sample, thresholds):
            pred = LABEL_UP
        y_true.append(str(row.get("true_label")))
        y_pred.append(pred)
    overall = metric_bundle(y_true, y_pred)
    for horizon in HORIZONS:
        subset = [row for row in rows if int(row.get("horizon_days") or 0) == horizon]
        if horizon == 30 and not subset:
            by_horizon[f"{horizon}d"] = {"status": "not_available", "reason": "no_external_test_samples", "sample_count": 0}
            continue
        yy: list[str] = []
        pp: list[str] = []
        for row in subset:
            sample = samples_by_key.get((str(row.get("sample_id")), horizon), {})
            pred = str(row.get("predicted_label"))
            if pred == LABEL_STABLE and sample and risk_flag(sample, thresholds):
                pred = LABEL_UP
            yy.append(str(row.get("true_label")))
            pp.append(pred)
        by_horizon[f"{horizon}d"] = {
            "status": "ok",
            "sample_count": len(subset),
            "low_n_warning": "low_n_warning" if horizon == 21 and subset else None,
            "metrics": metric_bundle(yy, pp),
        }
    return {
        "status": "ok",
        "metric_source": f"P0e external test; Qwen {version} plus frozen P0c-4A case_growth_safety_guard",
        "threshold_source": "P0c-4A validation tuning",
        "metrics": overall,
        "horizon_metrics": by_horizon,
    }


def _row(name: str, status: str, source: str, sample_count: int, metrics: dict[str, Any] | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    summary = extract_metric_summary(metrics or {})
    return {
        "strategy": name,
        "status": status,
        "metric_source": source,
        "sample_count": sample_count,
        **summary,
        **(extra or {}),
        "promote_allowed": False,
        "promote_gate_passed": False,
    }


def _qwen_row(version: str, report: dict[str, Any]) -> dict[str, Any]:
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    return _row(
        f"Qwen LoRA {version}",
        report.get("status", "missing"),
        report.get("metric_source", f"P0e external test; Qwen {version}"),
        int(report.get("evaluated_records") or 0),
        metrics,
        {"json_validity": report.get("json_validity_rate"), "blocked_reason": report.get("blocked_reason", "none")},
    )


def build_external_model_comparison() -> dict[str, Any]:
    dataset = load_json("data/outputs/reports/external_validation_dataset_report.json")
    baseline = load_json("data/outputs/reports/external_baseline_eval.json")
    guard = load_json("data/outputs/reports/external_guard_eval.json")
    qwen_v1 = load_json("data/outputs/reports/external_qwen_v1_eval.json")
    qwen_v2 = load_json("data/outputs/reports/external_qwen_v2_eval.json")
    v1_guard = _qwen_plus_guard("v1")
    v2_guard = _qwen_plus_guard("v2")
    total = int(dataset.get("external_test_total") or 0)
    rows: list[dict[str, Any]] = []
    rows.append(
        _row(
            "P0a baseline",
            baseline.get("status", "missing"),
            "P0e external test; saved P0a artifact only; no retraining",
            total,
            None,
            {"reason": baseline.get("reason"), "json_validity": None},
        )
    )
    guard_7d = guard.get("horizons", {}).get("7d", {}).get("tri_class_mode", {}).get("metrics", {})
    guard_overall_metrics = guard_7d if guard_7d else None
    rows.append(
        _row(
            "case_growth_safety_guard",
            guard.get("status", "missing"),
            "P0e external test; frozen P0c-4A validation thresholds; 7d shown for guard-only summary",
            len(read_external_samples(7)),
            guard_overall_metrics,
            {"json_validity": None},
        )
    )
    rows.append(_qwen_row("v1", qwen_v1))
    rows.append(_qwen_row("v2", qwen_v2))
    for name, payload in [("Qwen v1 + guard", v1_guard), ("Qwen v2 + guard", v2_guard)]:
        rows.append(
            _row(
                name,
                payload.get("status", "not_available"),
                payload.get("metric_source", f"P0e external test; {name}"),
                sum(item.get("sample_count", 0) for item in payload.get("horizon_metrics", {}).values()) if payload.get("status") == "ok" else 0,
                payload.get("metrics") if isinstance(payload.get("metrics"), dict) else None,
                {"json_validity": None, "reason": payload.get("reason")},
            )
        )
    available = [row for row in rows if row.get("status") == "ok" and isinstance(row.get("up_recall"), (int, float))]
    best = max(available, key=lambda row: (row.get("up_recall") or 0, row.get("macro_f1") or 0), default=None)
    result = {
        "status": "ok" if dataset.get("status") == "ok" else "blocked",
        "blocked_reason": "none" if dataset.get("status") == "ok" else "external_validation_dataset_not_ok",
        "comparison_limited": True,
        "comparison_limited_reason": "Guinea 2021 external test has small samples, 21d low_n, and 30d no external test; no metrics imply promotion.",
        "rows": rows,
        "best_external_strategy": best,
        "horizon_notes": {
            "7d": dataset.get("horizons", {}).get("7d", {}),
            "14d": dataset.get("horizons", {}).get("14d", {}),
            "21d": {"low_n_warning": True, **(dataset.get("horizons", {}).get("21d", {}))},
            "30d": {"status": "not_available", "reason": "no_external_test_samples"},
        },
        "qwen_standalone_warning_status": "not_recommended",
        "promote_allowed": False,
        "allow_auto_promote": False,
        "recommended_current_strategy": "v1_guard_for_research_external_review"
        if best and best.get("strategy") == "Qwen v1 + guard"
        else "external_validation_limited_continue_data_collection",
    }
    write_figure(result)
    write_report(result)
    return result


def write_figure(result: dict[str, Any]) -> None:
    path = resolve_path("data/outputs/figures/external_model_comparison.png")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        rows = [row for row in result["rows"] if isinstance(row.get("up_recall"), (int, float))]
        names = [row["strategy"] for row in rows]
        up = [row.get("up_recall") or 0 for row in rows]
        macro = [row.get("macro_f1") or 0 for row in rows]
        false_alarm = [row.get("false_alarm_rate") or 0 for row in rows]
        fig, ax = plt.subplots(figsize=(9, 4.5))
        x = list(range(len(names)))
        ax.bar([i - 0.25 for i in x], up, width=0.25, label="up recall")
        ax.bar(x, macro, width=0.25, label="macro-F1")
        ax.bar([i + 0.25 for i in x], false_alarm, width=0.25, label="false alarm")
        ax.set_xticks(x, names, rotation=25, ha="right")
        ax.set_ylim(0, 1)
        ax.legend()
        fig.tight_layout()
        fig.savefig(path, dpi=150)
        plt.close(fig)
    except Exception:
        path.with_suffix(".txt").write_text(json.dumps(result.get("rows", []), ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(result: dict[str, Any]) -> None:
    def fmt(value: Any) -> str:
        return "" if value is None else f"{float(value):.4f}"

    lines = [
        "# External Model Comparison",
        "",
        f"status: {result['status']}",
        f"blocked_reason: {result['blocked_reason']}",
        "comparison_limited: true",
        f"comparison_limited_reason: {result['comparison_limited_reason']}",
        "promote_allowed: false",
        "",
        "| strategy | status | metric source | samples | macro-F1 | balanced accuracy | up recall | up precision | false alarm | JSON validity |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in result["rows"]:
        lines.append(
            f"| {row['strategy']} | {row['status']} | {row['metric_source']} | {row.get('sample_count', 0)} | "
            f"{fmt(row.get('macro_f1'))} | "
            f"{fmt(row.get('balanced_accuracy'))} | "
            f"{fmt(row.get('up_recall'))} | "
            f"{fmt(row.get('up_precision'))} | "
            f"{fmt(row.get('false_alarm_rate'))} | "
            f"{fmt(row.get('json_validity'))} |"
        )
    lines.extend(
        [
            "",
            f"best_external_strategy: {json.dumps(result.get('best_external_strategy'), ensure_ascii=False)}",
            f"recommended_current_strategy: {result.get('recommended_current_strategy')}",
            "",
            "## JSON",
            "```json",
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
            "```",
        ]
    )
    write_report_pair("external_model_comparison", result, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare P0e external validation strategies.")
    parser.parse_args()
    result = build_external_model_comparison()
    print(json.dumps({"status": result["status"], "blocked_reason": result["blocked_reason"], "best": result.get("best_external_strategy")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
