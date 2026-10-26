from __future__ import annotations

from pathlib import Path

from cleaner_agent.agent import CleanerAgent
from cleaner_agent.features import FeatureBuilder
from common.db import get_session, init_db
from common.models import EpidemicEvent, ModelFeature, RawDocument
from crawler_agent.connector_ingest import store_connector_documents, upsert_connector_source
from crawler_agent.generic_parsers import parse_by_strategy, parser_result_to_connector_documents
from crawler_agent.raw_document_store import RawDocumentStore

FIXTURES = Path("tests/fixtures")


def test_generic_parsers_ingest_clean_and_build_features(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'generic-influenza.db'}"
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        source = upsert_connector_source(
            session,
            "cdc_fluview",
            base_url="https://local.invalid/generic-influenza",
            status="pending_review",
        )
        all_documents = []
        for strategy, fixture in (
            ("csv_dataset", "cdc_fluview_ili.csv"),
            ("html_table", "cdc_fluview_table.html"),
            ("official_report_page", "cdc_fluview_report.html"),
            ("json_api", "influenza_json_api_sample.json"),
        ):
            result = parse_by_strategy(
                strategy,
                (FIXTURES / fixture).read_text(encoding="utf-8"),
                source_name="CDC FluView Generic Fixture",
                url=f"https://local.invalid/generic-influenza/{strategy}",
                country="United States",
                disease="influenza",
            )
            all_documents.extend(parser_result_to_connector_documents(result))

        store_results = store_connector_documents(
            source=source,
            documents=all_documents,
            store=RawDocumentStore(session),
        )
        assert len(store_results) == 19
        assert session.query(RawDocument).count() == 19

        clean_summary = CleanerAgent(session).process_pending(limit=100, country="United States", disease="influenza")
        assert clean_summary["events_created"] >= 18
        assert session.query(EpidemicEvent).filter(EpidemicEvent.disease == "influenza").count() >= 18

        feature_summary = FeatureBuilder(session).build(
            country="United States",
            disease="influenza",
            horizons_days=(7,),
            outbreak_threshold=1,
        )
        assert feature_summary["feature_rows"] > 0
        assert session.query(ModelFeature).filter(ModelFeature.disease == "influenza").count() > 0
