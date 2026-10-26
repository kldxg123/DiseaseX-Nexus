from __future__ import annotations

import json

from common.db import get_session
from common.models import SourceRegistry
from crawler_optimizer_agent.cli import main
from tests.crawler_optimizer_test_utils import populated_optimizer_db


def test_cli_analyze_outputs_safe_json(tmp_path, capsys) -> None:
    database_url = populated_optimizer_db(tmp_path)

    exit_code = main(
        [
            "analyze",
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "norovirus",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert "recommended_actions" in payload
    assert "body_text" not in captured.out


def test_cli_connector_spec_outputs_json_and_markdown(tmp_path, capsys) -> None:
    database_url = populated_optimizer_db(tmp_path)
    with get_session(database_url) as session:
        source = session.query(SourceRegistry).filter(SourceRegistry.status == "pending_review").first()

    json_exit = main(["connector-spec", "--database-url", database_url, "--source-id", str(source.id)])
    json_output = capsys.readouterr().out
    payload = json.loads(json_output)

    md_exit = main(
        [
            "connector-spec",
            "--database-url",
            database_url,
            "--source-id",
            str(source.id),
            "--markdown",
        ]
    )
    markdown_output = capsys.readouterr().out

    assert json_exit == 0
    assert md_exit == 0
    assert payload["requires_human_review"] is True
    assert "Connector Spec" in markdown_output
    assert "password" not in markdown_output.lower()
