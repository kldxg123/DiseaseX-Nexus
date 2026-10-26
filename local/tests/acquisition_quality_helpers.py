from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta

from common.db import get_session, init_db
from common.models import EpidemicEvent, RawDocument, SourceRegistry


def make_acquisition_db(tmp_path, *, weeks: int = 1, numeric: bool = True, missing_iso_week: bool = False) -> str:
    database_url = f"sqlite:///{tmp_path / 'acquisition-quality.db'}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        source = SourceRegistry(
            name="Acquisition Test Source",
            base_url="https://example.invalid/measles",
            country_scope=["United States"],
            disease_scope=["measles"],
            source_type="official_report_page",
            status="pending_review",
            metadata_json={},
        )
        session.add(source)
        session.flush()
        start = date(2026, 1, 5)
        for index in range(max(1, weeks)):
            event_date = start + timedelta(days=7 * index)
            body = f"Measles acquisition fixture {index}"
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
            raw = RawDocument(
                source_registry_id=source.id,
                original_url=f"https://example.invalid/measles?row={index}",
                normalized_url=f"https://example.invalid/measles?row={index}",
                title="Measles acquisition fixture",
                body_text=body,
                content_hash=digest,
                status_code=200,
                content_type="text/html",
                fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
                country_detected="United States",
                disease_detected="measles",
                extraction_status="processed",
                metadata_json={},
            )
            session.add(raw)
            session.flush()
            iso_year, iso_week, _ = event_date.isocalendar()
            session.add(
                EpidemicEvent(
                    raw_document_id=raw.id,
                    country="United States",
                    admin1=None,
                    admin2=None,
                    disease="measles",
                    event_date=event_date,
                    iso_week=None if missing_iso_week else f"{iso_year}-W{iso_week:02d}",
                    cases=index + 1 if numeric else None,
                    confirmed_cases=index + 1 if numeric else None,
                    suspected_cases=None,
                    deaths=0,
                    hospitalizations=None,
                    tests=None,
                    positivity_rate=None,
                    outbreaks_count=1 if numeric and index % 2 == 0 else None,
                    confidence_score=0.8,
                    extraction_method="fixture",
                    provenance_json={},
                )
            )
        session.flush()
    return database_url


def make_run_dir(tmp_path):
    run_dir = tmp_path / "acquisition_measles"
    run_dir.mkdir()
    (run_dir / "candidate_acquisition_summary.json").write_text(
        '{"raw_documents_saved":1,"epidemic_events_created":1,"model_features_created":0}',
        encoding="utf-8",
    )
    return run_dir
