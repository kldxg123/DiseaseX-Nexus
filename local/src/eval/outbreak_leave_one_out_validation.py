from __future__ import annotations

import argparse
import json

from src.data.outbreak_risk_dataset import _load_csv, _read_json, _write_report
from src.models.outbreak_risk_baseline_train import _skip_report


def run_leave_one_out() -> dict:
    train_report = _read_json("data/outputs/reports/outbreak_risk_baseline_train_report.json")
    if train_report.get("status") != "ok":
        return _skip_report(
            "outbreak_leave_one_out_validation_report",
            "Outbreak Leave One Out Validation Report",
            train_report.get("blocked_reason", "train_report_not_ok"),
        )
    rows = _load_csv("outbreak_risk_dataset.csv")
    train_events = sorted({row.get("outbreak_id") for row in rows if row.get("split") in {"train", "val", "test"} and row.get("outbreak_id")})
    payload = {
        "status": "ok",
        "blocked_reason": "none",
        "prototype_only": True,
        "not_for_model_claim": True,
        "comparison_limited": len(train_events) < 5,
        "held_out_event_count": len(train_events),
        "event_ids": train_events,
        "result": "diagnostic_only; full refit metrics deferred for larger event table",
    }
    return _write_report("outbreak_leave_one_out_validation_report", "Outbreak Leave One Out Validation Report", payload)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run P2a outbreak leave-one-out validation.")
    parser.parse_args()
    payload = run_leave_one_out()
    print(json.dumps({"status": payload["status"], "blocked_reason": payload.get("blocked_reason")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
