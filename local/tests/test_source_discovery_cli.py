from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from source_discovery_agent.cli import main


FIXTURE = "tests/fixtures/source_discovery_norovirus_search_results.json"


def test_source_discovery_cli_discover_fixture(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{tmp_path / 'source-discovery-cli.db'}"
    output_dir = tmp_path / "reports"

    code = main(
        [
            "discover-fixture",
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "norovirus",
            "--fixture",
            FIXTURE,
            "--output-dir",
            str(output_dir),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["status"] == "ok"
    assert payload["search_results_seen"] == 8
    assert payload["candidates_found"] == 8
    assert payload["candidates_inserted_or_updated"] >= 1
    assert payload["connector_specs_generated"] >= 1
    assert Path(payload["report_json"]).exists()
    assert Path(payload["report_markdown"]).exists()
    assert payload["network_accessed"] is False


def test_source_discovery_cli_rejects_allow_network(capsys) -> None:
    code = main(
        [
            "discover-fixture",
            "--database-url",
            "sqlite:///:memory:",
            "--country",
            "United States",
            "--disease",
            "norovirus",
            "--fixture",
            FIXTURE,
            "--allow-network",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["status"] == "failed"
    assert "live search not implemented" in payload["error"]
    assert payload["network_accessed"] is False


def test_source_discovery_cli_subprocess_without_pythonpath(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'source-discovery-subprocess.db'}"
    output_dir = tmp_path / "reports"
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "source_discovery_agent.cli",
            "discover-fixture",
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "norovirus",
            "--fixture",
            FIXTURE,
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "ok"
    assert payload["network_accessed"] is False
