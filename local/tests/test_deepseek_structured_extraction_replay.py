from __future__ import annotations

import json
from pathlib import Path

from common.db import get_session, init_db
from common.models import DiseaseCountryInformationRecord
from deepseek_web_search_agent.extraction_replay import (
    extract_safe_search_context_for_deepseek,
    load_external_search_live_run,
    run_structured_extraction_replay,
)


def make_safe_external_run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "external_search_live_validation"
    nested = run_dir / "run_deepseek_safe"
    nested.mkdir(parents=True)
    (run_dir / "external_search_live_summary.json").write_text(
        json.dumps(
            {
                "run_id": "run_deepseek_existing",
                "country": "United States",
                "disease": "influenza",
                "selected_provider": "tavily",
                "external_search_provider_called": True,
                "page_read_request_count": 0,
            }
        ),
        encoding="utf-8",
    )
    (nested / "search_results_summary.json").write_text(
        json.dumps(
            {
                "results": [
                    {
                        "query": "United States influenza weekly cases",
                        "rank": 1,
                        "title": "United States influenza surveillance report 2026-W10",
                        "url": "https://health.example.gov/influenza/weekly",
                        "domain": "health.example.gov",
                        "snippet": "Public health summary reports 12 cases in United States during 2026-W10.",
                        "source_type": "official_report_evidence",
                        "total_score": 0.82,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def _database_url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'deepseek-replay.sqlite').as_posix()}"


def test_load_and_extract_safe_context_from_existing_run(tmp_path) -> None:
    run_dir = make_safe_external_run_dir(tmp_path)

    loaded = load_external_search_live_run(run_dir)
    context = extract_safe_search_context_for_deepseek(run_dir)

    assert loaded["summary"]["selected_provider"] == "tavily"
    assert context["page_read_request_count"] == 0
    assert len(context["safe_candidates"]) == 1
    assert "snippet_summary" in context["safe_candidates"][0]
    assert "results" not in context["safe_candidates"][0]


def test_structured_extraction_replay_persists_without_search_rerun(tmp_path) -> None:
    run_dir = make_safe_external_run_dir(tmp_path)
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()

    with get_session(database_url) as session:
        replay = run_structured_extraction_replay(
            session=session,
            run_dir=run_dir,
            allow_deepseek_api=False,
            output_dir=str(tmp_path / "reports"),
        )
        records = session.query(DiseaseCountryInformationRecord).all()

    assert replay["status"] == "ok"
    assert replay["no_tavily_rerun"] is True
    assert replay["page_read_request_count"] == 0
    assert replay["deepseek_call_count"] == 0
    assert replay["fallback_extraction_used"] is True
    assert replay["records_persisted"] == 1
    assert records[0].metric_name == "cases"
    assert records[0].metric_value == 12

