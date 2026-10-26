from __future__ import annotations

import json
from pathlib import Path

from scripts.source_discovery_bridge_smoke import run_source_discovery_bridge_smoke


def test_source_discovery_bridge_smoke_runs_all_strategies(tmp_path) -> None:
    summary = run_source_discovery_bridge_smoke(temp_db=True, output_dir=str(tmp_path / "reports"))

    assert summary["status"] == "ok"
    assert summary["network_accessed"] is False
    assert summary["source_discovery_candidates_found"] == 8
    assert summary["connector_specs_generated"] >= 1
    assert set(summary["parser_strategies_tested"]) == {
        "csv_dataset",
        "html_table",
        "json_api",
        "official_report_page",
    }
    assert summary["raw_documents_saved_by_strategy"]["csv_dataset"] == 4
    assert Path(summary["bridge_report"]["report_json"]).exists()
    rendered = json.dumps(summary, ensure_ascii=False).lower()
    assert "metadata_json" not in rendered
    assert "feature_json" not in rendered
