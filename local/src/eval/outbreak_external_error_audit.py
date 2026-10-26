from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.reports.p2b_common import read_report, write_p2b_report
from src.utils.config import resolve_path


def build_outbreak_external_error_audit() -> dict:
    eval_report = read_report("outbreak_risk_baseline_eval_report.json")
    external = (eval_report.get("results") or {}).get("external_test", {})
    per_sample_path = resolve_path("data/processed/outbreak_risk/outbreak_risk_external_test_predictions.csv")
    per_sample_available = per_sample_path.exists()
    cm = external.get("confusion_matrix", {}) if isinstance(external, dict) else {}
    payload: dict[str, Any] = {
        "status": "ok",
        "blocked_reason": "none",
        "per_sample_artifact_available": per_sample_available,
        "summary_level_audit": not per_sample_available,
        "external_false_negatives": int(cm.get("fn", 0) or 0),
        "external_false_positives": int(cm.get("fp", 0) or 0),
        "external_true_positives": int(cm.get("tp", 0) or 0),
        "external_true_negatives": int(cm.get("tn", 0) or 0),
        "external_pr_auc": external.get("pr_auc"),
        "external_recall_at_k": external.get("recall_at_k"),
        "external_false_alarms_per_100_region_weeks": external.get("false_alarms_per_100_region_weeks"),
        "external_lead_time_weeks": external.get("lead_time_weeks"),
        "low_n_warning": external.get("low_n_warning", True),
        "main_failure_reasons": [
            "External test has very low n, so ranked metrics are unstable.",
            "Recall@K is 0 because the top-ranked external rows did not include the positive onset rows.",
            "False alarms are high because 7 of 16 external rows were predicted positive at the fixed prototype threshold.",
            "Feature table lacks strong pre-event OSINT/official surveillance signals and region-level early case signals.",
            "Matched negative controls are still prototype proxies, so external calibration is weak.",
        ],
        "threshold_tuned": False,
        "predictions_regenerated": False,
        "model_retrained": False,
    }
    _write_error_breakdown_figure(payload)
    lines = [
        "# Outbreak External Error Audit",
        "",
        f"per_sample_artifact_available: {str(per_sample_available).lower()}",
        f"summary_level_audit: {str((not per_sample_available)).lower()}",
        f"external_false_negatives: {payload['external_false_negatives']}",
        f"external_false_positives: {payload['external_false_positives']}",
        f"external_recall_at_k: {payload['external_recall_at_k']}",
        f"external_false_alarms_per_100_region_weeks: {payload['external_false_alarms_per_100_region_weeks']}",
        "threshold_tuned: false",
        "model_retrained: false",
        "",
        "## Main Failure Reasons",
    ]
    lines.extend(f"- {item}" for item in payload["main_failure_reasons"])
    lines.extend(["", "## JSON", "```json", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), "```"])
    return write_p2b_report("outbreak_external_error_audit", "Outbreak External Error Audit", payload, lines)


def _write_error_breakdown_figure(payload: dict[str, Any]) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig_dir = resolve_path("data/outputs/figures")
        fig_dir.mkdir(parents=True, exist_ok=True)
        names = ["TP", "FP", "TN", "FN"]
        values = [
            payload["external_true_positives"],
            payload["external_false_positives"],
            payload["external_true_negatives"],
            payload["external_false_negatives"],
        ]
        fig, ax = plt.subplots(figsize=(5, 3))
        ax.bar(names, values, color=["#2f8f5b", "#c95f45", "#6a8fbf", "#d19c3a"])
        ax.set_title("External test error breakdown")
        ax.set_ylabel("Region-weeks")
        fig.tight_layout()
        fig.savefig(fig_dir / "outbreak_external_error_breakdown.png", dpi=150)
        plt.close(fig)
    except Exception:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit fixed P2a external-test errors.")
    parser.parse_args()
    payload = build_outbreak_external_error_audit()
    print(json.dumps({"status": payload["status"], "blocked_reason": payload.get("blocked_reason")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

