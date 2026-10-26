from __future__ import annotations

import json

from live_source_search_orchestrator.cli import main

from acquisition_quality_helpers import make_acquisition_db, make_run_dir


def test_diagnose_acquisition_quality_cli_outputs_safe_summary(tmp_path, capsys):
    database_url = make_acquisition_db(tmp_path, weeks=1)
    run_dir = make_run_dir(tmp_path)

    assert main(["diagnose-acquisition-quality", "--run-dir", str(run_dir), "--database-url", database_url]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["raw_documents_count"] == 1
    assert payload["epidemic_events_count"] == 1
    assert payload["model_features_count"] == 0
    assert payload["event_quality_status"] == "insufficient_temporal_signal"
    rendered = json.dumps(payload).lower()
    assert "raw_text" not in rendered
    assert "metadata_json" not in rendered
