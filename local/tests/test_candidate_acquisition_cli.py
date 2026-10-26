from __future__ import annotations

import json

import pytest

from live_source_search_orchestrator.cli import main


def test_cli_list_and_plan_candidate_acquisition(tmp_path, capsys):
    run_dir = _write_run_dir(tmp_path)

    assert main(["list-review-candidates", "--run-dir", str(run_dir)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert listed["selected_candidate"]["domain"] == "www.cdc.gov"

    assert (
        main(
            [
                "plan-candidate-acquisition",
                "--run-dir",
                str(run_dir),
                "--output-dir",
                str(tmp_path / "acquisition"),
            ]
        )
        == 0
    )
    planned = json.loads(capsys.readouterr().out)
    assert planned["status"] == "ok"
    assert planned["network_accessed"] is False
    assert planned["follow_links"] is False


def test_cli_run_candidate_acquisition_requires_explicit_flags(tmp_path):
    run_dir = _write_run_dir(tmp_path)

    with pytest.raises(SystemExit):
        main(
            [
                "run-candidate-acquisition",
                "--run-dir",
                str(run_dir),
                "--output-dir",
                str(tmp_path / "acquisition"),
                "--database-url",
                f"sqlite:///{tmp_path / 'db.sqlite'}",
            ]
        )


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
                        "refined_score": 0.7,
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
