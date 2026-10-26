from __future__ import annotations

import json
from pathlib import Path

from common.db import get_session, init_db
from source_discovery_agent.bridge_report import render_bridge_report_markdown, write_bridge_report
from source_discovery_agent.fixture_mapping import infer_fixture_for_parser_strategy
from source_discovery_agent.generic_bridge import DiscoveryParserBridge, DiscoveryParserBridgeInput


def _run_bridge(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'bridge-report.db'}"
    init_db(database_url).dispose()
    mapping = infer_fixture_for_parser_strategy(parser_strategy="csv_dataset", disease="influenza")
    with get_session(database_url) as session:
        return DiscoveryParserBridge(session).run(
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


def test_bridge_report_can_render_and_write_safe_outputs(tmp_path) -> None:
    result = _run_bridge(tmp_path)
    markdown = render_bridge_report_markdown([result])
    files = write_bridge_report([result], output_dir=str(tmp_path / "reports"))

    assert "Human Review Required" in markdown
    assert Path(files["report_json"]).exists()
    assert Path(files["report_markdown"]).exists()
    payload = json.loads(Path(files["report_json"]).read_text(encoding="utf-8"))
    rendered = json.dumps(payload, ensure_ascii=False).lower() + markdown.lower()
    assert "raw_text" not in rendered
    assert "metadata_json" not in rendered
    assert "feature_json" not in rendered
