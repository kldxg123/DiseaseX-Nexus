from __future__ import annotations

import json

from live_source_search_orchestrator.event_quality_diagnostics import write_event_quality_diagnostic_report

from acquisition_quality_helpers import make_acquisition_db, make_run_dir


def test_event_quality_diagnostic_report_files_are_written(tmp_path):
    database_url = make_acquisition_db(tmp_path, weeks=1)
    run_dir = make_run_dir(tmp_path)

    report = write_event_quality_diagnostic_report(run_dir, database_url=database_url)

    assert report.raw_documents_count == 1
    assert report.epidemic_events_count == 1
    assert report.model_features_count == 0
    assert report.event_quality.quality_status == "insufficient_temporal_signal"
    for path in report.report_paths.values():
        assert path
    summary = json.loads((run_dir / "event_quality_diagnostics.json").read_text(encoding="utf-8"))
    assert summary["event_quality"]["quality_status"] == "insufficient_temporal_signal"
    assert "raw_text" not in json.dumps(summary).lower()
