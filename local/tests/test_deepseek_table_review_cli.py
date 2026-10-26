from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_deepseek_table_review_mock_cli_outputs_safe_summary(tmp_path) -> None:
    command = [
        sys.executable,
        "scripts/run_deepseek_table_extraction_review.py",
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
        str(tmp_path / "cli-review"),
    ]

    completed = subprocess.run(command, cwd=Path.cwd(), capture_output=True, text=True, check=False)

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert payload["deepseek_called"] is False
    assert Path(payload["review_json"]).exists()
    rendered = completed.stdout.lower()
    assert "api_key" not in rendered
    assert "completion" not in rendered
    assert "raw_text" not in rendered
