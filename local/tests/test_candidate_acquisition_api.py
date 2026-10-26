from __future__ import annotations

import json


def test_candidate_acquisition_api_plan_and_run_are_safe(api_client, tmp_path, monkeypatch):
    run_dir = _write_run_dir(tmp_path)

    plan_response = api_client.post(
        "/live-source-search/acquisition/plan",
        json={"run_dir": str(run_dir), "output_dir": str(tmp_path / "acquisition")},
    )
    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["status"] == "ok"
    assert plan_payload["network_accessed"] is False

    class FakeResult:
        def to_dict(self) -> dict:
            return {
                "status": "ok",
                "run_id": "fake",
                "request_count": 1,
                "network_accessed": True,
                "follow_links": False,
                "files_downloaded": False,
                "full_body_saved": False,
                "source_registry_status": "pending_review",
                "raw_documents_saved": 1,
            }

    monkeypatch.setattr("apps.api.routers.live_source_search.run_candidate_acquisition", lambda *args, **kwargs: FakeResult())
    run_response = api_client.post(
        "/live-source-search/acquisition/run",
        json={
            "run_dir": str(run_dir),
            "output_dir": str(tmp_path / "acquisition"),
            "allow_network": True,
            "terms_reviewed": True,
            "manual_reviewed": True,
        },
    )
    assert run_response.status_code == 200
    rendered = json.dumps(run_response.json(), ensure_ascii=False).lower()
    assert "metadata_json" not in rendered
    assert "raw_text" not in rendered


def _write_run_dir(tmp_path):
    run_dir = tmp_path / "search_measles"
    run_dir.mkdir()
    (run_dir / "refined_review_queue.json").write_text(
        json.dumps(
            {
                "review_queue": [
                    {
                        "candidate_id": "a",
                        "title": "Cases and Outbreaks",
                        "domain": "www.cdc.gov",
                        "quality_gate_status": "pass",
                        "metadata_check_status": "data_source_candidate",
                        "data_source_likelihood": 0.68,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "metadata_check_results.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "url": "https://www.cdc.gov/measles/data-research/index.html",
                        "domain": "www.cdc.gov",
                        "title_summary": "Measles Cases and Outbreaks | CDC",
                        "status": "data_source_candidate",
                        "data_source_likelihood": 0.68,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "candidate_quality_gate.json").write_text('{"candidate_quality_gate":[]}', encoding="utf-8")
    (run_dir / "live_source_search_summary.json").write_text('{"country":"United States","disease":"measles"}', encoding="utf-8")
    return run_dir
