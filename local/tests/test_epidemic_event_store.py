from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from common.db import get_session, init_db
from common.models import EpidemicEvent, RawDocument, SourceRegistry
from cleaner_agent.event_extraction import extract_event_from_raw_document
from cleaner_agent.event_store import EpidemicEventStore


FIXTURES = Path("tests/fixtures")


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'epidemic-event-store.db'}"


def _add_raw_document(session, body_text: str) -> RawDocument:
    source = SourceRegistry(
        name="Cleaner fixture source",
        base_url="https://fixture.invalid/cleaner",
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
    document = RawDocument(
        source_registry_id=source.id,
        original_url="https://fixture.invalid/cleaner/doc",
        normalized_url="https://fixture.invalid/cleaner/doc",
        title="Cleaner fixture",
        body_text=body_text,
        content_hash=hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
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


def test_epidemic_event_store_saves_and_deduplicates(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    text = (FIXTURES / "cleaner_norovirus_en.txt").read_text(encoding="utf-8")

    with get_session(database_url) as session:
        raw_document = _add_raw_document(session, text)
        event = extract_event_from_raw_document(raw_document)
        store = EpidemicEventStore(session)
        saved = store.save_event(raw_document.id, event)
        duplicate = store.save_event(raw_document.id, event)
        event_count = session.query(EpidemicEvent).count()

    assert saved.id == duplicate.id
    assert event_count == 1
    assert saved.raw_document_id == raw_document.id
    assert saved.country == "United States"
    assert raw_document.extraction_status == "cleaned"


def test_epidemic_event_store_marks_no_event(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        raw_document = _add_raw_document(session, "irrelevant fixture text")
        EpidemicEventStore(session).mark_no_event(raw_document.id)

    with get_session(database_url) as session:
        stored = session.get(RawDocument, raw_document.id)

    assert stored.extraction_status == "no_event"
