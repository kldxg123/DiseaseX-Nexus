from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def run_cli(*args: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "model_benchmark_agent.cli", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_m2_m2_cli_ablation_selection_recommendation(tmp_path):
    ablation = run_cli(
        "run-feature-ablation",
        "--m2-m1-dir",
        "reports/milestone2_m1",
        "--output-dir",
        str(tmp_path),
        "--synthetic-smoke-if-needed",
    )
    assert ablation["status"] == "ok"
    assert Path(ablation["feature_ablation_results"]).exists()
    selection = run_cli(
        "rank-feature-selection",
        "--ablation-results",
        ablation["feature_ablation_results"],
        "--output-dir",
        str(tmp_path),
    )
    assert "epidemiological_lag" in selection["top_recommended_feature_groups"]
    recommendation = run_cli(
        "recommend-minimum-features",
        "--selection-ranking",
        selection["feature_selection_ranking"],
        "--output-dir",
        str(tmp_path),
    )
    assert recommendation["status"] == "ok"
    assert Path(recommendation["minimum_feature_recommendation"]).exists()
