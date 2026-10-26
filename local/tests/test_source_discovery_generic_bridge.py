from __future__ import annotations

from pathlib import Path

import pytest

from common.db import get_session, init_db
from common.models import EpidemicEvent, ModelFeature, RawDocument, SourceRegistry
from source_discovery_agent.fixture_mapping import infer_fixture_for_parser_strategy
from source_discovery_agent.generic_bridge import DiscoveryParserBridge, DiscoveryParserBridgeInput


@pytest.mark.parametrize(
    ("strategy", "expected_records"),
    [
        ("csv_dataset", 4),
        ("html_table", 2),
        ("json_api", 12),
        ("official_report_page", 1),
    ],
)
def test_discovery_parser_bridge_runs_local_fixture(tmp_path, strategy: str, expected_records: int) -> None:
    database_url = f"sqlite:///{tmp_path / f'bridge-{strategy}.db'}"
    init_db(database_url).dispose()
    mapping = infer_fixture_for_parser_strategy(parser_strategy=strategy, disease="influenza")
    fixture_content = Path(mapping["fixture_path"]).read_text(encoding="utf-8")

    with get_session(database_url) as session:
        result = DiscoveryParserBridge(session).run(
            DiscoveryParserBridgeInput(
                country="United States",
                disease="influenza",
                source_candidate_name=str(mapping["source_name"]),
                parser_strategy=strategy,
                fixture_content=fixture_content,
                fixture_url=str(mapping["fixture_url"]),
                source_name=str(mapping["source_name"]),
                source_type=str(mapping["source_type"]),
                field_mapping=mapping.get("field_mapping"),
                records_path=mapping.get("records_path"),
            )
        )
        sources = session.query(SourceRegistry).all()

    assert result.status == "ok"
    assert result.parsed_records == expected_records
    assert result.connector_documents == expected_records
    assert result.raw_documents_saved == expected_records
    assert result.epidemic_events_created >= 1
    assert result.model_features_created >= 0
    assert all(source.status == "pending_review" for source in sources)


def test_discovery_parser_bridge_csv_creates_raw_events_and_features(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'bridge-csv-full.db'}"
    init_db(database_url).dispose()
    mapping = infer_fixture_for_parser_strategy(parser_strategy="csv_dataset", disease="influenza")

    with get_session(database_url) as session:
        result = DiscoveryParserBridge(session).run(
            DiscoveryParserBridgeInput(
                country="United States",
                disease="influenza",
                source_candidate_name=str(mapping["source_name"]),
                parser_strategy="csv_dataset",
                fixture_content=Path(mapping["fixture_path"]).read_text(encoding="utf-8"),
                fixture_url=str(mapping["fixture_url"]),
                source_name=str(mapping["source_name"]),
                source_type=str(mapping["source_type"]),
            )
        )

        assert session.query(SourceRegistry).one().status == "pending_review"
        assert session.query(RawDocument).count() == 4
        assert session.query(EpidemicEvent).count() == 4
        assert session.query(ModelFeature).count() > 0
        assert result.summary["network_accessed"] is False
        assert result.summary["human_review_required"] is True
