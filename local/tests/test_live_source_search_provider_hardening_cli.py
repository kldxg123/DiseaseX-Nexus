from __future__ import annotations

import json
import subprocess
import sys


def test_cli_accepts_hardening_flags_in_dry_run(tmp_path):
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
            "--preferred-live-disease",
            "measles",
            "--providers",
            "auto",
            "--max-total-requests",
            "3",
            "--dry-run",
            "--curated-official-directory-url",
            "https://www.cdc.gov/measles/",
            "--output-dir",
            str(tmp_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(result.stdout)

    assert payload["disease"] == "measles"
    assert payload["request_count_total"] == 0
    assert payload["positive_yield_validation"]["request_budget_respected"] is True
    assert payload["provider_failure_diagnostics_path"].endswith("provider_failure_diagnostics.json")
