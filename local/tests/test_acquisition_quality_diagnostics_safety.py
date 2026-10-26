from __future__ import annotations

import json

from live_source_search_orchestrator.event_quality_diagnostics import write_event_quality_diagnostic_report

from acquisition_quality_helpers import make_acquisition_db, make_run_dir


def test_acquisition_quality_reports_do_not_expose_raw_or_sensitive_payloads(tmp_path):
    database_url = make_acquisition_db(tmp_path, weeks=1)
    run_dir = make_run_dir(tmp_path)

    report = write_event_quality_diagnostic_report(run_dir, database_url=database_url)
    rendered = json.dumps(report.to_dict(), ensure_ascii=False).lower()
    for forbidden in [
        "token",
        "password",
        "secret",
        "ssh_key_path",
        "html",
        "response_body",
        "raw_text",
        "metadata_json",
        "feature_json",
        "provenance_json",
        "lesson_json",
    ]:
        assert forbidden not in rendered
