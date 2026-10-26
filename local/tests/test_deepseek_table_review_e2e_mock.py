from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_deepseek_table_review_e2e_mock_cli_creates_review_and_pilot_events(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'e2e.sqlite').as_posix()}"
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
        "--output-dir",
        str(tmp_path / "e2e-review"),
        "--database-url",
        database_url,
    ]

    completed = subprocess.run(command, cwd=Path.cwd(), capture_output=True, text=True, check=False)

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["mock_e2e_completed"] is True
    assert payload["network_accessed"] is False
    assert payload["deepseek_call_count"] == 0
    assert payload["mock_rows_generated"] == 2
    assert payload["approved_rows_ingested"] == 2
    assert payload["epidemic_events_created"] == 2
    assert Path(payload["visualization_summary"]).exists()
    rendered = completed.stdout.lower()
    assert "prompt" not in rendered
    assert "completion" not in rendered
    assert "raw_text" not in rendered
