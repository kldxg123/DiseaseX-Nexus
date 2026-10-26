from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from common.db import get_session, init_db
from common.models import EpidemicEvent, ModelFeature, RawDocument, SourceRegistry
from cleaner_agent.features import FeatureBuilder
from cleaner_agent.features_cli import main
from cleaner_agent.time_series import iso_week_start_date


FIXTURE = Path("tests/fixtures/feature_events_norovirus.json")


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'feature-builder.db'}"


def _seed_events(database_url: str) -> None:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
    with get_session(database_url) as session:
        source = SourceRegistry(
            name="Feature fixture source",
            base_url="https://fixture.invalid/features",
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
        for index, row in enumerate(rows, start=1):
            body_text = f"synthetic feature event {index}"
            raw_document = RawDocument(
                source_registry_id=source.id,
                original_url=f"https://fixture.invalid/features/{index}",
                normalized_url=f"https://fixture.invalid/features/{index}",
                title="Synthetic feature event",
                body_text=body_text,
                content_hash=hashlib.sha256(body_text.encode("utf-8")).hexdigest(),
                status_code=200,
                content_type="text/plain",
                published_at=datetime(2025, 1, 1, tzinfo=UTC),
                fetched_at=datetime(2025, 1, 1, tzinfo=UTC),
                metadata_json={},
                extraction_status="cleaned",
            )
            session.add(raw_document)
            session.flush()
            session.add(
                EpidemicEvent(
                    raw_document_id=raw_document.id,
                    country=row["country"],
                    admin1=row["admin1"],
                    disease=row["disease"],
                    event_date=iso_week_start_date(row["iso_week"]),
                    iso_week=row["iso_week"],
                    cases=row["cases"],
                    suspected_cases=row["suspected_cases"],
                    confirmed_cases=row["confirmed_cases"],
                    deaths=row["deaths"],
                    hospitalizations=row["hospitalizations"],
                    tests=row["tests"],
                    positivity_rate=None,
                    outbreaks_count=row["outbreaks_count"],
                    setting=None,
                    pathogen_genotype=None,
                    confidence_score=row["confidence_score"],
                    extraction_method="fixture",
                    provenance_json=row["provenance_json"],
                )
            )


def test_feature_builder_builds_model_features_from_epidemic_events(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_events(database_url)

    with get_session(database_url) as session:
        summary = FeatureBuilder(session).build(
            country="United States",
            disease="norovirus",
            horizons_days=(7, 28),
        )
        feature_count = session.query(ModelFeature).count()

    assert summary["events_seen"] == 10
    assert summary["weekly_rows"] == 12
    assert summary["feature_rows"] == 19
    assert summary["saved"] == 19
    assert summary["updated"] == 0
    assert summary["skipped"] == 0
    assert feature_count == 19


def test_feature_builder_cli_outputs_safe_json(tmp_path, capsys) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    _seed_events(database_url)

    result = main(
        [
            "--database-url",
            database_url,
            "--country",
            "United States",
            "--disease",
            "norovirus",
            "--horizons-days",
            "7,28",
        ]
    )
    captured = capsys.readouterr()
    summary = json.loads(captured.out)

    assert result == 0
    assert summary["events_seen"] == 10
    assert summary["feature_rows"] == 19
    assert summary["saved"] == 19
    assert "synthetic feature event" not in captured.out
    assert "secret" not in captured.out.lower()
    assert "token" not in captured.out.lower()
    assert "password" not in captured.out.lower()
    assert "private key" not in captured.out.lower()
