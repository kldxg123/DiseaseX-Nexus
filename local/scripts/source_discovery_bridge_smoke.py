from __future__ import annotations

import argparse
import json
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from common.db import get_session, init_db
from source_discovery_agent import SourceDiscoveryAgent
from source_discovery_agent.bridge_report import write_bridge_report
from source_discovery_agent.fixture_mapping import infer_fixture_for_parser_strategy, supported_fixture_strategies
from source_discovery_agent.generic_bridge import DiscoveryParserBridge, DiscoveryParserBridgeInput

COUNTRY = "United States"
DISEASE = "influenza"
DISCOVERY_FIXTURE = Path("tests/fixtures/source_discovery_influenza_search_results.json")


def run_source_discovery_bridge_smoke(
    *,
    database_url: str | None = None,
    temp_db: bool = False,
    output_dir: str = "reports/source_discovery",
) -> dict[str, Any]:
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    if temp_db or not database_url:
        temp_dir = tempfile.TemporaryDirectory(prefix="source-discovery-bridge-")
        database_url = f"sqlite:///{(Path(temp_dir.name) / 'source-discovery-bridge.db').as_posix()}"

    init_db(database_url).dispose()
    with get_session(database_url) as session:
        discovery_report = SourceDiscoveryAgent(session).discover_from_fixture(
            country=COUNTRY,
            disease=DISEASE,
            fixture_path=str(DISCOVERY_FIXTURE),
            min_source_score=0.35,
            generate_connector_specs=True,
        )
        bridge_results = []
        for strategy in supported_fixture_strategies(disease=DISEASE):
            mapping = infer_fixture_for_parser_strategy(parser_strategy=strategy, disease=DISEASE)
            fixture_content = Path(mapping["fixture_path"]).read_text(encoding="utf-8")
            bridge_results.append(
                DiscoveryParserBridge(session).run(
                    DiscoveryParserBridgeInput(
                        country=COUNTRY,
                        disease=DISEASE,
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
            )
        report_files = write_bridge_report(bridge_results, output_dir=output_dir)

    raw_by_strategy = OrderedDict((result.parser_strategy, result.raw_documents_saved) for result in bridge_results)
    events_by_strategy = OrderedDict((result.parser_strategy, result.epidemic_events_created) for result in bridge_results)
    features_by_strategy = OrderedDict((result.parser_strategy, result.model_features_created) for result in bridge_results)
    summary = {
        "status": "ok",
        "network_accessed": False,
        "source_discovery_candidates_found": discovery_report.candidates_found,
        "candidates_inserted_or_updated": discovery_report.candidates_inserted_or_updated,
        "connector_specs_generated": discovery_report.connector_specs_generated,
        "parser_strategies_tested": list(raw_by_strategy),
        "raw_documents_saved_by_strategy": dict(raw_by_strategy),
        "epidemic_events_created_by_strategy": dict(events_by_strategy),
        "model_features_created_by_strategy": dict(features_by_strategy),
        "bridge_report": {
            "report_json": report_files.get("report_json"),
            "report_markdown": report_files.get("report_markdown"),
        },
        "warnings": [warning for result in bridge_results for warning in result.warnings][:40],
        "human_review_required": True,
    }
    if temp_dir is not None:
        temp_dir.cleanup()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run source discovery to generic parser bridge smoke locally.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--temp-db", action="store_true")
    parser.add_argument("--output-dir", default="reports/source_discovery")
    args = parser.parse_args(argv)
    summary = run_source_discovery_bridge_smoke(
        database_url=args.database_url,
        temp_db=args.temp_db,
        output_dir=args.output_dir,
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
