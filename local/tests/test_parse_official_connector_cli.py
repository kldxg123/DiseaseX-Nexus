from __future__ import annotations

import json
from pathlib import Path

from common.db import get_session
from common.models import RawDocument, SourceRegistry
from scripts import parse_official_connector


FIXTURES = Path("tests/fixtures")


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'parse-official-connector.db'}"


def test_parse_official_connector_cli_dry_run_does_not_write_database(tmp_path, capsys) -> None:
    database_path = tmp_path / "dry-run.db"

    result = parse_official_connector.main(
        [
            "--connector",
            "cdc_norostat",
            "--fixture-path",
            str(FIXTURES / "cdc_norostat.html"),
            "--database-url",
            f"sqlite:///{database_path}",
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert result == 0
    assert not database_path.exists()
    assert summary["dry_run"] is True
    assert summary["connector"] == "cdc_norostat"
    assert summary["document_count"] == 1
    assert "database" not in captured.out.lower()


def test_parse_official_connector_cli_fixture_writes_raw_documents(tmp_path) -> None:
    database_url = _database_url(tmp_path)

    result = parse_official_connector.main(
        [
            "--connector",
            "who_disease_outbreak_news",
            "--fixture-path",
            str(FIXTURES / "who_don.json"),
            "--database-url",
            database_url,
        ]
    )

    assert result == 0
    with get_session(database_url) as session:
        sources = session.query(SourceRegistry).all()
        raw_documents = session.query(RawDocument).all()

    assert len(sources) == 1
    assert len(raw_documents) == 1
    assert sources[0].parser_type == "who_disease_outbreak_news"
    assert raw_documents[0].metadata_json["connector"] == "who_disease_outbreak_news"


def test_parse_official_connector_cli_without_fixture_is_refused() -> None:
    try:
        parse_official_connector.main(["--connector", "cdc_nors"])
    except SystemExit as exc:
        assert str(exc) == "Refusing live official connector parsing without a local fixture."
    else:
        raise AssertionError("Expected SystemExit")
