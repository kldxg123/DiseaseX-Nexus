from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_live_source_search_cli_dry_run(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "live_source_search_orchestrator.cli",
            "search-sources",
            "--country",
            "United States",
            "--disease",
            "cholera",
            "--providers",
            "auto",
            "--max-total-requests",
            "3",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["request_count_total"] == 0
    assert payload["candidate_urls_visited"] is False
    assert Path(payload["summary_path"]).exists()
