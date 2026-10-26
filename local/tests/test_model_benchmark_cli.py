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


def test_model_benchmark_cli_build_backtest_rank(tmp_path):
    build = run_cli(
        "build-matrix",
        "--m2-readiness-dir",
        "reports/milestone2_readiness",
        "--m1-freeze-dir",
        "reports/milestone1_freeze",
        "--output-dir",
        str(tmp_path),
    )
    assert build["status"] == "ok"
    matrix = build["benchmark_matrix"]
    backtest = run_cli(
        "run-backtest",
        "--matrix",
        matrix,
        "--output-dir",
        str(tmp_path),
        "--synthetic-smoke-if-needed",
        "--max-tasks",
        "4",
    )
    assert backtest["result_count"] == 4
    rank = run_cli(
        "rank-results",
        "--results",
        backtest["backtest_results"],
        "--output-dir",
        str(tmp_path),
    )
    assert rank["status"] == "ok"
    assert Path(rank["ranking_report"]).exists()
