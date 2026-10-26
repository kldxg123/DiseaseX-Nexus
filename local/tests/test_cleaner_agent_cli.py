from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from common.db import get_session, init_db
from common.models import EpidemicEvent, RawDocument, SourceRegistry
from cleaner_agent.agent import CleanerAgent, main


FIXTURES = Path("tests/fixtures")


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'cleaner-agent.db'}"


def _add_source(session) -> SourceRegistry:
    source = SourceRegistry(
        name="Cleaner agent fixture source",
        base_url="https://fixture.invalid/cleaner-agent",
        country_scope=["global"],
        disease_scope=["all"],
        source_type="fixture",
        reliability_score=0.9,
        parser_type="fixture",
        status="active",
        metadata_json={},
    )
    session.add(source)
    session.flush()
    return source


def _add_raw_document(session, source: SourceRegistry, body_text: str, suffix: str) -> RawDocument:
    document = RawDocument(
        source_registry_id=source.id,
        original_url=f"https://fixture.invalid/cleaner-agent/{suffix}",
        normalized_url=f"https://fixture.invalid/cleaner-agent/{suffix}",
        title="Cleaner agent fixture",
        body_text=body_text,
        content_hash=hashlib.sha256(f"{suffix}:{body_text}".encode("utf-8")).hexdigest(),
        status_code=200,
        content_type="text/plain",
        published_at=datetime(2025, 1, 31, tzinfo=UTC),
        fetched_at=datetime(2025, 1, 31, tzinfo=UTC),
        metadata_json={},
        extraction_status="pending",
    )
    session.add(document)
    session.flush()
    return document


def test_cleaner_agent_process_pending_creates_events_and_marks_no_event(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        source = _add_source(session)
        _add_raw_document(
            session,
            source,
            (FIXTURES / "cleaner_norovirus_en.txt").read_text(encoding="utf-8"),
            "event",
        )
        _add_raw_document(
            session,
            source,
            (FIXTURES / "cleaner_irrelevant.txt").read_text(encoding="utf-8"),
            "none",
        )
        summary = CleanerAgent(session).process_pending(limit=10)
        event_count = session.query(EpidemicEvent).count()

    assert summary == {
        "processed": 2,
        "events_created": 1,
        "no_event": 1,
        "duplicates": 0,
        "errors": 0,
    }
    assert event_count == 1


def test_cleaner_agent_cli_outputs_safe_json(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        source = _add_source(session)
        _add_raw_document(
            session,
            source,
            (FIXTURES / "cleaner_norovirus_zh.txt").read_text(encoding="utf-8"),
            "zh-event",
        )

    result = main(["--database-url", database_url, "--limit", "10"])
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert result == 0
    assert summary["processed"] == 1
    assert summary["events_created"] == 1
    assert summary["errors"] == 0
    assert "诺如病毒" not in captured.out
