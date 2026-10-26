from __future__ import annotations

import argparse
import json

from src.reports.p2b_common import outbreak_distribution, read_processed_csv, split_label_distribution, write_p2b_report


def build_outbreak_dataset_audit() -> dict:
    dataset = read_processed_csv("outbreak_risk_dataset.csv")
    labels = read_processed_csv("outbreak_labels.csv")
    negatives = read_processed_csv("negative_samples.csv")
    missing_inputs = []
    if not dataset:
        missing_inputs.append("data/processed/outbreak_risk/outbreak_risk_dataset.csv")
    split_dist = split_label_distribution(dataset)
    test_dist = split_dist.get("test", {})
    single_class_issue = bool(test_dist) and len([label for label, count in test_dist.items() if count > 0]) < 2
    outbreak_dist = outbreak_distribution(dataset)
    total_positive = sum(1 for row in dataset if str(row.get("label")) == "1")
    max_positive = max((int(row.get("positive", 0) or 0) for row in outbreak_dist), default=0)
    payload = {
        "status": "ok",
        "blocked_reason": "none" if not missing_inputs else "missing_inputs",
        "missing_inputs": missing_inputs,
        "current_split_status": "feasibility_only",
        "split_label_distribution": split_dist,
        "outbreak_level_sample_distribution": outbreak_dist,
        "label_rows": len(labels),
        "negative_rows": len(negatives),
        "single_class_issue": single_class_issue,
        "internal_test_single_class_reason": (
            "The internal test split is assigned to one held-out outbreak with retained positive onset-window rows but no matched negative controls in that split; "
            "therefore PR-AUC/ROC-AUC are not available for internal test."
            if single_class_issue
            else "not_detected"
        ),
        "positive_sample_concentration": {
            "max_positive_rows_in_one_outbreak": max_positive,
            "total_positive_rows": total_positive,
            "max_share": (max_positive / total_positive) if total_positive else 0.0,
        },
        "outbreak_level_split_feasible": False,
        "outbreak_level_split_reason": "Only nine Ebola events and very few positive region-weeks are available; outbreak-level validation is useful for feasibility diagnostics but not formal claims.",
        "split_revision_recommended": True,
        "split_revision_should_train_now": False,
        "negative_control_assessment": "Current negatives are useful for prototype feasibility but are proxy/static controls and not sufficiently rich matched controls.",
    }
    lines = [
        "# Outbreak Dataset Audit",
        "",
        f"current_split_status: {payload['current_split_status']}",
        f"single_class_issue: {str(single_class_issue).lower()}",
        f"split_revision_recommended: {str(payload['split_revision_recommended']).lower()}",
        "split_revision_should_train_now: false",
        "",
        "## Split Label Distribution",
        "| split | label_0 | label_1 |",
        "|---|---:|---:|",
    ]
    for split, counts in split_dist.items():
        lines.append(f"| {split} | {counts.get('0', 0)} | {counts.get('1', 0)} |")
    lines.extend(["", "## JSON", "```json", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), "```"])
    return write_p2b_report("outbreak_dataset_audit", "Outbreak Dataset Audit", payload, lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit P2a outbreak risk dataset and splits.")
    parser.parse_args()
    payload = build_outbreak_dataset_audit()
    print(json.dumps({"status": payload["status"], "blocked_reason": payload.get("blocked_reason")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

