from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_live_source_search_metadata_cli_dry_run(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "live_source_search_orchestrator.cli",
            "search-sources",
            "--country",
            "United States",
            "--disease",
            "measles",
            "--providers",
            "auto",
            "--dry-run",
            "--metadata-check",
            "--metadata-check-top-n",
            "3",
            "--max-metadata-check-requests",
            "3",
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)

    assert payload["metadata_check_executed"] is True
    assert payload["metadata_check_request_count"] == 0
    assert payload["candidate_urls_visited_for_metadata_check"] is False
    assert Path(payload["metadata_check_results_path"]).exists()
