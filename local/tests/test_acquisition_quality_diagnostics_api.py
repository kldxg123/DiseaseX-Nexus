from __future__ import annotations

import json

from acquisition_quality_helpers import make_acquisition_db, make_run_dir


def test_acquisition_quality_diagnostics_api_returns_safe_summary(api_client, tmp_path):
    database_url = make_acquisition_db(tmp_path, weeks=1)
    run_dir = make_run_dir(tmp_path)

    response = api_client.post(
        "/live-source-search/acquisition/diagnose-quality",
        json={"run_dir": str(run_dir), "database_url": database_url},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["model_features_count"] == 0
    assert payload["event_quality_status"] == "insufficient_temporal_signal"
    rendered = json.dumps(payload, ensure_ascii=False).lower()
    assert "database_url" not in rendered
    assert "raw_text" not in rendered


def test_acquisition_quality_report_api_reads_written_report(api_client, tmp_path):
    database_url = make_acquisition_db(tmp_path, weeks=1)
    run_dir = make_run_dir(tmp_path)
    api_client.post(
        "/live-source-search/acquisition/diagnose-quality",
        json={"run_dir": str(run_dir), "database_url": database_url},
    )

    response = api_client.get(
        f"/live-source-search/acquisition/quality-report/{run_dir.name}",
        params={"output_dir": str(run_dir.parent)},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["quality_report"]["model_features_count"] == 0
