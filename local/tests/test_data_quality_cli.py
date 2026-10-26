from __future__ import annotations

import json

from common.db import get_session, init_db
from data_quality_agent.cli import main
from tests.test_data_quality_profile import _seed_quality_rows


def test_data_quality_cli_profile_label_audit_and_report(tmp_path, capsys) -> None:
    database_url = f"sqlite:///{tmp_path / 'cli.db'}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        _seed_quality_rows(session)

    assert main(["profile", "--database-url", database_url]) == 0
    profile_payload = json.loads(capsys.readouterr().out)
    assert profile_payload["status"] == "ok"
    assert profile_payload["profile"]["source_registry"]["row_count"] == 1

    assert (
        main(
            [
                "label-audit",
                "--database-url",
                database_url,
                "--country",
                "United States",
                "--disease",
                "norovirus",
            ]
        )
        == 0
    )
    audit_payload = json.loads(capsys.readouterr().out)
    assert audit_payload["status"] == "ok"
    assert audit_payload["label_audit"]["checked_features"] == 1

    output_dir = tmp_path / "reports"
    assert main(["report", "--database-url", database_url, "--output-dir", str(output_dir)]) == 0
    report_payload = json.loads(capsys.readouterr().out)
    assert report_payload["status"] == "ok"
    assert report_payload["report_json"].endswith(".json")
    rendered = json.dumps(report_payload, ensure_ascii=False).lower()
    assert "password" not in rendered
    assert "token" not in rendered
    assert "feature_json" not in rendered
