from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from common.db import get_session, init_db
from common.models import DiseaseCountryInformationRecord


def create_deepseek_audit_fixture(tmp_path: Path) -> tuple[Path, str, str]:
    run_id = "deepseek_replay_fixture"
    report_dir = tmp_path / "deepseek_api_diagnostics_fixture"
    report_dir.mkdir(parents=True)
    database_url = f"sqlite:///{(tmp_path / 'deepseek_web_search.sqlite').as_posix()}"
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        session.add_all(
            [
                DiseaseCountryInformationRecord(
                    run_id=run_id,
                    disease="influenza",
                    country="United States",
                    source_url="https://health.example.gov/influenza/weekly",
                    source_domain="health.example.gov",
                    source_title="Influenza weekly report",
                    information_type="surveillance_metric",
                    evidence_type="safe_deepseek_json",
                    source_type="official_report_evidence",
                    event_date=date(2026, 3, 2),
                    iso_week="2026-W10",
                    region_name="United States",
                    metric_name="cases",
                    metric_value=12.0,
                    metric_unit="count",
                    text_summary="Safe summary says 12 cases during 2026-W10.",
                    evidence_summary="Found cases=12 with time reference 2026-W10 in safe summary.",
                    confidence=0.8,
                    feature_ready_potential=True,
                    requires_human_review=True,
                ),
                DiseaseCountryInformationRecord(
                    run_id=run_id,
                    disease="influenza",
                    country="United States",
                    source_url="https://health.example.gov/influenza/report",
                    source_domain="health.example.gov",
                    source_title="Influenza event report",
                    information_type="official_report_evidence",
                    evidence_type="safe_deepseek_json",
                    source_type="official_report_evidence",
                    event_date=date(2026, 3, 9),
                    iso_week="2026-W11",
                    region_name="United States",
                    metric_name=None,
                    metric_value=None,
                    metric_unit=None,
                    text_summary="Safe summary confirms an influenza report during 2026-W11.",
                    evidence_summary="Safe evidence contains source and week but no numeric count.",
                    confidence=0.65,
                    feature_ready_potential=False,
                    requires_human_review=True,
                ),
                DiseaseCountryInformationRecord(
                    run_id=run_id,
                    disease="influenza",
                    country="United States",
                    source_url="https://news.example.org/influenza",
                    source_domain="news.example.org",
                    source_title="Influenza background evidence",
                    information_type="disease_country_summary",
                    evidence_type="safe_deepseek_json",
                    source_type="news",
                    event_date=None,
                    iso_week=None,
                    region_name="United States",
                    metric_name=None,
                    metric_value=None,
                    metric_unit=None,
                    text_summary="Safe summary mentions influenza surveillance without a date or numeric value.",
                    evidence_summary="Safe evidence-only summary.",
                    confidence=0.4,
                    feature_ready_potential=False,
                    requires_human_review=True,
                ),
            ]
        )
    (report_dir / "deepseek_structured_extraction_replay.json").write_text(
        json.dumps(
            {
                "status": "ok",
                "run_id": run_id,
                "records_extracted_by_deepseek": 3,
                "records_persisted": 3,
                "raw_documents_created": 0,
                "epidemic_events_created": 0,
                "feature_ready_records": 0,
                "visualization_points": 2,
                "no_tavily_rerun": True,
                "page_read_request_count": 0,
            }
        ),
        encoding="utf-8",
    )
    (report_dir / "database_persistence_summary.json").write_text(
        json.dumps({"run_id": run_id, "records_persisted": 3, "raw_documents_created": 0, "epidemic_events_created": 0}),
        encoding="utf-8",
    )
    (report_dir / "cleaning_bridge_summary.json").write_text(
        json.dumps(
            {
                "raw_documents_created": 0,
                "duplicates": 0,
                "skipped": 3,
                "epidemic_events_created": 0,
                "event_duplicates": 0,
                "feature_ready_records": 0,
                "evidence_only_records": 3,
            }
        ),
        encoding="utf-8",
    )
    (report_dir / "visualization_3d_data.json").write_text(
        json.dumps(
            {
                "disease": "influenza",
                "country": "United States",
                "points": [
                    {
                        "x": 0.8,
                        "y": 0.7,
                        "z": 12.0,
                        "size": 10,
                        "color": "blue",
                        "source_domain": "health.example.gov",
                        "information_type": "surveillance_metric",
                        "cleaning_status": "not_sent",
                    },
                    {
                        "x": 0.4,
                        "y": 0.3,
                        "z": 0.0,
                        "size": 5,
                        "color": "gray",
                        "source_domain": "news.example.org",
                        "information_type": "disease_country_summary",
                        "cleaning_status": "not_sent",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return report_dir, database_url, run_id
