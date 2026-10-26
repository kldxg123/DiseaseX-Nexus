from __future__ import annotations

import json
from pathlib import Path

from cleaner_agent.agent import CleanerAgent
from cleaner_agent.features import FeatureBuilder
from common.db import get_session, init_db
from common.models import EpidemicEvent, ModelFeature, RawDocument, SourceRegistry
from crawler_agent.connector_ingest import ingest_connector_payload
from crawler_optimizer_agent import CrawlerOptimizerAgent, summarize_optimizer_report
from data_quality_agent.report import build_data_quality_report
from source_discovery_agent import SourceDiscoveryAgent
from worker_agent.tasks import TaskRunner

FIXTURES = Path("tests/fixtures")
COUNTRY = "United States"
DISEASE = "influenza"


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'influenza-extensibility.db'}"


def test_influenza_source_discovery_and_multiformat_ingest_pipeline(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    search_payload = json.loads((FIXTURES / "source_discovery_influenza_search_results.json").read_text(encoding="utf-8"))

    with get_session(database_url) as session:
        report = SourceDiscoveryAgent(session).discover_from_search_results(
            country=COUNTRY,
            disease=DISEASE,
            search_results=search_payload,
            min_source_score=0.35,
            generate_connector_specs=True,
        )
        discovered = session.query(SourceRegistry).all()
        assert report.status == "ok"
        assert report.search_results_seen == 8
        assert report.candidates_found == 8
        assert report.candidates_inserted_or_updated >= 6
        assert report.connector_specs_generated >= 3
        assert discovered
        assert all(source.status == "pending_review" for source in discovered)
        assert any("csv" in source.parser_type or "table" in source.parser_type for source in discovered)

        csv_results = ingest_connector_payload(
            session,
            "cdc_fluview",
            (FIXTURES / "cdc_fluview_ili.csv").read_text(encoding="utf-8"),
            base_url="https://www.cdc.gov/flu/weekly/fluviewinteractive.htm",
        )
        table_results = ingest_connector_payload(
            session,
            "cdc_fluview",
            (FIXTURES / "cdc_fluview_table.html").read_text(encoding="utf-8"),
            base_url="https://www.cdc.gov/flu/weekly/fluview-table-fixture.html",
        )
        report_results = ingest_connector_payload(
            session,
            "cdc_fluview",
            (FIXTURES / "cdc_fluview_report.html").read_text(encoding="utf-8"),
            base_url="https://www.cdc.gov/flu/weekly/index.htm",
        )
        assert len(csv_results) == 4
        assert len(table_results) == 2
        assert len(report_results) == 1
        assert session.query(RawDocument).count() == 7
        assert all(result.document.disease_detected == "influenza" for result in csv_results + table_results)
        assert all(result.document.metadata_json.get("source_key") == "cdc_fluview" for result in csv_results + table_results)

        clean_summary = CleanerAgent(session).process_pending(limit=50, country=COUNTRY, disease=DISEASE)
        assert clean_summary["events_created"] >= 7
        events = session.query(EpidemicEvent).filter(EpidemicEvent.disease == DISEASE).all()
        assert events
        assert any(event.cases and event.cases > 0 for event in events)
        assert any(event.tests and event.tests > 0 for event in events)

        feature_summary = FeatureBuilder(session).build(
            country=COUNTRY,
            disease=DISEASE,
            horizons_days=(7,),
            outbreak_threshold=1,
        )
        assert feature_summary["events_seen"] >= 7
        assert feature_summary["feature_rows"] > 0
        assert session.query(ModelFeature).filter(ModelFeature.disease == DISEASE).count() > 0

        quality_report = build_data_quality_report(session, country=COUNTRY, disease=DISEASE)
        assert quality_report["summary"]["raw_document_count"] >= 7
        assert quality_report["summary"]["event_count"] >= 7
        assert quality_report["summary"]["feature_count"] > 0
        rendered_quality = json.dumps(quality_report, ensure_ascii=False).lower()
        assert "raw_text" not in rendered_quality
        assert "metadata_json" not in rendered_quality
        assert "feature_json" not in rendered_quality

        optimizer_report = CrawlerOptimizerAgent(session).analyze(
            country=COUNTRY,
            disease=DISEASE,
            include_connector_specs=True,
            apply_safe_actions=False,
        )
        optimizer_summary = summarize_optimizer_report(optimizer_report, max_failure_cases=2, max_actions=2, max_connector_specs=2)
        assert optimizer_summary["status"] == "ok"
        assert optimizer_summary["sources_analyzed"] >= 1
        assert optimizer_summary["connector_spec_count"] >= 1


def test_worker_source_discovery_fixture_supports_influenza(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        result = TaskRunner(session).run_task(
            "discover_sources_fixture",
            country=COUNTRY,
            disease=DISEASE,
            fixture=str(FIXTURES / "source_discovery_influenza_search_results.json"),
            output_dir=str(tmp_path / "reports"),
        )
        rows = session.query(SourceRegistry).all()

    assert result["status"] == "success"
    summary = result["job"]["summary"]
    assert summary["candidates_found"] == 8
    assert summary["candidates_inserted_or_updated"] == len(rows)
    assert summary["network_accessed"] is False
    assert all(row.status == "pending_review" for row in rows)
    rendered = json.dumps(summary, ensure_ascii=False).lower()
    assert "metadata_json" not in rendered
    assert "raw_text" not in rendered
