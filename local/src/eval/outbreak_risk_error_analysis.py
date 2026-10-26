from __future__ import annotations

import argparse
import json

from src.data.outbreak_risk_dataset import _read_json, _write_report
from src.models.outbreak_risk_baseline_train import _skip_report


def run_error_analysis() -> dict:
    eval_report = _read_json("data/outputs/reports/outbreak_risk_baseline_eval_report.json")
    if eval_report.get("status") != "ok":
        return _skip_report(
            "outbreak_risk_error_analysis",
            "Outbreak Risk Error Analysis",
            eval_report.get("blocked_reason", "eval_report_not_ok"),
        )
    test = eval_report.get("results", {}).get("test", {})
    cm = test.get("confusion_matrix", {}) if isinstance(test, dict) else {}
    payload = {
        "status": "ok",
        "blocked_reason": "none",
        "prototype_only": True,
        "not_for_model_claim": True,
        "false_negative_count": cm.get("fn", 0),
        "false_positive_count": cm.get("fp", 0),
        "lead_time": test.get("lead_time_weeks", "not_available"),
        "data_gap": [
            "structured pre-event local case signals remain sparse",
            "OSINT aggregate counts are metadata/prototype only",
            "population and mobility features are coarse proxies",
        ],
        "external_generalization": eval_report.get("results", {}).get("external_test", {}),
    }
    return _write_report("outbreak_risk_error_analysis", "Outbreak Risk Error Analysis", payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run P2a outbreak risk error analysis.")
    parser.parse_args()
    payload = run_error_analysis()
    print(json.dumps({"status": payload["status"], "blocked_reason": payload.get("blocked_reason")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
