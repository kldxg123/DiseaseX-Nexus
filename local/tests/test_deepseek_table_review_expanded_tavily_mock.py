from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_table_review_smoke_accepts_expanded_retrieval_arguments(tmp_path) -> None:
    command = [
        sys.executable,
        "scripts/run_deepseek_table_review_e2e_smoke.py",
        "--disease",
        "Ebola",
        "--country-or-region",
        "African Region",
        "--date-start",
        "2026-05-01",
        "--date-end",
        "2026-07-01",
        "--mock-deepseek",
        "--search-provider",
        "mock",
        "--max-search-queries",
        "6",
        "--max-search-results-per-query",
        "8",
        "--max-pages-to-read",
        "8",
        "--max-total-http-requests",
        "16",
        "--prefer-official-and-structured-sources",
        "--stop-when-structured-candidates-found",
        "--output-dir",
        str(tmp_path / "review"),
    ]

    completed = subprocess.run(command, cwd=Path.cwd(), capture_output=True, text=True, check=False)

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["network_accessed"] is False
    assert payload["retrieval_budget"]["search_provider"] == "mock"
    assert payload["retrieval_budget"]["max_pages_to_read"] == 8
