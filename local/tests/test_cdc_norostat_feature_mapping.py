from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from cleaner_agent.agent import CleanerAgent
from cleaner_agent.event_extraction import extract_event_from_raw_document
from cleaner_agent.features import FeatureBuilder
from common.db import get_session, init_db
from common.models import EpidemicEvent, ModelFeature, RawDocument
from crawler_agent.connector_ingest import store_connector_documents, upsert_connector_source
from crawler_agent.connectors import CdcNoroStatConnector
from crawler_agent.raw_document_store import RawDocumentStore


LIVE_URL = "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html"


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'cdc-norostat-feature.db'}"


def _six_week_table() -> str:
    rows = []
    for index in range(1, 7):
        rows.append(
            f"""
            <tr>
              <td>National</td>
              <td>2025-W0{index}</td>
              <td>{index}</td>
              <td>1</td>
              <td>{index + 1}</td>
            </tr>
            """
        )
    return (
        "<table><tr><th>State</th><th>Illness Onset Week</th>"
        "<th>Suspected Outbreaks</th><th>Confirmed Outbreaks</th><th>Total Outbreaks</th></tr>"
        + "\n".join(rows)
        + "</table>"
    )


def test_cdc_norostat_connector_cleaner_and_features_map_structured_rows(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    documents = CdcNoroStatConnector().parse(_six_week_table(), base_url=LIVE_URL)

    with get_session(database_url) as session:
        source = upsert_connector_source(session, "cdc_norostat", base_url=LIVE_URL)
        store_connector_documents(source=source, documents=documents, store=RawDocumentStore(session))
        raw_documents = session.query(RawDocument).order_by(RawDocument.id).all()
        clean_summary = CleanerAgent(session).process_pending(country="United States", disease="norovirus")
        feature_summary = FeatureBuilder(session).build(
            country="United States",
            disease="norovirus",
            admin1="National",
            horizons_days=(7,),
            outbreak_threshold=3,
        )
        events = session.query(EpidemicEvent).order_by(EpidemicEvent.iso_week).all()
        features = session.query(ModelFeature).order_by(ModelFeature.iso_week).all()

    assert len(raw_documents) == 6
    assert all(document.country_detected == "United States" for document in raw_documents)
    assert all(document.disease_detected == "norovirus" for document in raw_documents)
    assert clean_summary["events_created"] == 6
    assert [event.iso_week for event in events] == [f"2025-W0{index}" for index in range(1, 7)]
    assert [event.outbreaks_count for event in events] == [index + 1 for index in range(1, 7)]
    assert all(event.extraction_method == "structured_event_v1" for event in events)
    assert feature_summary["feature_rows"] == 5
    assert len(features) == 5
    assert features[0].feature_json["current_week_outbreaks"] == 2
    assert features[0].target_outbreak_label is True


def test_cdc_live_style_week_label_uses_fetch_season_reference() -> None:
    raw_document = SimpleNamespace(
        id=1,
        title="CDC NoroSTAT row",
        body_text="Illness onset week: 1-Aug.",
        fetched_at=datetime(2026, 7, 2, tzinfo=UTC),
        published_at=None,
        country_detected="United States",
        disease_detected="norovirus",
        metadata_json={
            "source_key": "cdc_norostat",
            "row_number": 1,
            "row_fields": {"week": "1-Aug"},
        },
    )

    event = extract_event_from_raw_document(
        raw_document,
        target_country="United States",
        target_disease="norovirus",
    )

    assert event is not None
    assert event.event_date.isoformat() == "2025-08-01"
    assert event.iso_week == "2025-W31"
    assert event.country == "United States"
    assert event.disease == "norovirus"
