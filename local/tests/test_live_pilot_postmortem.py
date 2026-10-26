from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from common.db import get_session, init_db
from common.models import EpidemicEvent, RawDocument, SourceRegistry
from scripts.analyze_live_pilot import analyze_live_pilot


def _seed_pilot(tmp_path):
    pilot_dir = tmp_path / "pilot"
    pilot_dir.mkdir()
    database_url = f"sqlite:///{pilot_dir / 'pilot.sqlite'}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        source = SourceRegistry(
            name="CDC NoroSTAT",
            base_url="https://fixture.invalid/cdc-norostat",
            country_scope=["United States"],
            disease_scope=["norovirus"],
            source_type="official_dataset",
            parser_type="cdc_norostat",
            status="pending_review",
            reliability_score=0.9,
            metadata_json={"connector": "cdc_norostat"},
        )
        session.add(source)
        session.flush()
        for index, week in enumerate(("1-Aug", "8-Aug", "15-Aug", "22-Aug"), start=1):
            body = f"Illness onset week: {week}."
            document = RawDocument(
                source_registry_id=source.id,
                original_url=f"https://fixture.invalid/cdc-norostat?row={index}",
                normalized_url=f"https://fixture.invalid/cdc-norostat?row={index}",
                title=f"CDC NoroSTAT row {index}",
                body_text=body,
                content_hash=hashlib.sha256(f"{index}:{body}".encode("utf-8")).hexdigest(),
                status_code=200,
                content_type="text/html",
                fetched_at=datetime(2026, 7, 2, tzinfo=UTC),
                country_detected="United States",
                disease_detected="norovirus",
                extraction_status="cleaned",
                metadata_json={
                    "connector": "cdc_norostat",
                    "source_key": "cdc_norostat",
                    "row_number": index,
                    "row_fields": {"week": week},
                },
            )
            session.add(document)
            session.flush()
            session.add(
                EpidemicEvent(
                    raw_document_id=document.id,
                    country="United States",
                    disease="norovirus",
                    event_date=datetime(2026, 7, 2, tzinfo=UTC).date(),
                    iso_week="2026-W27",
                    extraction_method="rule_based_v1",
                    confidence_score=0.5,
                    provenance_json={"safe": True},
                )
            )
    (pilot_dir / "live_fetch_summary.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "connector_documents_parsed": 4,
                "raw_documents_saved": 4,
                "duplicates": 0,
                "connector": {"status_code": 200},
            }
        ),
        encoding="utf-8",
    )
    (pilot_dir / "data_quality_report.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    (pilot_dir / "crawler_optimizer_report.json").write_text(
        json.dumps({"status": "ok", "summary": {"failures": {"low_relevance": 8}}, "recommended_actions": [1, 2]}),
        encoding="utf-8",
    )
    return pilot_dir


def test_analyze_live_pilot_writes_safe_postmortem_reports(tmp_path) -> None:
    pilot_dir = _seed_pilot(tmp_path)

    report = analyze_live_pilot(pilot_dir=pilot_dir)

    assert report["status"] == "ok"
    assert report["why_feature_rows_were_zero"]["feature_rows_zero"] is True
    assert report["why_feature_rows_were_zero"]["repair_can_generate_horizon_7"] is True
    assert report["repair_simulation"]["unique_iso_week_count"] == 4
    assert (pilot_dir / "postmortem_report.json").exists()
    assert (pilot_dir / "postmortem_report.md").exists()
    rendered = json.dumps(report, ensure_ascii=False).lower()
    assert "feature_json" not in rendered
    assert "lesson_json" not in rendered
    assert "metadata_json" not in rendered
    assert "raw_text" not in rendered
    assert "password" not in rendered
