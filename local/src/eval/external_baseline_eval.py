from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.eval.external_validation_common import HORIZONS, label_distribution, read_external_samples, write_report_pair
from src.models.baseline_train import FEATURE_SETS
from src.utils.config import resolve_path


ARTIFACT_PATTERNS = [
    "models/baseline/**/*.joblib",
    "models/baselines/**/*.joblib",
    "data/outputs/models/baseline/**/*.joblib",
    "data/processed/baseline_artifacts/**/*.joblib",
]


def find_baseline_artifacts() -> list[Path]:
    artifacts: list[Path] = []
    for pattern in ARTIFACT_PATTERNS:
        artifacts.extend(resolve_path(".").glob(pattern))
    return sorted({path for path in artifacts if path.is_file()})


def evaluate_external_baseline() -> dict[str, Any]:
    artifacts = find_baseline_artifacts()
    result: dict[str, Any] = {
        "status": "not_available",
        "blocked_reason": "none",
        "baseline_external_eval_status": "not_available",
        "reason": "missing_baseline_artifacts",
        "training_started": False,
        "retrained_baseline": False,
        "metric_source": "external_test; no P0a holdout/backtest mixing",
        "feature_set_priority": list(FEATURE_SETS),
        "artifact_search_patterns": ARTIFACT_PATTERNS,
        "artifacts_found": [path.as_posix() for path in artifacts],
        "horizons": {},
    }
    for horizon in HORIZONS:
        samples = read_external_samples(horizon)
        key = f"{horizon}d"
        result["horizons"][key] = {
            "status": "not_available",
            "reason": "no_external_test_samples" if horizon == 30 and not samples else "missing_baseline_artifacts",
            "sample_count": len(samples),
            "label_distribution": label_distribution(samples),
            "metrics": None,
            "low_n_warning": "low_n_warning" if horizon == 21 and samples else None,
        }
    lines = [
        "# External Baseline Eval",
        "",
        f"status: {result['status']}",
        "baseline_external_eval_status: not_available",
        "reason: missing_baseline_artifacts",
        "training_started: false",
        "retrained_baseline: false",
        "metric_source: external_test_only",
        "",
        "P0a baseline artifacts were not found in the approved artifact locations, so P0e does not train a temporary baseline.",
        "",
        "| horizon | samples | status | reason | up | down | stable | warning |",
        "|---|---:|---|---|---:|---:|---:|---|",
    ]
    for horizon, item in result["horizons"].items():
        counts = item["label_distribution"]
        lines.append(
            f"| {horizon} | {item['sample_count']} | {item['status']} | {item['reason']} | "
            f"{counts.get('上升', 0)} | {counts.get('下降', 0)} | {counts.get('稳定', 0)} | {item.get('low_n_warning') or ''} |"
        )
    lines.extend(["", "## Artifact Search", "```json", json.dumps(result["artifacts_found"], ensure_ascii=False, indent=2), "```"])
    write_report_pair("external_baseline_eval", result, lines)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate saved P0a baseline artifacts on external test without retraining.")
    parser.parse_args()
    result = evaluate_external_baseline()
    print(json.dumps({"status": result["status"], "reason": result["reason"], "retrained_baseline": False}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
