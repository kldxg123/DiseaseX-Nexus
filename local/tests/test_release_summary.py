from __future__ import annotations

import json
import subprocess
import sys

from scripts.release_summary import REQUIRED_REPORTS, build_release_summary


def test_release_summary_function_reports_ok_without_sensitive_output() -> None:
    payload = build_release_summary()

    assert payload["status"] == "ok"
    assert sorted(payload["reports_present"]) == sorted(REQUIRED_REPORTS)
    assert payload["missing_reports"] == []
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    assert "ssh_key_path" not in rendered
    assert "password=" not in rendered
    assert "token=" not in rendered
    assert "secret=" not in rendered
    assert "private key" not in rendered


def test_release_summary_script_outputs_safe_json() -> None:
    completed = subprocess.run(
        [sys.executable, "scripts/release_summary.py"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert payload["missing_reports"] == []
