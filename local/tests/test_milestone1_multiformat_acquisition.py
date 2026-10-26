from __future__ import annotations

import json

from scripts.influenza_extension_smoke import run_influenza_extension_smoke


def test_influenza_extension_smoke_covers_all_multiformat_strategies() -> None:
    summary = run_influenza_extension_smoke(temp_db=True)

    assert summary["status"] == "ok"
    assert summary["network_accessed"] is False
    assert set(summary["parser_strategies_used"]) == {
        "csv_dataset",
        "html_table",
        "json_api",
        "official_report_page",
    }
    assert summary["raw_documents_saved_by_strategy"]["csv_dataset"] == 4
    assert summary["raw_documents_saved_by_strategy"]["html_table"] == 2
    assert summary["raw_documents_saved_by_strategy"]["json_api"] == 12
    assert summary["raw_documents_saved_by_strategy"]["official_report_page"] == 1
    assert summary["epidemic_events_created"] >= 18
    assert summary["model_features_created"] > 0
    rendered = json.dumps(summary, ensure_ascii=False).lower()
    assert "metadata_json" not in rendered
    assert "feature_json" not in rendered
    assert "raw_text" not in rendered
