from __future__ import annotations

import json

from source_discovery_agent.bridge_cli import main


def test_bridge_cli_runs_fixture_bridge_and_outputs_safe_json(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{tmp_path / 'bridge-cli.db'}"

    exit_code = main(
        [
            "run-fixture-bridge",
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "influenza",
            "--parser-strategy",
            "html_table",
        ]
    )
    captured = capsys.readouterr().out
    payload = json.loads(captured)

    assert exit_code == 0
    assert payload["parser_strategy"] == "html_table"
    assert payload["raw_documents_saved"] == 2
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    assert "metadata_json" not in rendered
    assert "feature_json" not in rendered
    assert "<html" not in rendered
